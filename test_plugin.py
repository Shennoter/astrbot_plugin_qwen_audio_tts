# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""Offline tests against lightweight interfaces matching the reviewed AstrBot API."""

import asyncio
import base64
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
    "astrbot.api.web",
    "astrbot.core",
    "astrbot.core.message",
    "astrbot.core.message.message_event_result",
    "astrbot.core.star",
    "astrbot.core.star.filter",
    "astrbot.core.star.filter.command",
    "astrbot.core.utils",
    "astrbot.core.utils.astrbot_path",
):
    MODULES[name] = types.ModuleType(name)
    MODULES[name].__path__ = []
MODULES["astrbot.api"].AstrBotConfig = Config
MODULES["astrbot.api"].logger = logging.getLogger("plugin-test")
MODULES["astrbot.api"].web = MODULES["astrbot.api.web"]
MODULES["astrbot.api.event"].AstrMessageEvent = Event
MODULES["astrbot.api.event"].filter = types.SimpleNamespace(
    on_decorating_result=decorator,
    after_message_sent=decorator,
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
    "astrbot.core.utils.astrbot_path"
].get_astrbot_plugin_data_path = tempfile.gettempdir
with patch.dict(sys.modules, MODULES):
    from astrbot_plugin_qwen_audio_tts import main


