# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""Offline regressions for the persona voice rules and their migration."""

import copy
import json
import unittest

from . import settings
from . import test_plugin as h
from .tts_client import TTSError
from .voices import (
    default_pair,
    normalize_voices,
    parse_rules,
    resolve_voice,
    speech_connection,
    uses_language_breakdown,
)


def rule(**overrides):
    entry = {"persona": "kuchiba-chisa", "model": "model-a", "voice": "voice-a"}
    entry.update(overrides)
    return entry


class RuleParsingTests(unittest.TestCase):
    def test_rules_are_normalized_and_keep_one_entry_per_persona(self):
        parsed = parse_rules(
            json.dumps(
                [
                    rule(),
                    rule(persona="whale-chan", model="model-b", voice="voice-b"),
                ]
            )
        )
        self.assertEqual([item["persona"] for item in parsed], ["kuchiba-chisa", "whale-chan"])
        self.assertFalse(parsed[0]["per_language"])
        for field in ("zh_model", "en_voice", "ja_model"):
            self.assertEqual(parsed[0][field], "")
        with self.assertRaisesRegex(TTSError, "同一人格只能有一条"):
            parse_rules(json.dumps([rule(), rule()]))

    def test_invalid_rule_sets_are_rejected(self):
        cases = {
            "一起填写": [{"persona": "a", "model": "model-a"}],
            "至少需要配置": [{"persona": "a"}],
            "音色 ID 格式": [{"persona": "a", "model": "model-a", "voice": "bad voice"}],
            "人格名称": [{"persona": " ", "model": "model-a", "voice": "voice-a"}],
            "只支持 persona": [{"persona": "a", "model": "m", "voice": "v", "extra": 1}],
            "布尔值": [{"persona": "a", "model": "m", "voice": "v", "per_language": "yes"}],
        }
        for message, entries in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(TTSError, message):
                    parse_rules(json.dumps(entries))
        with self.assertRaisesRegex(TTSError, "有效 JSON"):
            parse_rules("{")
        with self.assertRaisesRegex(TTSError, "不超过 50 项"):
            parse_rules(json.dumps([rule(persona=f"p{index}") for index in range(51)]))

    def test_normalize_voices_validates_default_pairs(self):
        voices = {
            "enabled": True,
            "rules_json": "[]",
            "default_per_language": True,
            "default_model": "model-a",
            "default_voice": "voice-a",
            "default_zh_model": "model-zh",
            "default_zh_voice": "voice-zh",
        }
        normalized = normalize_voices(voices)
        self.assertEqual(normalized["default_zh_model"], "model-zh")
        self.assertEqual(normalized["default_voice"], "voice-a")
        broken = dict(voices, default_ja_model="only-model")
        with self.assertRaisesRegex(TTSError, "一起填写"):
            normalize_voices(broken)


class VoiceResolutionTests(unittest.TestCase):
    def config(self, rules=(), **voices):
        base = settings.snapshot({})
        base["voices"].update(
            {
                "rules_json": json.dumps(list(rules)),
                "default_model": "default-model",
                "default_voice": "default-voice",
            }
        )
        base["voices"].update(voices)
        return base

    def test_persona_rule_beats_default_and_uses_its_own_language_pairs(self):
        config = self.config(
            [rule(model="", voice="", per_language=True, zh_model="zh-model", zh_voice="zh-voice")]
        )
        self.assertEqual(
            resolve_voice(config, "kuchiba-chisa", "zh"),
            ("zh-model", "zh-voice", "persona:kuchiba-chisa/zh"),
        )
        self.assertEqual(
            resolve_voice(config, "kuchiba-chisa", "en"),
            ("default-model", "default-voice", ""),
        )
        self.assertEqual(
            resolve_voice(config, "whale-chan", "zh"),
            ("default-model", "default-voice", ""),
        )

    def test_rule_default_pair_is_used_when_language_breakdown_is_off(self):
        config = self.config([rule()], default_per_language=True, default_en_model="en-model", default_en_voice="en-voice")
        self.assertEqual(resolve_voice(config, "kuchiba-chisa", "en")[0], "model-a")
        self.assertEqual(resolve_voice(config, None, "en")[0], "en-model")
        self.assertEqual(resolve_voice(config, None, "ja")[0], "default-model")

    def test_disabled_switch_ignores_every_persona_rule(self):
        config = self.config([rule()], enabled=False)
        self.assertEqual(resolve_voice(config, "kuchiba-chisa", "auto")[0], "default-model")

    def test_default_pair_and_language_breakdown_flags(self):
        config = self.config(default_per_language=True, default_zh_model="zh-model", default_zh_voice="zh-voice")
        self.assertEqual(default_pair(config), ("default-model", "default-voice"))
        self.assertTrue(uses_language_breakdown(config))
        config["voices"]["default_per_language"] = False
        self.assertFalse(uses_language_breakdown(config))
        config = self.config([rule(per_language=True, zh_model="m", zh_voice="v")])
        self.assertTrue(uses_language_breakdown(config, "kuchiba-chisa"))
        self.assertFalse(uses_language_breakdown(config, "whale-chan"))

    def test_speech_connection_detects_language_only_when_rules_need_it(self):
        config = self.config(default_per_language=True, default_en_model="en-model", default_en_voice="en-voice")
        code, connection = speech_connection(config, "Hello world")
        self.assertEqual((code, connection["model"]), ("en", "en-model"))
        plain = self.config()
        code, connection = speech_connection(plain, "Hello world")
        self.assertEqual(code, "auto")
        self.assertEqual(connection["model"], "default-model")
        self.assertEqual(connection["voice"], "default-voice")

    def test_speech_connection_never_mutates_the_snapshot(self):
        config = self.config([rule()])
        before = copy.deepcopy(config)
        speech_connection(config, "你好", None, "kuchiba-chisa")
        self.assertEqual(config, before)


