# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""Offline regressions for language routing at the actual synthesis boundary."""

import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

from . import test_plugin as h
from . import settings
from .language_models import detect_language
from .tts_client import TTSError


class LanguageModelTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = h.PluginTests.asyncSetUp
    enable_translation = h.PluginTests.enable_translation

    def enable_routes(self):
        self.config["language_models"]["enabled"] = True
        for code in ("zh", "en", "ja"):
            self.config["language_models"].update({
                f"{code}_model": f"model-{code}", f"{code}_voice": f"voice-{code}",
            })

    def assert_pair(self, payload, code):
        self.assertEqual(payload["model"], f"model-{code}")
        self.assertEqual(payload["input"]["voice"], f"voice-{code}")
        self.assertEqual(payload["input"]["language_hints"], [code])

    async def test_explicit_default_and_detected_language_select_complete_pair(self):
        self.enable_routes()
        for code, text in (("zh", "你好 Hello"), ("en", "Hello world"), ("ja", "今日はこんにちは")):
            for source in ("explicit", "default", "detect"):
                with self.subTest(code=code, source=source):
                    self.config["audio"]["language"] = code if source == "default" else "auto"
                    before = copy.deepcopy(self.config)
                    with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
                        await self.plugin._speak(text, language=code if source == "explicit" else "auto")
                    self.assert_pair(network.call_args.args[2], code)
                    self.assertEqual(self.config, before)

    async def test_disabled_empty_and_unknown_routes_use_default_pair(self):
        self.enable_routes()
        for case in ("disabled", "empty", "unknown"):
            with self.subTest(case=case):
                self.config["language_models"]["enabled"] = case != "disabled"
                self.config["language_models"].update(en_model="", en_voice="")
                with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
                    await self.plugin._speak("123 !!!" if case == "unknown" else "Hello")
                payload = network.call_args.args[2]
                self.assertEqual(payload["model"], self.config["connection"]["model"])
                self.assertEqual(payload["input"]["voice"], self.config["connection"]["voice"])
                if case != "empty":
                    self.assertNotIn("language_hints", payload["input"])

    async def test_translation_and_explicit_chat_override_choose_final_language(self):
        self.enable_routes()
        self.config["audio"]["language"] = "zh"
        self.config["command"]["cooldown_seconds"] = 0
        for language, expected, translated in (("auto", "ja", "こんにちは"), ("en", "en", "Hello")):
            self.enable_translation("日语", [translated])
            with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
                await self.plugin._speech_chain(h.Event(), "中文原文", language)
            self.assert_pair(network.call_args.args[2], expected)
            self.assertEqual(network.call_args.args[2]["input"]["text"], translated)
            self.assertEqual(self.config["translation"]["language"], "日语")

    async def test_web_preview_uses_draft_and_reports_selected_model(self):
        draft = settings.snapshot(self.config)
        draft["language_models"].update(enabled=True, ja_model="preview-model", ja_voice="preview-voice")
        self.request.json.return_value = {"text": "こんにちは", "language": "auto", "config": draft}
        before = copy.deepcopy(self.config)
        with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
            response, status = await self.plugin.preview_generate()
        self.assertEqual(status, 200, response)
        self.assertEqual(network.call_args.args[2]["model"], "preview-model")
        self.assertEqual(response["model"], "preview-model")
        self.assertEqual(response["models"], ["preview-model"])
        self.assertEqual(self.config, before)

    async def test_auto_reply_uses_detected_language(self):
        self.enable_routes()
        event = h.Event("Hello world")
        with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
            await self.plugin.random_voice_reply(event)
        self.assert_pair(network.call_args.args[2], "en")

    async def test_segments_route_and_validate_dictionary_expansion_before_requests(self):
        self.enable_routes()
        self.config["connection"]["voice"] = ""
        plan = {"segments": [{"text": "你好"}, {"text": "Hello"}, {"text": "こんにちは"}]}
        self.config["pronunciation"]["dictionary_en_json"] = json.dumps({"Hello": "x" * 220})
        with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
            with self.assertRaisesRegex(TTSError, "字数上限"):
                await self.plugin._speak("你好Helloこんにちは", delivery_plan=plan)
            network.assert_not_called()
            self.config["pronunciation"]["dictionary_en_json"] = "{}"
            await self.plugin._speak("你好Helloこんにちは", delivery_plan=plan)
        for call, code in zip(network.call_args_list, ("zh", "en", "ja")):
            self.assert_pair(call.args[2], code)

    async def test_partial_pair_fails_before_any_segment_is_sent(self):
        self.enable_routes()
        self.config["language_models"]["en_voice"] = ""
        with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
            with self.assertRaisesRegex(TTSError, "一起填写"):
                await self.plugin._speak("你好Hello", delivery_plan={"segments": [{"text": "你好"}, {"text": "Hello"}]})
        network.assert_not_called()

    async def test_parallel_languages_do_not_leak_connection_or_hints(self):
        self.enable_routes()
        before = copy.deepcopy(self.config)
        with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
            await asyncio.gather(self.plugin._speak("Hello", language="en"), self.plugin._speak("你好", language="zh"))
        payloads = {call.args[2]["model"]: call.args[2] for call in network.call_args_list}
        for code in ("en", "zh"):
            self.assert_pair(payloads[f"model-{code}"], code)
        self.assertEqual(self.config, before)

    async def test_dynamic_mode_accepts_language_voice_without_default_voice(self):
        self.enable_routes()
        self.config["connection"]["voice"] = ""
        self.config["delivery"]["mode"] = "动态参数"
        with (
            patch.object(h.main, "plan_dynamic", new=AsyncMock(return_value=("", None))) as planner,
            patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network,
        ):
            await self.plugin._speak("Hello")
        planner.assert_awaited_once()
        self.assert_pair(network.call_args.args[2], "en")

    async def test_settings_profiles_and_legacy_defaults(self):
        self.enable_routes()
        candidate = settings.validate_draft(self.config, {})
        saved = settings.save_profile(self.config, candidate, "", "分语言模型")
        profile = next(p for p in settings.profile_entries(saved) if p["name"] == "分语言模型")
        self.assertEqual(profile["config"]["language_models"], self.config["language_models"])
        self.assertFalse(settings.snapshot({})["language_models"]["enabled"])
        for patch_value in ({"en_model": "only-model", "en_voice": ""}, {"ja_voice": "bad voice"}, {"enabled": "yes"}):
            with self.subTest(patch=patch_value), self.assertRaises(TTSError):
                settings.validate_draft(self.config, {"language_models": patch_value})

    def test_detection_is_conservative_with_ssml_and_ambiguous_scripts(self):
        for text, expected in (("<speak>你好</speak>", "zh"), ("日本語", "zh"), ("ｺﾝﾆﾁﾊ", "ja"), ("Привет TTS", "auto"), ("123 🙂", "auto"), ("안녕하세요", "auto")):
            self.assertEqual(detect_language(text), expected)


class JapaneseTranslationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = h.PluginTests.asyncSetUp
    enable_translation = h.PluginTests.enable_translation

    async def test_chinese_translation_is_corrected_before_automatic_speech(self):
        original = "你好，今天很开心。"
        self.enable_translation("日语", [original, "こんにちは、今日はとても嬉しいです。"])
        event = h.Event(original)
        self.config["audio"]["language"] = "zh"
        self.config["language_models"].update(enabled=True, ja_model="japanese-model", ja_voice="japanese-voice")
        with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
            await self.plugin.random_voice_reply(event)
        self.assertEqual(self.context.llm_generate.await_count, 2)
        network.assert_called_once()
        payload = network.call_args.args[2]
        self.assertEqual(payload["input"]["text"], "こんにちは、今日はとても嬉しいです。")
        self.assertEqual(payload["input"]["language_hints"], ["ja"])
        self.assertEqual(payload["model"], "japanese-model")
        self.assertEqual(payload["input"]["voice"], "japanese-voice")
        self.assertEqual(self.config["translation"]["language"], "日语")

    async def test_repeated_chinese_translation_keeps_original_without_tts(self):
        self.enable_translation("日语", ["这是中文回答。", "这里还是中文，没有翻译。"])
        event = h.Event("你好")
        with patch.object(h.main, "synthesize_http") as network:
            await self.plugin.random_voice_reply(event)
        self.assertEqual(self.context.llm_generate.await_count, 2)
        network.assert_not_called()
        self.assertEqual(event.result.chain[0].text, "你好")
        self.assertFalse(any(isinstance(item, h.Record) for item in event.result.chain))

    async def test_wrong_language_is_rejected_even_when_not_identical_to_source(self):
        self.enable_translation("日语", ["好的，我们现在就出发吧！", "那我们马上走吧！"])
        with patch.object(h.main, "synthesize_http") as network:
            with self.assertRaisesRegex(TTSError, "日语.*未生成语音"):
                await self.plugin._speak("一起出去走走")
        network.assert_not_called()

    async def test_valid_japanese_needs_only_one_translation(self):
        for translated in ("こんにちは！", "ｺﾝﾆﾁﾊ", "東京大学医学部附属病院", "了解。", "2026", "OpenAI"):
            with self.subTest(translated=translated):
                self.enable_translation("日语", [translated])
                with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
                    await self.plugin._speak("原文")
                self.assertEqual(self.context.llm_generate.await_count, 1)
                self.assertEqual(network.call_args.args[2]["input"]["text"], translated)

    async def test_segments_are_all_translated_and_checked_before_first_tts(self):
        self.enable_translation("日语", ["こんにちは。", "谢谢你。", "还是没有翻译。"])
        plan = {"segments": [{"text": "你好。"}, {"text": "谢谢。"}]}
        with patch.object(h.main, "synthesize_http") as network:
            with self.assertRaisesRegex(TTSError, "日语.*未生成语音"):
                await self.plugin._speak("你好。谢谢。", delivery_plan=plan)
        network.assert_not_called()

    async def test_failed_correction_does_not_retry_tts_or_send_original_audio(self):
        self.enable_translation("日语", ["你好。"])
        event = h.Event("你好。")
        with patch.object(h.main, "synthesize_http") as network:
            await self.plugin.random_voice_reply(event)
        network.assert_not_called()
        self.assertEqual(event.result.chain[0].text, "你好。")

    async def test_traditional_chinese_translation_also_requires_correction(self):
        self.enable_translation("日语", ["這是什麼？謝謝你。", "これは何ですか？ありがとうございます。"])
        with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
            await self.plugin._speak("这是什么，谢谢你")
        self.assertEqual(self.context.llm_generate.await_count, 2)
        self.assertEqual(network.call_args.args[2]["input"]["language_hints"], ["ja"])
