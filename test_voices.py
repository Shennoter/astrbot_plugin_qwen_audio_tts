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
    fallback_pair,
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


def defaults():
    """A clean install config built from the schema, without a plugin instance."""
    return {
        group: {
            key: copy.deepcopy(field["default"])
            for key, field in meta["items"].items()
        }
        for group, meta in settings.SCHEMA.items()
    }


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

    def test_default_model_without_a_voice_still_reports_the_model(self):
        config = self.config()
        config["voices"]["default_voice"] = ""
        self.assertEqual(default_pair(config), ("default-model", ""))
        self.assertEqual(
            resolve_voice(config, "kuchiba-chisa", "zh"),
            ("default-model", "", ""),
        )

    def test_fallback_pair_prefers_default_then_language_then_rules(self):
        config = self.config(default_voice="", default_per_language=True)
        config["voices"]["default_en_model"] = "en-model"
        config["voices"]["default_en_voice"] = "en-voice"
        self.assertEqual(fallback_pair(config), ("en-model", "en-voice"))
        config = self.config(default_voice="")
        config["voices"]["rules_json"] = json.dumps([rule(model="rule-model", voice="rule-voice")])
        self.assertEqual(fallback_pair(config), ("rule-model", "rule-voice"))
        self.assertEqual(fallback_pair(self.config(default_voice="")), ("", ""))

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

    def test_existing_rules_are_kept_while_the_legacy_pair_fills_the_defaults(self):
        config = self.legacy_config()
        config["voices"]["rules_json"] = json.dumps([rule()])
        seeded = settings.snapshot(config)["voices"]
        self.assertEqual(json.loads(seeded["rules_json"])[0]["persona"], "kuchiba-chisa")
        # The stored rules stay untouched; only the blank default pair is filled.
        self.assertEqual(seeded["default_model"], "legacy-model")
        self.assertEqual(seeded["default_voice"], "legacy-voice")

    def test_migrate_voices_writes_once_and_removes_legacy_keys(self):
        config = self.legacy_config()
        self.assertTrue(settings.migrate_voices(config))
        self.assertNotIn("language_models", config)
        self.assertNotIn("model", config["connection"])
        self.assertNotIn("voice", config["connection"])
        self.assertEqual(config["voices"]["default_voice"], "legacy-voice")
        self.assertFalse(settings.migrate_voices(config))

    def test_fresh_install_saves_with_the_shipped_default_model(self):
        candidate = settings.validate_draft({}, {"connection": {"api_key": "abc"}})
        self.assertEqual(candidate["voices"]["default_model"], "qwen-audio-3.1-tts-plus")
        self.assertEqual(candidate["voices"]["default_voice"], "")
        # A blank default voice is reported by speech, not by the settings panel.
        model, voice = default_pair(candidate)
        self.assertEqual((model, voice), ("qwen-audio-3.1-tts-plus", ""))

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

    def test_pruned_config_recovers_the_voice_from_the_active_profile(self):
        config = self.production_like_config()
        # AstrBot rebuilds the plugin config from the schema before the plugin
        # starts, so an upgrade arrives without the legacy top-level fields.
        del config["language_models"]
        del config["connection"]["model"]
        del config["connection"]["voice"]
        seeded = settings.snapshot(config)["voices"]
        self.assertEqual(seeded["default_voice"], "profile-voice")
        self.assertTrue(seeded["default_per_language"])
        self.assertEqual(seeded["default_zh_model"], "profile-zh-model")
        self.assertTrue(settings.migrate_voices(config))
        self.assertEqual(config["voices"]["default_voice"], "profile-voice")
        self.assertNotIn("model", config["connection"])
        stored = json.loads(config["profiles"]["entries_json"])[0]["config"]
        self.assertNotIn("language_models", stored)
        self.assertEqual(stored["voices"]["default_voice"], "profile-voice")
        self.assertFalse(settings.migrate_voices(config))

    def test_pruned_config_without_profiles_is_left_alone(self):
        config = h.defaults()
        config["profiles"] = {"active_id": "default", "entries_json": "[]"}
        self.assertFalse(settings.migrate_voices(config))
        self.assertEqual(config["voices"]["default_voice"], "")