class MigrationTests(unittest.TestCase):
    def legacy_config(self):
        config = h.defaults()
        config["connection"]["model"] = "legacy-model"
        config["connection"]["voice"] = "legacy-voice"
        config["language_models"] = {
            "enabled": True,
            "zh_model": "legacy-zh-model",
            "zh_voice": "legacy-zh-voice",
            "en_model": "",
            "en_voice": "",
            "ja_model": "",
            "ja_voice": "",
        }
        return config

    def production_like_config(self):
        """Mirror a 1.1.2 install: legacy groups both at the top and inside a profile."""
        config = self.legacy_config()
        profile = h.defaults()
        # Stored profile configs never carry the shared indexes.
        profile.pop("profiles", None)
        profile.pop("voice_library", None)
        profile["connection"]["model"] = "profile-model"
        profile["connection"]["voice"] = "profile-voice"
        profile["language_models"] = {
            "enabled": True,
            "zh_model": "profile-zh-model",
            "zh_voice": "profile-zh-voice",
            "en_model": "",
            "en_voice": "",
            "ja_model": "",
            "ja_voice": "",
        }
        config["profiles"] = {
            "active_id": "p1",
            "entries_json": json.dumps(
                [{"id": "p1", "name": "旧档案", "config": profile}], ensure_ascii=False
            ),
        }
        return config

    def test_snapshot_seeds_the_default_rule_from_legacy_fields(self):
        seeded = settings.snapshot(self.legacy_config())["voices"]
        self.assertEqual(seeded["default_model"], "legacy-model")
        self.assertEqual(seeded["default_voice"], "legacy-voice")
        self.assertTrue(seeded["default_per_language"])
        self.assertEqual(seeded["default_zh_model"], "legacy-zh-model")
        self.assertEqual(seeded["default_zh_voice"], "legacy-zh-voice")

    def test_existing_rules_are_never_overwritten_by_the_legacy_seed(self):
        config = self.legacy_config()
        config["voices"]["rules_json"] = json.dumps([rule()])
        seeded = settings.snapshot(config)["voices"]
        self.assertEqual(json.loads(seeded["rules_json"])[0]["persona"], "kuchiba-chisa")
        self.assertEqual(seeded["default_voice"], "")

    def test_migrate_voices_writes_once_and_removes_legacy_keys(self):
        config = self.legacy_config()
        self.assertTrue(settings.migrate_voices(config))
        self.assertNotIn("language_models", config)
        self.assertNotIn("model", config["connection"])
        self.assertNotIn("voice", config["connection"])
        self.assertEqual(config["voices"]["default_voice"], "legacy-voice")
        self.assertFalse(settings.migrate_voices(config))

    def test_legacy_dashboard_patch_still_saves(self):
        config = h.defaults()
        candidate = settings.validate_draft(
            config,
            {
                "connection": {"model": "legacy-model", "voice": "legacy-voice"},
                "language_models": {"enabled": True, "en_model": "en-model", "en_voice": "en-voice"},
            },
        )
        self.assertEqual(candidate["voices"]["default_model"], "legacy-model")
        self.assertEqual(candidate["voices"]["default_voice"], "legacy-voice")
        self.assertTrue(candidate["voices"]["default_per_language"])
        self.assertEqual(candidate["voices"]["default_en_model"], "en-model")
        self.assertNotIn("model", candidate["connection"])

    def test_saved_profiles_keep_loading_with_the_old_groups(self):
        entries = settings.profile_entries(self.production_like_config())
        self.assertEqual(len(entries), 1)
        profile = entries[0]["config"]
        self.assertNotIn("language_models", profile)
        self.assertEqual(profile["voices"]["default_voice"], "profile-voice")
        self.assertTrue(profile["voices"]["default_per_language"])
        self.assertEqual(profile["voices"]["default_zh_model"], "profile-zh-model")

    def test_migration_projects_stored_profiles_without_losing_their_voice(self):
        config = self.production_like_config()
        self.assertTrue(settings.migrate_voices(config))
        stored = json.loads(config["profiles"]["entries_json"])
        self.assertEqual(config["profiles"]["active_id"], "p1")
        self.assertNotIn("language_models", stored[0]["config"])
        self.assertEqual(stored[0]["config"]["voices"]["default_voice"], "profile-voice")
        self.assertEqual(stored[0]["config"]["voices"]["default_zh_voice"], "profile-zh-voice")
        # The top-level legacy fields are the source of the new default rule.
        self.assertEqual(config["voices"]["default_voice"], "legacy-voice")


if __name__ == "__main__":
    unittest.main()
