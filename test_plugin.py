# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""Offline tests against lightweight interfaces matching the reviewed AstrBot API."""

import asyncio
import copy
import io
import json
import logging
import struct
import sys
import tempfile
import types
import unittest
import wave
from enum import Enum, auto
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError

from astrbot_plugin_qwen_audio_tts import tts_client as client


def decorator(*args, **kwargs):
    return lambda func: func


class Plain:
    def __init__(self, text):
        self.text = text


class Record:
    def __init__(self, file):
        self.file = file
        self.text = None

    @staticmethod
    def fromFileSystem(path):
        return Record(Path(path).resolve().as_uri())

    @staticmethod
    def fromURL(url):
        return Record(url)

    async def register_to_file_service(self):
        return "https://files.example/audio"


class At:
    pass


class Reply:
    pass


class GreedyStr(str):
    pass


class ResultContentType(Enum):
    LLM_RESULT = auto()
    GENERAL_RESULT = auto()
    STREAMING_RESULT = auto()
    STREAMING_FINISH = auto()


class Result:
    def __init__(self, chain, kind=ResultContentType.LLM_RESULT):
        self.chain = chain
        self.result_content_type = kind
        self.use_t2i_ = None

    def is_llm_result(self):
        return self.result_content_type == ResultContentType.LLM_RESULT


class Config(dict):
    def save_config(self):
        self.saved = True


class Star:
    def __init__(self, context):
        self.context = context


class Provider:
    def __init__(self, model="qwen-audio-3.1-tts-plus", key="test-key", voice=None):
        self.model = model
        self.provider_config = {
            "api_key": key,
            "dashscope_tts_voice": voice or model + "-test-voice",
        }

    def get_model(self):
        return self.model

    async def get_audio(self, text):
        return "original:" + text


class Event:
    def __init__(self, text="你好", kind=ResultContentType.LLM_RESULT):
        self.result = Result([Plain(text)], kind)
        self.extra = {}
        self.unified_msg_origin = "aiocqhttp:FriendMessage:123"
        self.admin = True
        self.llm = True
        self.sent = []

    def get_result(self):
        return self.result

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)

    def set_extra(self, key, value):
        self.extra[key] = value

    def get_platform_name(self):
        return "aiocqhttp"

    def is_private_chat(self):
        return True

    def is_admin(self):
        return self.admin

    def get_sender_id(self):
        return "123"

    def should_call_llm(self, value):
        self.llm = value

    def plain_result(self, text):
        return Result([Plain(text)], ResultContentType.GENERAL_RESULT)

    def chain_result(self, chain):
        return Result(chain, ResultContentType.GENERAL_RESULT)

    async def send(self, result):
        self.sent.append(result)


# Only this test process uses stubs; no AstrBot files or installed modules change.
MODULES = {}
for name in (
    "astrbot",
    "astrbot.api",
    "astrbot.api.event",
    "astrbot.api.message_components",
    "astrbot.api.star",
    "astrbot.core",
    "astrbot.core.message",
    "astrbot.core.message.message_event_result",
    "astrbot.core.star",
    "astrbot.core.star.filter",
    "astrbot.core.star.filter.command",
    "astrbot.core.utils",
    "astrbot.core.utils.astrbot_path",
    "astrbot.core.provider",
    "astrbot.core.provider.sources",
    "astrbot.core.provider.sources.dashscope_tts",
):
    MODULES[name] = types.ModuleType(name)
    MODULES[name].__path__ = []
MODULES["astrbot.api"].AstrBotConfig = Config
MODULES["astrbot.api"].logger = logging.getLogger("plugin-test")
MODULES["astrbot.api.event"].AstrMessageEvent = Event
MODULES["astrbot.api.event"].filter = types.SimpleNamespace(
    on_decorating_result=decorator,
    command=decorator,
    permission_type=decorator,
    llm_tool=decorator,
    PermissionType=types.SimpleNamespace(ADMIN="admin"),
)
for name, cls in {"At": At, "Plain": Plain, "Record": Record, "Reply": Reply}.items():
    setattr(MODULES["astrbot.api.message_components"], name, cls)
