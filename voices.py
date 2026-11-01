# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""Resolve the model and voice for the current persona and speech language.

The voice configuration is a list of per-persona rules plus one default rule.
A rule carries an optional language breakdown, so the saved settings keep a
single source of truth instead of a global pair duplicated in the connection.
"""

import json
import re

from .language_models import LANGUAGE_NAMES, detect_language
from .tts_client import TTSError, model_id

LANGUAGE_CODES = ("zh", "en", "ja")
VOICE_ID = re.compile(r"[A-Za-z0-9_.-]{1,256}")
# Every rule stores both members of a pair, so a partial override is rejected
# instead of silently mixing a voice with the model of another rule.
PAIR_FIELDS = ("model", "voice") + tuple(
    f"{code}_{key}" for code in LANGUAGE_CODES for key in ("model", "voice")
)
RULE_FIELDS = ("persona", "per_language") + PAIR_FIELDS
DEFAULT_FIELDS = ("default_per_language",) + tuple(
    f"default_{field}" for field in PAIR_FIELDS
)
MAX_RULES = 50
MAX_RULES_BYTES = 60_000
RULE_HINT = (
    "每条音色规则只支持 persona、per_language、model、voice 以及 "
    "zh_model/zh_voice、en_model/en_voice、ja_model/ja_voice。"
)


def _pair(model, voice, label):
    """Validate one model/voice pair; both empty means "not configured"."""
    if not isinstance(model, str) or not isinstance(voice, str):
        raise TTSError(f"{label}的模型和音色 ID 必须为文字。")
    model, voice = model.strip(), voice.strip()
    if bool(model) != bool(voice):
        raise TTSError(f"{label}的模型和音色 ID 必须一起填写，或一起留空。")
    if not model:
        return "", ""
    model = model_id(model)
    if not VOICE_ID.fullmatch(voice):
        raise TTSError(f"{label}的音色 ID 格式不正确。")
    return model, voice


def parse_rules(raw):
    """Validate the JSON rule list and return normalized rules in stored order."""
    if not isinstance(raw, str) or len(raw.encode("utf8")) > MAX_RULES_BYTES:
        raise TTSError("音色规则超过 60 KiB 或格式错误。")
    try:
        entries = json.loads(raw or "[]")
    except ValueError:
        raise TTSError("音色规则必须是有效 JSON。") from None
    if not isinstance(entries, list) or len(entries) > MAX_RULES:
        raise TTSError(f"音色规则必须是不超过 {MAX_RULES} 项的 JSON 数组。")
    rules, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - set(RULE_FIELDS):
            raise TTSError(RULE_HINT)
        persona = entry.get("persona", "")
        if not isinstance(persona, str) or not 1 <= len(persona.strip()) <= 64:
            raise TTSError("音色规则的人格名称需为 1～64 字。")
        persona = persona.strip()
        if persona in seen:
            raise TTSError(f"同一人格只能有一条音色规则：{persona}")
        seen.add(persona)
        per_language = entry.get("per_language", False)
        if type(per_language) is not bool:
            raise TTSError(f"音色规则 {persona} 的按语言开关必须为布尔值。")
        rule = {"persona": persona, "per_language": per_language}
        for field in PAIR_FIELDS:
            value = entry.get(field, "")
            if not isinstance(value, str):
                raise TTSError(f"音色规则 {persona} 的 {field} 必须为文字。")
            rule[field] = value.strip()
        for prefix, label in (("", "默认"), ("zh_", "汉语"), ("en_", "英语"), ("ja_", "日语")):
            model, voice = _pair(
                rule[f"{prefix}model"], rule[f"{prefix}voice"], f"音色规则 {persona} 的{label}"
            )
            rule[f"{prefix}model"], rule[f"{prefix}voice"] = model, voice
        if not any(rule[field] for field in ("model", "zh_model", "en_model", "ja_model")):
            raise TTSError(f"音色规则 {persona} 至少需要配置一组模型与音色。")
        rules.append(rule)
    return rules


def normalize_voices(voices):
    """Return the canonical ``voices`` group, raising on an invalid rule set."""
    if not isinstance(voices, dict) or set(voices) - set(DEFAULT_FIELDS) - {
        "enabled",
        "rules_json",
    }:
        raise TTSError(
            "音色配置只支持开关、人格规则和默认模型与音色。"
        )
    enabled = voices.get("enabled", True)
    if type(enabled) is not bool:
        raise TTSError("启用音色配置的值必须为布尔值。")
    per_language = voices.get("default_per_language", False)
    if type(per_language) is not bool:
        raise TTSError("默认音色按语言区分的值必须为布尔值。")
    raw_rules = voices.get("rules_json", "[]")
    rules = parse_rules(raw_rules)
    result = {
        "enabled": enabled,
        "rules_json": "[]" if not rules else json.dumps(rules, ensure_ascii=False),
        "default_per_language": per_language,
    }
    for prefix, label in (("", "默认"), ("zh_", "汉语"), ("en_", "英语"), ("ja_", "日语")):
        model, voice = _pair(
            voices.get(f"default_{prefix}model", ""),
            voices.get(f"default_{prefix}voice", ""),
            f"默认音色的{label}",
        )
        result[f"default_{prefix}model"], result[f"default_{prefix}voice"] = model, voice
    return result


def _stored_pair(entry, model_key, voice_key, label, allow_partial=False):
    """Read one stored pair; a partial pair is a configuration error, not a gap.

    The default model ships with a schema default while its voice starts empty,
    so a fresh installation must report "voice not configured" instead of a
    partial-pair error; every other pair is typed by the user and stays strict.
    """
    model = entry.get(model_key, "")
    voice = entry.get(voice_key, "")
    if bool(model) != bool(voice):
        if allow_partial and model and not voice:
            return "", ""
        raise TTSError(f"{label}的模型和音色 ID 必须一起填写，或一起留空。")
    return model, voice


def _pair_for(entry, code):
    """Pick the pair a rule offers for one language, or an empty pair."""
    if entry.get("per_language") and code in LANGUAGE_CODES:
        model, voice = _stored_pair(
            entry,
            f"{code}_model",
            f"{code}_voice",
            f"音色规则 {entry.get('persona', '')} 的{LANGUAGE_NAMES[code]}",
        )
        if model:
            return model, voice, code
    model, voice = _stored_pair(
        entry, "model", "voice", f"音色规则 {entry.get('persona', '')} 的默认"
    )
    if model:
        return model, voice, ""
    return "", "", ""


def _default_voice(voices, code):
    if voices.get("default_per_language") and code in LANGUAGE_CODES:
        model, voice = _stored_pair(
            voices,
            f"default_{code}_model",
            f"default_{code}_voice",
            f"默认音色的{LANGUAGE_NAMES[code]}",
        )
        if model:
            return model, voice, code
    model, voice = _stored_pair(
        voices, "default_model", "default_voice", "默认音色", allow_partial=True
    )
    if model:
        return model, voice, ""
    return "", "", ""


def _matching_rule(voices, persona):
    if not persona or not voices.get("enabled", True):
        return None
    try:
        rules = parse_rules(voices.get("rules_json", "[]"))
    except TTSError:
        return None
    return next((rule for rule in rules if rule["persona"] == persona), None)


def uses_language_breakdown(config, persona=None):
    """Whether the rule that applies to this request distinguishes languages."""
    voices = config.get("voices", {})
    rule = _matching_rule(voices, persona)
    if rule is not None and rule["per_language"]:
        return True
    return bool(voices.get("default_per_language", False))


def resolve_voice(config, persona=None, code="auto"):
    """Resolve the final model/voice pair for a persona and language.

    Returns:
        A ``(model, voice, source)`` tuple; empty strings mean "not configured"
        so the caller can report a readable configuration error.
    """
    voices = config.get("voices", {})
    rule = _matching_rule(voices, persona)
    if rule is not None:
        model, voice, language = _pair_for(rule, code)
        if model:
            source = f"persona:{rule['persona']}" + (f"/{language}" if language else "")
            return model, voice, source
    return _default_voice(voices, code)


def default_pair(config):
    """Return the default rule's model and voice, ignoring any persona rule."""
    model, voice, _ = _default_voice(config.get("voices", {}), "auto")
    return model, voice


def _debug(message, *args):
    """Log through AstrBot when it is available; the module stays importable offline."""
    try:
        from astrbot.api import logger
    except ImportError:
        return
    logger.debug(message, *args)


def speech_connection(config, text, language=None, persona=None):
    """Select the final speech language and an independent connection copy."""
    target = config.get("translation", {}).get("language", "不翻译")
    code = {name: code for code, name in LANGUAGE_NAMES.items()}.get(target)
    code = code or language or config.get("audio", {}).get("language", "auto")
    if code == "auto" and uses_language_breakdown(config, persona):
        code = detect_language(text)
    model, voice, source = resolve_voice(config, persona, code)
    connection = dict(config.get("connection", {}))
    if model:
        connection.update(model=model, voice=voice)
        _debug(
            "TTS voice resolved: source=%s language=%s model=%s", source, code, model
        )
    return code, connection
