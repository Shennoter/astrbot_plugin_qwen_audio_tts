# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""Offline regressions for voice routing at the actual synthesis boundary."""

import asyncio
import copy
import json
import types
import unittest
from unittest.mock import AsyncMock, patch

from . import settings
from . import test_plugin as h
from .language_models import detect_language
from .tts_client import TTSError
from .voices import parse_rules


class LanguageModelTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = h.PluginTests.asyncSetUp
    enable_translation = h.PluginTests.enable_translation

    def enable_routes(self):
        voices = self.config["voices"]
        voices["default_per_language"] = True
        for code in ("zh", "en", "ja"):
            voices.update(
                {f"default_{code}_model": f"model-{code}", f"default_{code}_voice": f"voice-{code}"}
            )

    def enable_persona(self, persona="kuchiba-chisa", voice="persona-voice", per_language=False):
        voices = copy.deepcopy(self.config["voices"])
        rule = {
            "persona": persona,
            "per_language": per_language,
            "model": "" if per_language else "persona-model",
            "voice": "" if per_language else voice,
            "zh_model": "",
            "zh_voice": "",
            "en_model": "",
            "en_voice": "",
            "ja_model": "",
            "ja_voice": "",
        }
        if per_language:
            rule.update(zh_model="persona-zh-model", zh_voice="persona-zh-voice")
        voices["rules_json"] = json.dumps([rule], ensure_ascii=False)
        self.config["voices"] = voices

    def attach_persona(self, name="kuchiba-chisa"):
        """Stub the AstrBot persona/conversation managers used by _current_persona."""
        self.context.persona_manager = types.SimpleNamespace(
            resolve_selected_persona=AsyncMock(
                return_value=(name, {"name": name}, None, False)
            )
        )
        self.context.conversation_manager = types.SimpleNamespace(
            get_curr_conversation_id=AsyncMock(return_value="cid"),
            get_conversation=AsyncMock(
                return_value=types.SimpleNamespace(persona_id=name)
            ),
        )

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
                self.config["voices"]["default_per_language"] = case != "disabled"
                self.config["voices"].update(default_en_model="", default_en_voice="")
                with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
                    await self.plugin._speak("123 !!!" if case == "unknown" else "Hello")
                payload = network.call_args.args[2]
                self.assertEqual(payload["model"], self.config["voices"]["default_model"])
                self.assertEqual(
                    payload["input"]["voice"], self.config["voices"]["default_voice"]
                )
                if case == "disabled":
                    self.assertNotIn("language_hints", payload["input"])

    async def test_persona_rule_applies_to_every_speech_entrypoint(self):
        self.enable_persona()
        self.attach_persona()
        self.config["command"]["cooldown_seconds"] = 0
        with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
            await self.plugin._speak("你好", persona="kuchiba-chisa")
            await self.plugin._speech_chain(h.Event(), "你好", "zh")
            event = h.Event("你好")
            await self.plugin.random_voice_reply(event)
        self.assertEqual(network.call_count, 3)
        for call in network.call_args_list:
            self.assertEqual(call.args[2]["model"], "persona-model")
            self.assertEqual(call.args[2]["input"]["voice"], "persona-voice")

    async def test_persona_rule_language_breakdown_wins_over_default_rule(self):
        self.enable_routes()
        self.enable_persona(per_language=True)
        with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
            await self.plugin._speak("你好", language="zh", persona="kuchiba-chisa")
        payload = network.call_args.args[2]
        self.assertEqual(payload["model"], "persona-zh-model")
        self.assertEqual(payload["input"]["voice"], "persona-zh-voice")
        # English has no persona pair, so the rule's own default falls through.
        with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
            await self.plugin._speak("Hello", language="en", persona="kuchiba-chisa")
        self.assertEqual(network.call_args.args[2]["model"], "model-en")

    async def test_unknown_or_disabled_persona_falls_back_to_default_rule(self):
        self.enable_routes()
        self.enable_persona()
        for case, persona in (("unknown", "whale-chan"), ("missing", None), ("disabled", "kuchiba-chisa")):
            with self.subTest(case=case):
                self.config["voices"]["enabled"] = case != "disabled"
                with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
                    await self.plugin._speak("Hello", language="en", persona=persona)
                self.assertEqual(network.call_args.args[2]["model"], "model-en")
                self.config["voices"]["enabled"] = True

    async def test_persona_id_wins_over_the_display_name(self):
        self.enable_persona(persona="persona-id-1")
        self.context.persona_manager = types.SimpleNamespace(
            resolve_selected_persona=AsyncMock(
                return_value=("persona-id-1", {"name": "显示名"}, None, False)
            )
        )
        self.context.conversation_manager = types.SimpleNamespace(
            get_curr_conversation_id=AsyncMock(return_value="cid"),
            get_conversation=AsyncMock(return_value=types.SimpleNamespace(persona_id="persona-id-1")),
        )
        event = h.Event("你好")
        with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
            await self.plugin.random_voice_reply(event)
        self.assertEqual(network.call_args.args[2]["model"], "persona-model")
        self.assertEqual(await self.plugin._current_persona(event), "persona-id-1")

    async def test_missing_voice_is_reported_instead_of_keyerror(self):
        self.config["voices"]["default_voice"] = ""
        self.config["delivery"]["mode"] = "动态参数"
        with patch.object(h.main, "plan_dynamic", new=AsyncMock(return_value=("", None))):
            with self.assertRaisesRegex(TTSError, "音色配置"):
                await self.plugin._speak("Hello")
        self.config["delivery"]["mode"] = "混合"
        with self.assertRaisesRegex(TTSError, "音色配置"):
            await self.plugin._speak(
                "你好再见",
                delivery_plan={"segments": [{"text": "你好"}, {"text": "再见"}]},
            )

    async def test_persona_lookup_uses_the_current_conversation(self):
        self.enable_persona()
        self.attach_persona("kuchiba-chisa")
        event = h.Event("你好")
        with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
            await self.plugin.random_voice_reply(event)
        self.assertEqual(network.call_args.args[2]["model"], "persona-model")
        manager = self.context.persona_manager
        manager.resolve_selected_persona.assert_awaited_once()
        kwargs = manager.resolve_selected_persona.await_args.kwargs
        self.assertEqual(kwargs["conversation_persona_id"], "kuchiba-chisa")
        self.assertEqual(kwargs["platform_name"], "aiocqhttp")
        # The lookup is cached per event, so a second call must not hit the database.
        self.assertEqual(await self.plugin._current_persona(event), "kuchiba-chisa")
        self.assertEqual(manager.resolve_selected_persona.await_count, 1)

    async def test_missing_persona_api_and_webchat_default_keep_the_default_rule(self):
        self.enable_persona()
        with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
            await self.plugin._speak("你好", persona=None)
        self.assertEqual(network.call_args.args[2]["model"], self.config["voices"]["default_model"])
        self.context.persona_manager = types.SimpleNamespace(
            resolve_selected_persona=AsyncMock(
                return_value=("_chatui_default_", {"name": "_chatui_default_"}, None, True)
            )
        )
        self.context.conversation_manager = types.SimpleNamespace(
            get_curr_conversation_id=AsyncMock(return_value=None),
            get_conversation=AsyncMock(return_value=None),
        )
        self.assertIsNone(await self.plugin._current_persona(h.Event()))
        broken = types.SimpleNamespace(
            resolve_selected_persona=AsyncMock(side_effect=RuntimeError("no session"))
        )
        self.context.persona_manager = broken
        self.assertIsNone(await self.plugin._current_persona(h.Event()))

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
        draft["voices"].update(
            default_per_language=True,
            default_ja_model="preview-model",
            default_ja_voice="preview-voice",
        )
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
        self.config["voices"]["default_voice"] = ""
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

    async def test_segments_keep_the_persona_voice(self):
        self.enable_persona()
        plan = {"segments": [{"text": "你好"}, {"text": "再见"}]}
        with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
            await self.plugin._speak(
                "你好再见", delivery_plan=plan, persona="kuchiba-chisa"
            )
        self.assertEqual(network.call_count, 2)
        for call in network.call_args_list:
            self.assertEqual(call.args[2]["model"], "persona-model")

    async def test_partial_pair_fails_before_any_segment_is_sent(self):
        self.enable_routes()
        self.config["voices"]["default_en_voice"] = ""
        with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()) as network:
            with self.assertRaisesRegex(TTSError, "一起填写"):
                await self.plugin._speak(
                    "你好Hello", delivery_plan={"segments": [{"text": "你好"}, {"text": "Hello"}]}
                )
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
        self.config["voices"]["default_voice"] = ""
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
        self.assertEqual(profile["config"]["voices"], self.config["voices"])
        self.assertFalse(settings.snapshot({})["voices"]["default_per_language"])
        for patch_value in (
            {"default_en_model": "only-model", "default_en_voice": ""},
            {"default_ja_voice": "bad voice"},
            {"enabled": "yes"},
            {"rules_json": json.dumps([{"persona": "a", "model": "m"}])},
        ):
            with self.subTest(patch=patch_value), self.assertRaises(TTSError):
                settings.validate_draft(self.config, {"voices": patch_value})

    async def test_one_rule_per_persona_is_enforced(self):
        duplicate = json.dumps(
            [
                {"persona": "a", "model": "m", "voice": "v"},
                {"persona": "a", "model": "m", "voice": "v"},
            ]
        )
        with self.assertRaisesRegex(TTSError, "同一人格只能有一条"):
            parse_rules(duplicate)

    def test_detection_is_conservative_with_ssml_and_ambiguous_scripts(self):
        for text, expected in (("<speak>你好</speak>", "zh"), ("日本語", "zh"), ("ｺﾝﾆﾁﾊ", "ja"), ("Привет TTS", "auto"), ("123 🙂", "auto"), ("안녕하세요", "auto")):
            self.assertEqual(detect_language(text), expected)


class JapaneseTranslationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = h.PluginTests.asyncSetUp
    enable_translation = h.PluginTests.enable_translation

    def enable_japanese(self):
        self.config["voices"].update(
            default_per_language=True,
            default_ja_model="japanese-model",
            default_ja_voice="japanese-voice",
        )

    async def test_chinese_translation_is_corrected_before_automatic_speech(self):
        original = "你好，今天很开心。"
        self.enable_translation("日语", [original, "こんにちは、今日はとても嬉しいです。"])
        event = h.Event(original)
        self.config["audio"]["language"] = "zh"
        self.enable_japanese()
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