MODULES["astrbot.api.star"].Star = Star
MODULES["astrbot.api.star"].Context = object
MODULES["astrbot.api.star"].register = decorator
MODULES[
    "astrbot.core.message.message_event_result"
].ResultContentType = ResultContentType
MODULES["astrbot.core.star.filter.command"].GreedyStr = GreedyStr
MODULES["astrbot.core.utils.astrbot_path"].get_astrbot_temp_path = tempfile.gettempdir
MODULES[
    "astrbot.core.provider.sources.dashscope_tts"
].ProviderDashscopeTTSAPI = Provider
with patch.dict(sys.modules, MODULES):
    from astrbot_plugin_qwen_audio_tts import main


def defaults():
    schema = json.loads(
        Path(__file__).with_name("_conf_schema.json").read_text(encoding="utf-8")
    )
    return Config(
        {
            group: {
                key: copy.deepcopy(value["default"])
                for key, value in field["items"].items()
            }
            for group, field in schema.items()
        }
    )


def wav_bytes():
    data = io.BytesIO()
    with wave.open(data, "wb") as writer:
        writer.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        writer.writeframes(b"\x10\x00" * 2400)
    return data.getvalue()


class ClientTests(unittest.TestCase):
    def test_workspace_validation(self):
        self.assertEqual(
            client.workspace_base("ws-example"),
            "https://ws-example.cn-beijing.maas.aliyuncs.com/api/v1",
        )
        for value in (
            "http://ws-example.cn-beijing.maas.aliyuncs.com",
            "evil.example",
            "ws-example.cn-beijing.maas.aliyuncs.com:443",
            "ws-example.cn-beijing.maas.aliyuncs.com/evil",
        ):
            with self.subTest(value=value), self.assertRaises(client.TTSError):
                client.workspace_base(value)

    def test_documented_parameters_and_languages(self):
        settings = defaults()["audio"]
        for language in ("zh", "en", "ja"):
            with self.subTest(language=language):
                payload = client.build_payload(
                    "qwen-audio-3.1-tts-plus", "voice", "原文不翻译", settings, language
                )
                self.assertEqual(payload["input"]["language_hints"], [language])
                self.assertEqual(payload["input"]["text"], "原文不翻译")
                self.assertNotIn("seed", payload["input"])
                self.assertNotIn("bit_rate", payload["input"])
        settings.update(
            format="opus",
            seed=65535,
            bit_rate=64,
            instruction="温柔",
            hot_fix_json='{"replace":[{"今天":"金天"}]}',
        )
        inp = client.build_payload(
            "qwen-audio-3.1-tts-plus", "voice", "今天", settings
        )["input"]
        self.assertEqual(inp["seed"], 65535)
        self.assertEqual(inp["bit_rate"], 64)
        self.assertEqual(inp["hot_fix"], {"replace": [{"今天": "金天"}]})

    def test_invalid_parameters_are_rejected_before_network(self):
        for override in (
            {"volume": 101},
            {"rate": 0.4},
            {"pitch": 3},
            {"seed": 65536},
            {"sample_rate": 17000},
            {"format": "opus", "sample_rate": 44100},
            {"format": "pcm"},
            {"hot_fix_json": "[]"},
            {"hot_fix_json": "invalid"},
        ):
            with self.subTest(override=override), self.assertRaises(client.TTSError):
                client.build_payload(
                    "qwen-audio-3.1-tts-plus",
                    "voice",
                    "hello",
                    {**defaults()["audio"], **override},
                )
        with self.assertRaises(client.TTSError):
            client.build_payload(
                "qwen-audio-3.1-tts-plus", "qwen-audio-3.1-tts-flash-test", "hello", {}
            )

    def test_wav_placeholder_and_metadata_preserved(self):
        wav = bytearray(wav_bytes())
        # Insert a RIFF metadata chunk before data, and use streaming placeholders.
        wav[36:36] = b"JUNK\x04\x00\x00\x00test"
        struct.pack_into("<I", wav, 4, 0x7FFFFFFF)
        struct.pack_into("<I", wav, 52, 0x7FFFFFFF)
        fixed = client.normalize_audio(bytes(wav), "wav")
        self.assertIn(b"JUNK\x04\x00\x00\x00test", fixed)
        with wave.open(io.BytesIO(fixed), "rb") as reader:
            self.assertEqual(reader.getnframes(), 2400)

    def test_service_http_and_credential_isolation(self):
        payload = client.build_payload("qwen-audio-3.1-tts-plus", "voice", "hello", {})
        service = Mock()
        service.open.return_value.__enter__ = Mock(
            return_value=io.BytesIO(
                json.dumps(
                    {
                        "output": {
                            "audio": {
                                "url": "http://files.example/test.wav?secret=signed"
                            }
                        }
                    }
                ).encode()
            )
        )
        service.open.return_value.__exit__ = Mock(return_value=False)
        storage = Mock()
        storage.open.return_value.__enter__ = Mock(return_value=io.BytesIO(wav_bytes()))
        storage.open.return_value.__exit__ = Mock(return_value=False)
        with patch.object(client, "build_opener", side_effect=[service, storage]):
            data = client.synthesize_http(
                client.workspace_base("ws-example"), "private-key", payload
            )
        self.assertEqual(data, wav_bytes())
        first = service.open.call_args.args[0]
        second = storage.open.call_args.args[0]
        self.assertEqual(first.get_header("Authorization"), "Bearer private-key")
        self.assertIsNone(second.get_header("Authorization"))
        self.assertTrue(second.full_url.startswith("https://files.example/"))

    def test_service_errors_are_redacted_and_never_saved_as_audio(self):
        service = Mock()
        service.open.side_effect = HTTPError(
            "https://example",
            400,
            "bad",
            {},
            io.BytesIO(b'{"message":"private-key is bad"}'),
        )
        with (
            patch.object(client, "build_opener", return_value=service),
            self.assertRaises(client.TTSError) as caught,
        ):
            client.synthesize_http(
                client.workspace_base("ws-example"), "private-key", {}
            )
        self.assertNotIn("private-key", str(caught.exception))
        with self.assertRaises(client.TTSError):
            client.normalize_audio(b"<html>error</html>", "wav")


