# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""Regression tests for ephemeral controls, persistent dictionaries, and tools."""

import copy
import json
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from . import test_plugin as harness
from .pronunciation import compose_settings, estimated_text, parse_dictionary
from .tts_client import TTSError, build_payload


def flattened(settings, kind="replace"):
    result = {}
    for entry in json.loads(settings["hot_fix_json"] or "{}").get(kind, []):
        result.update(entry)
    return result


class PronunciationTests(TestCase):
    def test_language_dictionaries_are_isolated_and_auto_does_not_mix(self):
        prefs = {f"dictionary_{code}_json": json.dumps({"名字": reading}) for code, reading in [("zh", "中文读音"), ("ja", "なまえ"), ("en", "name")]}
        before = copy.deepcopy(prefs)
        for code, reading in [("zh", "中文读音"), ("ja", "なまえ"), ("en", "name")]:
            self.assertEqual(flattened(compose_settings({}, prefs, code)), {"名字": reading})
        self.assertFalse(flattened(compose_settings({}, prefs, "auto")))
        self.assertEqual(flattened(compose_settings({"language": "en"}, prefs)), {"名字": "name"})
        self.assertEqual(prefs, before)
        prefs["dictionary_enabled"] = False
        self.assertFalse(flattened(compose_settings({}, prefs, "ja")))

    def test_merge_priority_and_no_mutation(self):
        audio = {"hot_fix_json": '{"replace":[{"名字":"基础"},{"旧词":"保留"}]}'}
        prefs = {"dictionary_json": '{"名字":"词典","常用词":"固定"}'}
        local = {"replace": [{"名字": "单次"}]}
        snapshot = copy.deepcopy((audio, prefs, local))
        settings = compose_settings(audio, prefs, "ja", local)
        self.assertEqual(
            flattened(settings), {"名字": "单次", "旧词": "保留", "常用词": "固定"}
        )
        self.assertEqual((audio, prefs, local), snapshot)
        next_settings = compose_settings(audio, prefs, "ja")
        self.assertEqual(flattened(next_settings)["名字"], "词典")

    def test_one_word_cannot_have_conflicting_replacement_and_pronunciation(self):
        audio = {"hot_fix_json": '{"pronunciation":[{"名字":"ming2 zi4"}]}'}
        prefs = {"dictionary_json": '{"名字":"なまえ"}'}
        settings = compose_settings(audio, prefs)
        self.assertNotIn("名字", flattened(settings, "pronunciation"))
        settings = compose_settings(
            audio, prefs, hot_fix={"pronunciation": [{"名字": "ming2"}]}
        )
        self.assertNotIn("名字", flattened(settings))
        self.assertEqual(flattened(settings, "pronunciation"), {"名字": "ming2"})

    def test_modes_select_single_request_parameters(self):
        local = {"replace": [{"鳳凰院凶真": "ほうおういん きょうま"}]}
        settings = compose_settings(
            {}, {"request_mode": "hot_fix"}, "ja", local, "单次指令"
        )
        self.assertNotIn("instruction", settings)
        self.assertIn("鳳凰院凶真", flattened(settings))
        settings = compose_settings(
            {"instruction": "全局语气"},
            {
                "request_mode": "instruction",
                "dictionary_json": '{"鳳凰院凶真":"错误读音","其他":"保留"}',
            },
            "ja",
            local,
            "单次语气",
        )
        self.assertNotIn("鳳凰院凶真", flattened(settings))
        self.assertEqual(flattened(settings), {"其他": "保留"})
        self.assertIn("ほうおういん きょうま", settings["instruction"])
        self.assertIn("全局语气", settings["instruction"])
        settings = compose_settings({}, {"request_mode": "both"}, "ja", local, "温柔")
        self.assertIn("鳳凰院凶真", flattened(settings))
        self.assertEqual(settings["instruction"], "温柔")

    def test_instruction_only_in_hot_fix_mode_is_not_silently_ignored(self):
        with self.assertRaisesRegex(TTSError, "当前单次模式"):
            compose_settings({}, {"request_mode": "hot_fix"}, instruction="温柔")

    def test_dictionary_language_and_enablement(self):
        prefs = {"dictionary_json": '{"名字":"なまえ"}', "dictionary_language": "ja"}
        self.assertFalse(flattened(compose_settings({}, prefs, "en")))
        self.assertFalse(flattened(compose_settings({}, prefs, "auto")))
        self.assertIn("名字", flattened(compose_settings({}, prefs, "ja")))
        prefs["dictionary_enabled"] = False
        self.assertFalse(flattened(compose_settings({}, prefs, "ja")))

    def test_literal_longest_match_length_estimation_does_not_cascade(self):
        rules = {
            "replace": [
                {"A": "B"},
                {"AB": "完整"},
                {"B": "很长的读音"},
                {".*": "字面词"},
            ]
        }
        self.assertEqual(estimated_text("AB A .*", rules), "完整 B 字面词")

    def test_invalid_dictionary_or_mode_fails_clearly(self):
        for value in ("[]", '{"":"读音"}', '{"名字":3}', "{invalid}"):
            with self.subTest(value=value), self.assertRaises(TTSError):
                parse_dictionary(value)
        with self.assertRaises(TTSError):
            compose_settings({}, {"request_mode": "unknown"})

    def test_request_payload_keeps_original_text_and_uses_kana_hot_fix(self):
        original = "鳳凰院凶真"
        settings = compose_settings(
            {}, {}, "ja", '{"replace":[{"鳳凰院凶真":"ほうおういん きょうま"}]}'
        )
        payload = build_payload(
            "qwen-audio-3.1-tts-plus", "voice", original, settings, "ja"
        )
        self.assertEqual(payload["input"]["text"], original)
        self.assertEqual(payload["input"]["language_hints"], ["ja"])
        self.assertEqual(
            payload["input"]["hot_fix"]["replace"],
            [{original: "ほうおういん きょうま"}],
        )