def defaults():
    from .settings import SCHEMA as schema

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
            client.build_payload("  ", "voice", "hello", {})

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
        payload = client.build_payload("cosyvoice-v3.5-plus", "voice", "hello", {})
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
        self.path_patch = patch.multiple(
            main,
            get_astrbot_temp_path=Mock(return_value=self.tmp.name),
            get_astrbot_plugin_data_path=Mock(return_value=self.tmp.name),
        )
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.config = defaults()
        self.config["connection"]["workspace"] = "ws-example"
        self.config["connection"]["api_key"] = "test-key"
        self.config["connection"]["voice"] = "qwen-audio-3.1-tts-plus-test-voice"
        self.config["reply"]["probability"] = 100
        self.context = types.SimpleNamespace(
            get_provider_by_id=Mock(
                side_effect=AssertionError("Built-in provider lookup forbidden")
            ),
            get_using_tts_provider_async=AsyncMock(
                side_effect=AssertionError("Built-in TTS lookup forbidden")
            ),
            get_config=lambda origin: {"provider_tts_settings": {"enable": False}},
            register_web_api=Mock(),
        )
        self.plugin = main.QwenAudioVoicePlugin(self.context, self.config)
        self.request = types.SimpleNamespace(
            username="admin", json=AsyncMock(return_value={})
        )
        self.plugin._web = types.SimpleNamespace(
            request=self.request,
            json_response=lambda data: (data, 200),
            error_response=lambda message, status_code=400: (
                {"message": message},
                status_code,
            ),
        )
        self.addAsyncCleanup(self.plugin.terminate)
        self.audio = Path(self.tmp.name) / "example.wav"
        self.audio.write_bytes(wav_bytes())

    def enable_translation(self, target="英语", outputs=None):
        self.config["translation"].update(language=target, provider_id="translator")
        self.context.get_provider_by_id = Mock(return_value=object())
        self.context.llm_generate = AsyncMock(side_effect=[
            types.SimpleNamespace(completion_text=text) for text in (outputs or ["Hello world"])
        ])

    async def test_translation_auto_language_overrides_default_and_preserves_original(self):
        for target, code, translated in [("汉语", "zh", "你好"), ("日语", "ja", "こんにちは"), ("英语", "en", "Hello")]:
            with self.subTest(target=target):
                self.enable_translation(target, [translated])
                self.config["reply"]["dual_output"] = True
                self.config["command"]["cooldown_seconds"] = 0
                self.config["audio"]["language"] = "ja" if code != "ja" else "en"
                original = "  原文\n保持原样  "
                with patch.object(main, "synthesize_http", return_value=wav_bytes()) as network:
                    chain = await self.plugin._speech_chain(Event(), original, "auto")
                self.assertEqual(chain[1].text, original)
                payload = network.call_args.args[2]["input"]
                self.assertEqual(payload["text"], translated)
                self.assertEqual(payload["language_hints"], [code])
                self.assertEqual(self.config["translation"]["language"], target)
                self.assertEqual(self.context.llm_generate.await_count, 1)

    async def test_explicit_chat_language_overrides_translation_in_all_entrypoints(self):
        self.config["command"]["cooldown_seconds"] = 0
        self.config["reply"]["dual_output"] = True
        for entry in ("command", "parameters", "natural", "segments"):
            for language, code, target, translated in (
                ("中文", "zh", "汉语", "中文译文"),
                ("English", "en", "英语", "English translation"),
                ("日本語", "ja", "日语", "日本語の訳"),
            ):
                with self.subTest(entry=entry, language=language):
                    global_target = "英语" if code != "en" else "日语"
                    self.enable_translation(global_target, [translated])
                    self.config["audio"]["language"] = "ja" if code != "ja" else "en"
                    self.config["pronunciation"].update({
                        "dictionary_" + lang + "_json": json.dumps({translated: lang})
                        for lang in ("zh", "en", "ja")
                    })
                    before = copy.deepcopy(self.config)
                    event = Event()
                    original = "原文保持不变"
                    with patch.object(main, "synthesize_http", return_value=wav_bytes()) as network:
                        if entry == "command":
                            results = [r async for r in self.plugin.speak_command(event, language, GreedyStr(original))]
                            chain = results[0].chain
                        elif entry == "parameters":
                            params = json.dumps({"text": original, "hot_fix": {"replace": [{"词": "读音"}]}})
                            results = [r async for r in self.plugin.speech_with_parameters(event, language, GreedyStr(params))]
                            chain = results[0].chain
                        else:
                            options = {}
                            if entry == "segments":
                                options["delivery_json"] = json.dumps({"segments": [{"text": original, "pitch": 1.2}]})
                            result = await self.plugin.natural_speech(event, original, language=language, **options)
                            self.assertIn("语音已发送", result)
                            chain = event.sent[0].chain
                    payload = network.call_args.args[2]["input"]
                    self.assertEqual(payload["text"], translated)
                    self.assertEqual(payload["language_hints"], [code])
                    self.assertIn({translated: code}, payload["hot_fix"]["replace"])
                    if entry == "segments":
                        self.assertEqual(payload["pitch"], 1.2)
                    self.assertEqual(chain[1].text, original)
                    self.assertIn("翻译成" + target, self.context.llm_generate.call_args.kwargs["prompt"])
                    self.assertEqual(self.context.llm_generate.await_count, 1)
                    self.assertEqual(self.config, before)

    async def test_explicit_language_does_not_enable_disabled_translation(self):
        self.config["command"]["cooldown_seconds"] = 0
        self.context.llm_generate = AsyncMock()
        with patch.object(main, "synthesize_http", return_value=wav_bytes()) as network:
            await self.plugin._speech_chain(Event(), "原文", "en")
        self.assertEqual(network.call_args.args[2]["input"]["text"], "原文")
        self.assertEqual(network.call_args.args[2]["input"]["language_hints"], ["en"])
        self.context.llm_generate.assert_not_awaited()

    async def test_translation_selects_target_dictionary_and_preserves_display_text(self):
        self.enable_translation(outputs=["name"])
        self.config["reply"]["dual_output"] = True
        self.config["pronunciation"].update(dictionary_zh_json='{"name":"中文"}', dictionary_en_json='{"name":"English"}', dictionary_ja_json='{"name":"日本語"}')
        with patch.object(main, "synthesize_http", return_value=wav_bytes()) as network:
            chain = await self.plugin._speech_chain(Event(), "名字", "auto")
        self.assertEqual(chain[1].text, "名字")
        self.assertEqual(network.call_args.args[2]["input"]["hot_fix"]["replace"], [{"name": "English"}])

    async def test_translation_auto_reply_preserves_original_markdown(self):
        self.enable_translation()
        self.config["reply"]["dual_output"] = True
        event = Event("**你好** [世界](https://example.com)")
        with patch.object(main, "synthesize_http", return_value=wav_bytes()) as network:
            await self.plugin.random_voice_reply(event)
        self.assertEqual(event.result.chain[1].text, "**你好** [世界](https://example.com)")
        self.assertEqual(network.call_args.args[2]["input"]["text"], "Hello world")

    async def test_translation_failure_preserves_auto_text_and_never_synthesizes(self):
        for output in ("", " ", "x" * 201, None):
            self.enable_translation(outputs=[output])
            event = Event("你好")
            with patch.object(main, "synthesize_http") as network:
                await self.plugin.random_voice_reply(event)
            network.assert_not_called()
            self.assertEqual(event.result.chain[0].text, "你好")
        self.enable_translation()
        self.context.llm_generate.side_effect = TimeoutError("private details")
        with patch.object(main, "synthesize_http") as network:
            with self.assertRaisesRegex(client.TTSError, "翻译失败"):
                await self.plugin._speak("你好")
        network.assert_not_called()

    async def test_translation_automatic_provider_and_voice_only(self):
        self.enable_translation()
        self.config["translation"]["provider_id"] = ""
        self.context.get_all_providers = Mock(return_value=[types.SimpleNamespace(meta=lambda: types.SimpleNamespace(id="first-chat"))])
        with patch.object(main, "synthesize_http", return_value=wav_bytes()):
            chain = await self.plugin._speech_chain(Event(), "你好", "auto")
        self.assertEqual(len(chain), 1)
        self.assertEqual(self.context.llm_generate.call_args.kwargs["chat_provider_id"], "first-chat")

    async def test_translation_missing_provider_fails_before_tts(self):
        self.enable_translation()
        self.context.get_provider_by_id.return_value = None
        with patch.object(main, "synthesize_http") as network:
            with self.assertRaisesRegex(client.TTSError, "聊天模型"):
                await self.plugin._speak("你好")
        network.assert_not_called()

    async def test_translation_segments_are_translated_once_and_keep_controls(self):
        self.enable_translation(outputs=["Hello, ", "go!"])
        plan = {"segments": [{"text": "你好，", "pause_ms": 500, "pitch": 0.9}, {"text": "出发！", "pitch": 1.2}]}
        with patch.object(main, "synthesize_http", return_value=wav_bytes()) as network:
            await self.plugin._speak("你好，出发！", delivery_plan=plan)
        self.assertEqual(self.context.llm_generate.await_count, 2)
        inputs = [c.args[2]["input"] for c in network.call_args_list]
        self.assertEqual([p["text"] for p in inputs], ["Hello,", "go!"])
        self.assertEqual([p["pitch"] for p in inputs], [0.9, 1.2])
        self.assertEqual(plan["segments"][0]["text"], "你好，")

    async def test_works_without_any_builtin_tts_provider(self):
        with patch.object(main, "synthesize_http", return_value=wav_bytes()) as network:
            responses = [
                r
                async for r in self.plugin.speak_command(
                    Event(), "zh", GreedyStr("独立语音")
                )
            ]
        self.assertIsInstance(responses[0].chain[0], Record)
        self.context.get_provider_by_id.assert_not_called()
        self.context.get_using_tts_provider_async.assert_not_called()
        self.assertEqual(network.call_args.args[1], "test-key")
        self.assertEqual(
            network.call_args.args[2]["model"], self.config["connection"]["model"]
        )
        self.assertEqual(
            network.call_args.args[2]["input"]["voice"],
            self.config["connection"]["voice"],
        )

    async def test_shutdown_stops_new_synthesis(self):
        await self.plugin.terminate()
        with (
            patch.object(main, "synthesize_http") as network,
            self.assertRaises(client.TTSError),
        ):
            await self.plugin._speak("hello")
        network.assert_not_called()

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
        self.assertEqual(self.plugin._speak.call_args.args[0], "你好 世界")

    async def test_text_first_sends_original_while_translation_is_pending(self):
        self.config["reply"].update(text_first=True, dual_output=False)
        self.enable_translation()
        started, release = asyncio.Event(), asyncio.Event()

        async def translate(**kwargs):
            started.set()
            await release.wait()
            return types.SimpleNamespace(completion_text="Hello world")

        self.context.llm_generate.side_effect = translate
        event = Event("**你好** [世界](https://example.com)")
        original = list(event.result.chain)
        event.result.chain.extend([At(), Reply()])
        with patch.object(main, "synthesize_http", return_value=wav_bytes()) as network:
            await asyncio.wait_for(self.plugin.random_voice_reply(event), 1)
            tasks = tuple(self.plugin._background_tasks)
            await asyncio.wait_for(started.wait(), 1)
            network.assert_not_called()
            self.assertIs(event.result.chain[0], original[0])
            self.assertEqual(event.result.chain[0].text, "**你好** [世界](https://example.com)")
            await event.send(event.result)
            await self.plugin.cleanup_sent_audio(event)
            self.assertEqual(len(event.sent), 1)
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 2)
        self.assertEqual(network.call_args.args[2]["input"]["text"], "Hello world")
        self.assertEqual(len(event.sent), 2)
        self.assertEqual(len(event.sent[1].chain), 1)
        self.assertIsInstance(event.sent[1].chain[0], Record)
        self.assertFalse(self.plugin._temporary_files)
        self.assertFalse(self.plugin._auto_busy)

    async def test_text_first_fast_audio_waits_for_text_and_cleans_only_after_audio_send(self):
        self.config["reply"]["text_first"] = True
        path = self.plugin._store_audio(wav_bytes(), "wav", self.config["connection"])
        self.plugin._speak = AsyncMock(return_value=path)
        prepared = asyncio.Event()
        record = self.plugin._record

        async def prepare(*args, **kwargs):
            result = await record(*args, **kwargs)
            prepared.set()
            return result

        self.plugin._record = prepare
        event = Event()
        await self.plugin.random_voice_reply(event)
        tasks = tuple(self.plugin._background_tasks)
        await asyncio.wait_for(prepared.wait(), 1)
        self.assertFalse(event.sent)
        self.assertTrue(path.exists())
        await self.plugin.random_voice_reply(event)
        self.plugin._speak.assert_awaited_once()
        await event.send(event.result)
        await self.plugin.cleanup_sent_audio(event)
        self.assertTrue(path.exists())
        await asyncio.wait_for(asyncio.gather(*tasks), 1)
        self.assertEqual(len(event.sent), 2)
        self.assertFalse(path.exists())

    async def test_text_first_busy_and_cooldown_do_not_hold_other_text(self):
        self.config["reply"]["text_first"] = True
        self.plugin._speak = AsyncMock(return_value=self.audio)
        first, second, other = Event(), Event(), Event()
        other.unified_msg_origin += "other"
        await self.plugin.random_voice_reply(first)
        await self.plugin.random_voice_reply(second)
        await self.plugin.random_voice_reply(other)
        self.assertIsInstance(second.result.chain[0], Plain)
        self.assertEqual(len(self.plugin._background_tasks), 2)
        tasks = tuple(self.plugin._background_tasks)
        for event in (first, other):
            await self.plugin.cleanup_sent_audio(event)
        await asyncio.wait_for(asyncio.gather(*tasks), 1)
        self.assertEqual(self.plugin._speak.await_count, 2)
        after = Event()
        await self.plugin.random_voice_reply(after)
        self.assertIsInstance(after.result.chain[0], Plain)
        self.assertFalse(self.plugin._background_tasks)

    async def test_text_first_failures_preserve_text_and_release_session(self):
        self.config["reply"]["text_first"] = True
        for failure in ("translation", "synthesis", "record", "send"):
            with self.subTest(failure=failure):
                self.plugin._last_auto.clear()
                self.plugin._speak = AsyncMock(return_value=self.audio)
                event = Event("原文")
                if failure in ("translation", "synthesis"):
                    self.plugin._speak.side_effect = client.TTSError(failure)
                elif failure == "send":
                    event.send = AsyncMock(side_effect=RuntimeError("adapter"))
                with patch.object(self.plugin, "_record", wraps=self.plugin._record) as record:
                    if failure == "record":
                        record.side_effect = RuntimeError("file service")
                    await self.plugin.random_voice_reply(event)
                    tasks = tuple(self.plugin._background_tasks)
                    await self.plugin.cleanup_sent_audio(event)
                    await asyncio.wait_for(asyncio.gather(*tasks), 1)
                self.assertEqual(event.result.chain[0].text, "原文")
                self.assertFalse(self.plugin._auto_busy)
                self.assertFalse(self.plugin._last_auto)
                self.assertIsNone(event.get_extra("qwen_audio_text_sent"))

    async def test_text_first_uses_snapshot_despite_later_settings_changes(self):
        self.config["reply"]["text_first"] = True
        self.plugin._speak = AsyncMock(return_value=self.audio)
        event = Event()
        expected = main.snapshot(self.config)
        await self.plugin.random_voice_reply(event)
        tasks = tuple(self.plugin._background_tasks)
        self.config["translation"]["language"] = "日语"
        self.config["connection"]["use_file_service"] = True
        with patch.object(Record, "register_to_file_service", new_callable=AsyncMock) as upload:
            await self.plugin.cleanup_sent_audio(event)
            await asyncio.wait_for(asyncio.gather(*tasks), 1)
        self.assertEqual(self.plugin._speak.call_args.kwargs["config_snapshot"], expected)
        upload.assert_not_awaited()

    async def test_text_first_shutdown_cancels_pending_work_and_cleans_audio(self):
        self.config["reply"]["text_first"] = True
        path = self.plugin._store_audio(wav_bytes(), "wav", self.config["connection"])
        self.plugin._speak = AsyncMock(return_value=path)
        prepared = asyncio.Event()
        record = self.plugin._record

        async def prepare(*args, **kwargs):
            result = await record(*args, **kwargs)
            prepared.set()
            return result

        self.plugin._record = prepare
        event = Event()
        await self.plugin.random_voice_reply(event)
        tasks = tuple(self.plugin._background_tasks)
        await asyncio.wait_for(prepared.wait(), 1)
        await self.plugin.terminate()
        self.assertTrue(all(task.cancelled() for task in tasks))
        self.assertFalse(event.sent)
        self.assertFalse(path.exists())
        self.assertFalse(self.plugin._auto_busy)

    async def test_text_first_respects_existing_eligibility(self):
        self.config["reply"]["text_first"] = True
        self.plugin._speak = AsyncMock(return_value=self.audio)
        for event in (Event("字" * 201), Event("```code```"), Event(kind=ResultContentType.STREAMING_RESULT)):
            await self.plugin.random_voice_reply(event)
        self.config["reply"]["probability"] = 0
        await self.plugin.random_voice_reply(Event())
        self.assertFalse(self.plugin._background_tasks)
        self.plugin._speak.assert_not_awaited()

    async def test_text_first_file_service_keeps_audio_until_delayed_cleanup(self):
        self.config["reply"]["text_first"] = True
        self.config["connection"]["use_file_service"] = True
        path = self.plugin._store_audio(wav_bytes(), "wav", self.config["connection"])
        self.plugin._speak = AsyncMock(return_value=path)
        event = Event()
        await self.plugin.random_voice_reply(event)
        tasks = tuple(self.plugin._background_tasks)
        await self.plugin.cleanup_sent_audio(event)
        await asyncio.wait_for(asyncio.gather(*tasks), 1)
        self.assertEqual(event.sent[0].chain[0].file, "https://files.example/audio")
        self.assertTrue(path.exists())
        expiry = self.plugin._temporary_tasks[path]
        delayed = [t for t in self.plugin._cleanup_tasks if t is not expiry]
        self.assertEqual(len(delayed), 1)
        await self.plugin.terminate()
        self.assertFalse(path.exists())

    async def test_text_first_unload_cancels_inflight_translation(self):
        self.config["reply"]["text_first"] = True
        self.enable_translation()
        started, release = asyncio.Event(), asyncio.Event()

        async def translate(**kwargs):
            started.set()
            await release.wait()

        self.context.llm_generate.side_effect = translate
        event = Event("原文")
        with patch.object(main, "synthesize_http") as network:
            await self.plugin.random_voice_reply(event)
            await asyncio.wait_for(started.wait(), 1)
            await self.plugin.terminate()
        network.assert_not_called()
        self.assertFalse(self.plugin._background_tasks)
        self.assertFalse(self.plugin._auto_busy)
        self.assertFalse(event.sent)
        self.assertEqual(event.result.chain[0].text, "原文")

    async def test_text_first_missing_text_send_hook_discards_audio(self):
        self.config["reply"]["text_first"] = True
        path = self.plugin._store_audio(wav_bytes(), "wav", self.config["connection"])
        self.plugin._speak = AsyncMock(return_value=path)
        event = Event()
        await self.plugin.random_voice_reply(event)
        tasks = tuple(self.plugin._background_tasks)

        async def expire(awaitable, timeout):
            self.assertEqual(timeout, 300)
            awaitable.close()
            raise TimeoutError()

        with patch.object(main.asyncio, "wait_for", side_effect=expire):
            await asyncio.gather(*tasks)
        self.assertFalse(event.sent)
        self.assertFalse(path.exists())
        self.assertFalse(self.plugin._auto_busy)

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
                self.assertEqual(self.plugin._speak.call_args.args[0], sentence)
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

    async def test_network_payload_plugin_key_and_ssml(self):
        self.config["audio"]["enable_ssml"] = True
        with patch.object(main, "synthesize_http", return_value=wav_bytes()) as network:
            path = await self.plugin._speak("hello", "en", automatic=True)
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
        status = await self.plugin.natural_voice_control(event, "status")
        self.assertNotIn("test-key", status)

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
            await asyncio.gather(*(self.plugin._speak("hello") for _ in range(5)))
        self.assertLessEqual(maximum, 2)
        await self.plugin._semaphore.acquire()
        await self.plugin._semaphore.acquire()
        with patch.object(main, "synthesize_http") as request:
            waiting = asyncio.create_task(self.plugin._speak("hello"))
            await asyncio.sleep(0)
            await self.plugin.terminate()
            self.plugin._semaphore.release()
            self.plugin._semaphore.release()
            with self.assertRaises(client.TTSError):
                await waiting
            request.assert_not_called()

    async def test_sid_is_only_session_identifier_command(self):
        self.assertFalse(hasattr(self.plugin, "voice_status"))
        schema = json.loads(
            Path(__file__).with_name("_conf_schema.json").read_text(encoding="utf-8")
        )
        self.assertIn("/sid", schema["scope"]["items"]["allowed_sessions"]["hint"])
        event = Event()
        self.config["scope"]["allowed_sessions"] = [event.unified_msg_origin]
        self.assertTrue(self.plugin._scope_allowed(event))
        self.config["scope"]["allowed_sessions"] = [event.get_sender_id()]
        self.assertFalse(self.plugin._scope_allowed(event))

    async def test_service_defaults_do_not_include_secret_or_builtin_options(self):
        schema = json.loads(
            Path(__file__).with_name("_conf_schema.json").read_text(encoding="utf-8")
        )
        fields = schema["connection"]["items"]
        self.assertTrue(fields["api_key"]["secret"])
        self.assertEqual(fields["api_key"]["default"], "")
        self.assertNotIn("provider_id", fields)
        self.assertNotIn("patch_builtin", fields)

    async def test_missing_credentials_fail_before_network(self):
        for key in ("api_key", "voice", "workspace"):
            saved = self.config["connection"][key]
            self.config["connection"][key] = ""
            with (
                self.subTest(key=key),
                patch.object(main, "synthesize_http") as network,
                self.assertRaises(client.TTSError),
            ):
                await self.plugin._speak("hello")
            network.assert_not_called()
            self.config["connection"][key] = saved

    async def test_preview_uses_current_settings_without_random_or_manual_command(self):
        self.config["reply"]["probability"] = 0
        self.config["command"]["enabled"] = False
        self.config["preview"].update(text="Hello preview", language="en")
        self.config["audio"]["rate"] = 0.75
        with patch.object(main, "synthesize_http", return_value=wav_bytes()) as network:
            response, code = await self.plugin.preview_generate()
        self.assertEqual(code, 200)
        self.assertEqual(base64.b64decode(response["audio_base64"]), wav_bytes())
        self.assertEqual(response["mime_type"], "audio/wav")
        self.assertFalse(hasattr(self.plugin, "preview_command"))
        payload = network.call_args.args[2]
        self.assertEqual(payload["input"]["text"], "Hello preview")
        self.assertEqual(payload["input"]["language_hints"], ["en"])
        self.assertEqual(payload["input"]["rate"], 0.75)

    async def test_preview_permissions_scope_disabled_and_length_limits(self):
        self.request.username = None
        self.assertEqual((await self.plugin.preview_generate())[1], 401)
        self.assertEqual((await self.plugin.preview_settings())[1], 401)
        self.request.username = "admin"
        self.config["preview"]["enabled"] = False
        self.assertNotEqual((await self.plugin.preview_generate())[1], 403)
        self.config["preview"]["enabled"] = True
        self.config["preview"]["text"] = "字" * 201
        response, code = await self.plugin.preview_generate()
        self.assertEqual(code, 400)
        self.assertIn("最大字数", response["message"])
        self.assertFalse(self.plugin._preview_busy)

    async def test_natural_preview_delivery_success_and_failure(self):
        self.plugin._speak = AsyncMock(return_value=self.audio)
        self.config["preview"]["cooldown_seconds"] = 0
        event = Event()
        self.assertIn("已发送", await self.plugin.natural_preview(event))
        self.assertIsInstance(event.sent[0].chain[0], Record)
        event.send = AsyncMock(side_effect=OSError("delivery failed"))
        self.assertIn("发送失败", await self.plugin.natural_preview(event))

    async def test_preview_uses_dictionary_and_does_not_expose_api_key(self):
        self.config["preview"]["text"] = "名字"
        self.config["audio"]["language"] = "zh"
        self.config["pronunciation"]["dictionary_zh_json"] = '{"名字":"读音"}'
        with patch.object(main, "synthesize_http", return_value=wav_bytes()) as network:
            response, code = await self.plugin.preview_generate()
        self.assertEqual(
            network.call_args.args[2]["input"]["hot_fix"]["replace"], [{"名字": "读音"}]
        )
        self.assertEqual(code, 200)
        details, _ = await self.plugin.preview_settings()
        for value in (response, details):
            self.assertNotIn("test-key", json.dumps(value))
            self.assertNotIn("api_key", value)

    async def test_web_preview_request_is_temporary_and_validated(self):
        before = copy.deepcopy(self.config)
        self.request.json.return_value = {"text": "Hello", "language": "en"}
        with patch.object(main, "synthesize_http", return_value=wav_bytes()) as network:
            self.assertEqual((await self.plugin.preview_generate())[1], 200)
        self.assertEqual(network.call_args.args[2]["input"]["text"], "Hello")
        self.assertEqual(network.call_args.args[2]["input"]["language_hints"], ["en"])
        self.assertEqual(self.config, before)
        self.config["preview"]["cooldown_seconds"] = 0
        for body in (
            [],
            {"text": 3},
            {"text": " "},
            {"language": "xx"},
            {"language": []},
            {"api_key": "injection"},
        ):
            self.request.json.return_value = body
            self.assertEqual((await self.plugin.preview_generate())[1], 400, body)

    async def test_web_preview_busy_cooldown_and_error_redaction(self):
        self.plugin._preview_busy = True
        self.assertEqual((await self.plugin.preview_generate())[1], 429)
        self.plugin._preview_busy = False
        self.plugin._speak = AsyncMock(side_effect=client.TTSError("test-key failed"))
        response, code = await self.plugin.preview_generate()
        self.assertEqual(code, 400)
        self.assertNotIn("test-key", response["message"])
        self.assertFalse(self.plugin._preview_busy)
        self.assertEqual((await self.plugin.preview_generate())[1], 400)
        self.config["preview"]["cooldown_seconds"] = 0
        self.plugin._speak.side_effect = OSError("test-key")
        response, code = await self.plugin.preview_generate()
        self.assertEqual(code, 502)
        self.assertNotIn("test-key", response["message"])
        self.assertFalse(self.plugin._preview_busy)

    async def test_preview_rechecks_busy_after_awaiting_request_body(self):
        body_started = asyncio.Event()
        release_body = asyncio.Event()

        async def body(**kwargs):
            body_started.set()
            await release_body.wait()
            return {"text": "hello", "language": "en"}

        self.request.json.side_effect = body
        self.config["preview"]["cooldown_seconds"] = 0
        self.plugin._speak = AsyncMock(return_value=self.audio)
        pending = asyncio.create_task(self.plugin.preview_generate())
        await body_started.wait()
        self.plugin._preview_busy = True
        release_body.set()
        self.assertEqual((await pending)[1], 429)
        self.plugin._speak.assert_not_called()

    async def test_web_api_registration_and_older_astrbot_fallback(self):
        await self.plugin.initialize()
        routes = self.context.register_web_api.call_args_list
        self.assertEqual(len(routes), 7)
        self.assertEqual(
            routes[0].args[0], "/astrbot_plugin_qwen_audio_tts/preview/settings"
        )
        self.assertEqual(routes[1].args[2], ["POST"])
        # Missing public Web API leaves normal voice features usable.
        with patch.dict(
            sys.modules,
            {"astrbot.api": types.ModuleType("astrbot.api"), "astrbot.api.web": None},
        ):
            await self.plugin.initialize()
        self.assertEqual(self.context.register_web_api.call_count, 7)


if __name__ == "__main__":
    unittest.main()