class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.modules_patch = patch.dict(sys.modules, MODULES)
        self.modules_patch.start()
        self.addCleanup(self.modules_patch.stop)
        self.path_patch = patch.object(
            main, "get_astrbot_temp_path", return_value=self.tmp.name
        )
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.config = defaults()
        self.config["connection"]["workspace"] = "ws-example"
        self.config["reply"]["probability"] = 100
        self.provider = Provider()
        self.context = types.SimpleNamespace(
            get_provider_by_id=lambda key: self.provider,
            get_using_tts_provider_async=AsyncMock(return_value=self.provider),
            get_config=lambda origin: {"provider_tts_settings": {"enable": False}},
        )
        self.plugin = main.QwenAudioVoicePlugin(self.context, self.config)
        self.addAsyncCleanup(self.plugin.terminate)
        self.audio = Path(self.tmp.name) / "example.wav"
        self.audio.write_bytes(wav_bytes())

    async def test_lifecycle_patch_existing_instance_and_restore(self):
        original = Provider.get_audio
        self.plugin._speak = AsyncMock(return_value=self.audio)
        await self.plugin.initialize()
        await self.plugin.initialize()
        self.assertEqual(await self.provider.get_audio("hi"), str(self.audio))
        self.assertEqual(
            await Provider("qwen3-tts-flash").get_audio("hi"), "original:hi"
        )
        await self.plugin.terminate()
        self.assertIs(Provider.get_audio, original)
        self.assertEqual(await self.provider.get_audio("hi"), "original:hi")

    async def test_unload_does_not_overwrite_another_patch(self):
        original = Provider.get_audio
        await self.plugin.initialize()
        replacement = AsyncMock(return_value="external")
        Provider.get_audio = replacement
        try:
            await self.plugin.terminate()
            self.assertIs(Provider.get_audio, replacement)
        finally:
            Provider.get_audio = original

    async def test_probability_zero_one_hundred_and_duplicate_guard(self):
        self.plugin._speak = AsyncMock(return_value=self.audio)
        event = Event()
        self.config["reply"]["probability"] = 0
        with patch.object(main.random, "random", return_value=0):
            await self.plugin.random_voice_reply(event)
        self.plugin._speak.assert_not_called()
        self.config["reply"]["probability"] = 100
        with patch.object(main.random, "random", return_value=0.999999):
            await self.plugin.random_voice_reply(event)
        self.assertIsInstance(event.result.chain[0], Record)
        await self.plugin.random_voice_reply(event)
        self.assertEqual(self.plugin._speak.await_count, 1)

    async def test_oversize_mixed_code_stream_and_non_llm_skipped(self):
        self.plugin._speak = AsyncMock(return_value=self.audio)
        events = [
            Event("字" * 201),
            Event("```python\nprint(1)\n```"),
            Event(kind=ResultContentType.STREAMING_FINISH),
            Event(kind=ResultContentType.STREAMING_RESULT),
            Event(kind=ResultContentType.GENERAL_RESULT),
        ]
        mixed = Event()
        mixed.result.chain.append(object())
        events.append(mixed)
        for event in events:
            with self.subTest(event=event):
                original = list(event.result.chain)
                await self.plugin.random_voice_reply(event)
                self.assertEqual(event.result.chain, original)
        self.plugin._speak.assert_not_called()

    async def test_native_tts_conflict_does_not_double_synthesize(self):
        self.plugin._speak = AsyncMock(return_value=self.audio)
        self.context.get_config = lambda origin: {
            "provider_tts_settings": {"enable": True}
        }
        event = Event()
        await self.plugin.random_voice_reply(event)
        self.plugin._speak.assert_not_called()
        self.assertIsInstance(event.result.chain[0], Plain)

    async def test_synthesis_failure_preserves_original_chain(self):
        self.plugin._speak = AsyncMock(side_effect=client.TTSError("test failure"))
        event = Event()
        original = list(event.result.chain)
        await self.plugin.random_voice_reply(event)
        self.assertEqual(event.result.chain, original)

    async def test_dual_output_preserves_unmodified_text(self):
        self.plugin._speak = AsyncMock(return_value=self.audio)
        self.config["reply"]["dual_output"] = True
        event = Event("**你好** [世界](https://example.com)")
        await self.plugin.random_voice_reply(event)
        self.assertIsInstance(event.result.chain[0], Record)
        self.assertEqual(
            event.result.chain[1].text, "**你好** [世界](https://example.com)"
        )
        self.assertEqual(self.plugin._speak.call_args.args[1], "你好 世界")

    async def test_cooldown_is_per_session_and_same_session_is_serialized(self):
        self.plugin._speak = AsyncMock(return_value=self.audio)
        first, second, other = Event(), Event(), Event()
        other.unified_msg_origin += "other"
        await asyncio.gather(
            self.plugin.random_voice_reply(first),
            self.plugin.random_voice_reply(second),
        )
        self.assertEqual(self.plugin._speak.await_count, 1)
        await self.plugin.random_voice_reply(other)
        self.assertEqual(self.plugin._speak.await_count, 2)

    async def test_scope_blacklist_wins_and_applies_to_command(self):
        event = Event()
        self.config["scope"]["allowed_sessions"] = [event.unified_msg_origin]
        self.config["scope"]["blocked_sessions"] = [event.unified_msg_origin]
        self.plugin._speak = AsyncMock(return_value=self.audio)
        await self.plugin.random_voice_reply(event)
        responses = [
            r async for r in self.plugin.speak_command(event, "en", GreedyStr("Hello"))
        ]
        self.assertIn("范围", responses[0].chain[0].text)
        self.plugin._speak.assert_not_called()

    async def test_command_reads_original_in_three_languages(self):
        self.plugin._speak = AsyncMock(return_value=self.audio)
        self.config["command"]["cooldown_seconds"] = 0
        for language, code, sentence in [
            ("英语", "en", "Hello world"),
            ("汉语", "zh", "你好，世界"),
            ("日语", "ja", "こんにちは"),
        ]:
            with self.subTest(language=language):
                event = Event()
                responses = [
                    r
                    async for r in self.plugin.speak_command(
                        event, language, GreedyStr(sentence)
                    )
                ]
                self.assertIsInstance(responses[0].chain[0], Record)
                self.assertFalse(event.llm)
                self.assertEqual(self.plugin._speak.call_args.args[1], sentence)
                self.assertEqual(self.plugin._speak.call_args.kwargs["language"], code)

    async def test_command_permissions_length_and_unknown_language(self):
        self.config["command"]["admin_only"] = True
        event = Event()
        event.admin = False
        responses = [
            r async for r in self.plugin.speak_command(event, "en", GreedyStr("hello"))
        ]
        self.assertIn("管理员", responses[0].chain[0].text)
        event.admin = True
        responses = [
            r
            async for r in self.plugin.speak_command(event, "fr", GreedyStr("bonjour"))
        ]
        self.assertIn("用法", responses[0].chain[0].text)
        responses = [
            r
            async for r in self.plugin.speak_command(event, "zh", GreedyStr("字" * 201))
        ]
        self.assertIn("超过最大字数", responses[0].chain[0].text)

    async def test_network_payload_per_provider_key_and_ssml(self):
        self.config["audio"]["enable_ssml"] = True
        with patch.object(main, "synthesize_http", return_value=wav_bytes()) as network:
            path = await self.plugin._speak(
                self.provider, "hello", "en", automatic=True
            )
        self.assertTrue(path.is_file())
        args = network.call_args.args
        self.assertEqual(args[1], "test-key")
        self.assertEqual(args[2]["input"]["language_hints"], ["en"])
        self.assertFalse(args[2]["input"]["enable_ssml"])

    async def test_admin_setting_validates_saves_and_status_omits_key(self):
        event = Event()
        responses = [r async for r in self.plugin.voice_settings(event, "概率", "35")]
        self.assertIn("已保存", responses[0].chain[0].text)
        self.assertEqual(self.config["reply"]["probability"], 35)
        self.assertTrue(self.config.saved)
        responses = [r async for r in self.plugin.voice_settings(event, "概率", "101")]
        self.assertIn("失败", responses[0].chain[0].text)
        status = [r async for r in self.plugin.voice_status(event)]
        self.assertNotIn("test-key", status[0].chain[0].text)

    async def test_non_admin_cannot_modify_global_settings(self):
        event = Event()
        event.admin = False
        responses = [r async for r in self.plugin.voice_settings(event, "概率", "35")]
        self.assertIn("只有管理员", responses[0].chain[0].text)
        self.assertEqual(self.config["reply"]["probability"], 100)
        self.assertFalse(hasattr(self.config, "saved"))

    async def test_network_concurrency_limit_and_waiting_jobs_on_unload(self):
        import threading

        count, maximum = 0, 0
        lock = threading.Lock()

        def network(*args):
            nonlocal count, maximum
            with lock:
                count += 1
                maximum = max(maximum, count)
            try:
                import time

                time.sleep(0.025)
                return wav_bytes()
            finally:
                with lock:
                    count -= 1

        with patch.object(main, "synthesize_http", side_effect=network):
            await asyncio.gather(
                *(self.plugin._speak(self.provider, "hello") for _ in range(5))
            )
        self.assertLessEqual(maximum, 2)
        await self.plugin._semaphore.acquire()
        await self.plugin._semaphore.acquire()
        with patch.object(main, "synthesize_http") as request:
            waiting = asyncio.create_task(self.plugin._speak(self.provider, "hello"))
            await asyncio.sleep(0)
            await self.plugin.terminate()
            self.plugin._semaphore.release()
            self.plugin._semaphore.release()
            with self.assertRaises(client.TTSError):
                await waiting
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