class NaturalLanguageTests(IsolatedAsyncioTestCase):
    asyncSetUp = harness.PluginTests.asyncSetUp

    async def test_natural_speech_sends_audio_with_single_request_controls(self):
        event = harness.Event()
        before = copy.deepcopy(self.config)
        self.plugin._speak = AsyncMock(return_value=self.audio)
        result = await self.plugin.natural_speech(
            event,
            "鳳凰院凶真",
            "ja",
            '{"replace":[{"鳳凰院凶真":"ほうおういん きょうま"}]}',
            "",
        )
        self.assertIn("已发送", result)
        self.assertEqual(len(event.sent), 1)
        self.assertIsInstance(event.sent[0].chain[0], harness.Record)
        self.assertIn("鳳凰院凶真", self.plugin._speak.call_args.kwargs["hot_fix"])
        self.assertEqual(self.config, before)
        self.assertFalse(hasattr(self.config, "saved"))
        await self.plugin.random_voice_reply(event)
        self.assertEqual(self.plugin._speak.await_count, 1)

    async def test_natural_speech_delivery_failure_does_not_claim_success(self):
        event = harness.Event()
        event.send = AsyncMock(side_effect=OSError("adapter error"))
        self.plugin._speak = AsyncMock(return_value=self.audio)
        result = await self.plugin.natural_speech(event, "Hello", "en")
        self.assertIn("发送失败", result)
        self.assertNotIn("已发送", result)

    async def test_natural_speech_obeys_scope_and_permissions(self):
        self.config["command"]["admin_only"] = True
        event = harness.Event()
        event.admin = False
        result = await self.plugin.natural_speech(event, "你好", "zh")
        self.assertIn("管理员", result)
        self.assertFalse(event.sent)

    async def test_dictionary_tool_edits_languages_independently(self):
        event = harness.Event()
        for code, reading in [("zh", "中文"), ("ja", "日本語"), ("en", "English")]:
            result = await self.plugin.natural_dictionary(event, "set", "名字", reading, code)
            self.assertIn("已保存", result)
            self.assertEqual(json.loads(await self.plugin.natural_dictionary(event, "list", language=code)), {"名字": reading})
        await self.plugin.natural_dictionary(event, "delete", "名字", language="ja")
        self.assertEqual(await self.plugin.natural_dictionary(event, "list", language="ja"), "{}")
        self.assertEqual(json.loads(await self.plugin.natural_dictionary(event, "list", language="en")), {"名字": "English"})
        self.config["translation"]["language"] = "英语"
        self.assertEqual(json.loads(await self.plugin.natural_dictionary(event, "list")), {"名字": "English"})
        self.assertIn("未保存", await self.plugin.natural_dictionary(event, "set", "名字", "读音", "xx"))

    async def test_persistent_dictionary_add_edit_delete_and_reload(self):
        self.config["audio"]["language"] = "ja"
        disk = Path(self.tmp.name) / "plugin_config.json"

        def save(config):
            disk.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

        with patch.object(harness.Config, "save_config", save):
            event = harness.Event()
            result = await self.plugin.natural_dictionary(
                event, "set", "鳳凰院凶真", "ほうおういん きょうま"
            )
            self.assertIn("已保存", result)
            reloaded = json.loads(disk.read_text(encoding="utf-8"))
            self.assertEqual(
                parse_dictionary(reloaded["pronunciation"]["dictionary_ja_json"])[
                    "鳳凰院凶真"
                ],
                "ほうおういん きょうま",
            )
            self.assertIn(
                "鳳凰院凶真",
                flattened(
                    compose_settings(reloaded["audio"], reloaded["pronunciation"])
                ),
            )
            await self.plugin.natural_dictionary(event, "set", "鳳凰院凶真", "更新读音")
            self.assertIn(
                "更新读音", await self.plugin.natural_dictionary(event, "list")
            )
            await self.plugin.natural_dictionary(event, "delete", "鳳凰院凶真")
            self.assertEqual(await self.plugin.natural_dictionary(event, "list"), "{}")

    async def test_non_admin_cannot_persist_dictionary_or_settings(self):
        event = harness.Event()
        event.admin = False
        before = copy.deepcopy(self.config)
        self.assertIn(
            "管理员", await self.plugin.natural_dictionary(event, "set", "名字", "读音")
        )
        self.assertIn(
            "管理员",
            await self.plugin.natural_voice_control(event, "probability", "80"),
        )
        self.assertEqual(self.config, before)
        self.assertFalse(hasattr(self.config, "saved"))

    async def test_failed_save_restores_config_and_reports_failure(self):
        before = copy.deepcopy(self.config)
        with patch.object(
            harness.Config, "save_config", side_effect=OSError("disk full")
        ):
            result = await self.plugin.natural_dictionary(
                harness.Event(), "set", "名字", "读音"
            )
        self.assertIn("未保存", result)
        self.assertEqual(self.config, before)

    async def test_natural_settings_cover_probability_length_and_audio(self):
        event = harness.Event()
        for key, value in (
            ("probability", "30"),
            ("max_chars", "300"),
            ("request_mode", "both"),
            ("language", "日语"),
            ("rate", "0.8"),
            ("enabled", "off"),
            ("instruction", "以后温柔一点"),
        ):
            with self.subTest(key=key):
                self.assertIn(
                    "已持久保存",
                    await self.plugin.natural_voice_control(event, key, value),
                )
        self.assertEqual(self.config["reply"]["probability"], 30)
        self.assertEqual(self.config["reply"]["max_chars"], 300)
        self.assertFalse(self.config["reply"]["enabled"])
        self.assertEqual(self.config["pronunciation"]["request_mode"], "both")
        self.assertEqual(self.config["audio"]["language"], "ja")
        status = await self.plugin.natural_voice_control(event, "status")
        self.assertNotIn("test-key", status)

    async def test_auto_language_uses_saved_default(self):
        self.config["audio"]["language"] = "ja"
        self.plugin._speak = AsyncMock(return_value=self.audio)
        await self.plugin.natural_speech(harness.Event(), "こんにちは")
        self.assertEqual(self.plugin._speak.call_args.kwargs["language"], "ja")

    async def test_persistent_rules_reach_auto_and_next_request_is_clean(self):
        self.config["pronunciation"]["dictionary_ja_json"] = '{"名字":"词典"}'
        with patch.object(
            harness.main, "synthesize_http", return_value=harness.wav_bytes()
        ) as network:
            await self.plugin._speak(
                "名字", "ja", hot_fix={"replace": [{"名字": "单次"}]}
            )
            first = network.call_args.args[2]["input"]["hot_fix"]["replace"]
            await self.plugin._speak("名字", "ja", automatic=True)
            second = network.call_args.args[2]["input"]["hot_fix"]["replace"]
        self.assertEqual(first, [{"名字": "单次"}])
        self.assertEqual(second, [{"名字": "词典"}])

    async def test_replacement_expansion_respects_maximum_length(self):
        self.config["audio"]["language"] = "ja"
        self.config["pronunciation"]["dictionary_ja_json"] = json.dumps({"字": "A" * 201})
        with (
            patch.object(harness.main, "synthesize_http") as network,
            self.assertRaisesRegex(TTSError, "预计文字长度"),
        ):
            await self.plugin._speak("字")
        network.assert_not_called()

    async def test_natural_tools_can_be_disabled(self):
        self.config["natural"]["enabled"] = False
        event = harness.Event()
        self.assertIn("已关闭", await self.plugin.natural_speech(event, "你好"))
        self.assertIn(
            "已关闭", await self.plugin.natural_dictionary(event, "set", "名字", "读音")
        )
        self.assertIn(
            "已关闭", await self.plugin.natural_voice_control(event, "enabled", "on")
        )

    async def test_parameterized_command_does_not_persist(self):
        self.plugin._speak = AsyncMock(return_value=self.audio)
        event = harness.Event()
        values = {
            "text": "鳳凰院凶真",
            "hot_fix": {"replace": [{"鳳凰院凶真": "ほうおういん きょうま"}]},
        }
        before = copy.deepcopy(self.config)
        results = [
            r
            async for r in self.plugin.speech_with_parameters(
                event, "ja", harness.GreedyStr(json.dumps(values, ensure_ascii=False))
            )
        ]
        self.assertIsInstance(results[0].chain[0], harness.Record)
        self.assertEqual(
            self.plugin._speak.call_args.kwargs["hot_fix"], values["hot_fix"]
        )
        self.assertEqual(self.config, before)