class LegacyVoiceMigrationTests(unittest.TestCase):
    """Regressions for 1.1.x voice defaults that the migration used to drop."""

    def legacy_config(self, rules=(), **voices):
        config = defaults()
        config["connection"]["model"] = "legacy-model"
        config["connection"]["voice"] = "legacy-voice"
        config["language_models"] = {
            "enabled": True,
            "zh_model": "legacy-zh-model",
            "zh_voice": "legacy-zh-voice",
            "en_model": "legacy-en-model",
            "en_voice": "legacy-en-voice",
            "ja_model": "legacy-ja-model",
            "ja_voice": "legacy-ja-voice",
        }
        config["voices"]["rules_json"] = json.dumps(list(rules))
        config["voices"].update(voices)
        return config

    def test_rules_and_the_legacy_pair_both_survive_the_migration(self):
        config = self.legacy_config([rule()])
        self.assertTrue(settings.migrate_voices(config))
        voices = config["voices"]
        self.assertEqual(voices["default_model"], "legacy-model")
        self.assertEqual(voices["default_voice"], "legacy-voice")
        self.assertEqual(json.loads(voices["rules_json"])[0]["voice"], "voice-a")
        self.assertNotIn("model", config["connection"])
        self.assertNotIn("voice", config["connection"])
        self.assertFalse(settings.migrate_voices(config))

    def test_a_configured_default_pair_is_never_replaced_by_the_legacy_pair(self):
        config = self.legacy_config(
            default_model="user-model", default_voice="user-voice"
        )
        self.assertTrue(settings.migrate_voices(config))
        self.assertEqual(config["voices"]["default_model"], "user-model")
        self.assertEqual(config["voices"]["default_voice"], "user-voice")
        self.assertNotIn("voice", config["connection"])

    def test_a_chosen_default_model_without_a_voice_is_left_alone(self):
        # The old "voices untouched" check ignored default_model and replaced it.
        config = self.legacy_config(default_model="user-model")
        self.assertTrue(settings.migrate_voices(config))
        self.assertEqual(config["voices"]["default_model"], "user-model")
        self.assertEqual(config["voices"]["default_voice"], "")

    def test_a_partly_configured_language_pair_keeps_both_of_its_halves(self):
        config = self.legacy_config(
            default_en_model="user-en-model", default_en_voice="user-en-voice"
        )
        self.assertTrue(settings.migrate_voices(config))
        voices = config["voices"]
        self.assertEqual(voices["default_en_model"], "user-en-model")
        self.assertEqual(voices["default_en_voice"], "user-en-voice")
        self.assertEqual(voices["default_zh_model"], "legacy-zh-model")
        self.assertEqual(voices["default_ja_voice"], "legacy-ja-voice")
        self.assertTrue(voices["default_per_language"])

    def test_blank_language_pairs_gain_the_legacy_values_and_the_flag(self):
        config = self.legacy_config()
        self.assertFalse(config["voices"]["default_per_language"])
        self.assertTrue(settings.migrate_voices(config))
        voices = config["voices"]
        self.assertTrue(voices["default_per_language"])
        for code in ("zh", "en", "ja"):
            self.assertEqual(voices[f"default_{code}_model"], f"legacy-{code}-model")
            self.assertEqual(voices[f"default_{code}_voice"], f"legacy-{code}-voice")

    def test_the_language_flag_follows_the_pairs_actually_filled(self):
        config = self.legacy_config()
        config["language_models"]["enabled"] = False
        self.assertTrue(settings.migrate_voices(config))
        self.assertTrue(config["voices"]["default_per_language"])


class LegacyPatchTests(unittest.TestCase):
    """Regressions for the pre-1.2 dashboard payload the save path still accepts."""

    def config(self, **voices):
        config = defaults()
        config["voices"].update(voices)
        return config

    def test_a_language_switch_that_is_not_a_boolean_is_rejected(self):
        for value in ("false", "0", 0, 1, None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(TTSError, "布尔值"):
                    settings.validate_draft(
                        self.config(), {"language_models": {"enabled": value}}
                    )

    def test_a_switch_only_patch_keeps_the_configured_language_pairs(self):
        config = self.config(
            default_en_model="en-model", default_en_voice="en-voice"
        )
        candidate = settings.validate_draft(
            config, {"language_models": {"enabled": True}}
        )
        self.assertEqual(candidate["voices"]["default_en_model"], "en-model")
        self.assertEqual(candidate["voices"]["default_en_voice"], "en-voice")
        self.assertTrue(candidate["voices"]["default_per_language"])

    def test_unknown_old_fields_are_rejected(self):
        for languages in (
            {"fr_model": "m"},
            {"zh_modal": "m"},
            {"voice_map": {}},
            {"enabled": True, "ja_voice_id": "v"},
        ):
            with self.subTest(languages=languages):
                with self.assertRaisesRegex(TTSError, "未知"):
                    settings.validate_draft(
                        self.config(), {"language_models": languages}
                    )

    def test_a_lone_old_model_clears_the_stale_voice(self):
        config = self.config(default_model="old-model", default_voice="old-voice")
        candidate = settings.validate_draft(
            config, {"connection": {"model": "new-model"}}
        )
        self.assertEqual(candidate["voices"]["default_model"], "new-model")
        self.assertEqual(candidate["voices"]["default_voice"], "")
        self.assertNotIn("model", candidate["connection"])

    def test_an_empty_old_patch_writes_nothing(self):
        config = self.config(default_zh_model="zh-model", default_zh_voice="zh-voice")
        candidate = settings.validate_draft(config, {"language_models": {}})
        self.assertEqual(candidate["voices"], settings.snapshot(config)["voices"])
        self.assertEqual(settings._split_legacy_patch({}), ({}, {}))
        self.assertEqual(settings._split_legacy_patch({"language_models": {}}), ({}, {}))
        patch, updates = settings._split_legacy_patch(
            {"connection": {"model": "m", "voice": "v"}}
        )
        self.assertEqual(patch, {"connection": {}})
        self.assertEqual(updates, {"default_model": "m", "default_voice": "v"})


if __name__ == "__main__":
    unittest.main()
