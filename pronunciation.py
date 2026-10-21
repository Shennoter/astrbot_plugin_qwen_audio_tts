# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""Compose request-local pronunciation controls without changing saved settings."""

import copy
import json
import re

from .tts_client import TTSError, parse_hot_fix


def parse_dictionary(value):
    """Read a persistent dictionary of literal words and replacement readings.

    Returns:
        A validated new dictionary.

    Raises:
        TTSError: If the dictionary is malformed or contains empty rules.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value or "{}")
        except ValueError:
            raise TTSError(
                '持久替换词典必须是 JSON 对象，例如 {"鳳凰院凶真":"ほうおういん きょうま"}。'
            ) from None
    if not isinstance(value, dict):
        raise TTSError("持久替换词典必须是词语到读音的 JSON 对象。")
    for word, reading in value.items():
        if (
            not isinstance(word, str)
            or not word.strip()
            or not isinstance(reading, str)
            or not reading.strip()
        ):
            raise TTSError("词典中的词语和读音必须是非空字符串。")
    return dict(value)


def compose_settings(audio, preferences, language=None, hot_fix=None, instruction=None):
    """Merge configured, dictionary, and request-local controls in that order.

    Args:
        audio: Existing audio settings; never mutated.
        preferences: Pronunciation mode and persistent dictionary settings.
        language: Optional request language overriding the configured language.
        hot_fix: Optional request-local hot_fix object or JSON string.
        instruction: Optional request-local natural-language instruction.

    Returns:
        New audio settings for one request, with merged literal rules.

    Raises:
        TTSError: If a mode or rule is invalid or cannot be used in that mode.
    """
    settings = copy.deepcopy(audio)
    mode = preferences.get("request_mode", "hot_fix")
    if mode not in ("hot_fix", "instruction", "both"):
        raise TTSError("单次发音模式请选择 hot_fix、instruction 或 both。")
    base = parse_hot_fix(settings.get("hot_fix_json", ""))
    rules = {"pronunciation": {}, "replace": {}}
    for kind, entries in base.items():
        for entry in entries:
            rules[kind].update(entry)
    target_language = language or settings.get("language", "auto")
    dictionary_language = preferences.get("dictionary_language", "all")
    if preferences.get("dictionary_enabled", True) and dictionary_language in (
        "all",
        target_language,
    ):
        for word, reading in parse_dictionary(
            preferences.get("dictionary_json", "{}")
        ).items():
            rules["pronunciation"].pop(word, None)
            rules["replace"][word] = reading
    local = parse_hot_fix(hot_fix)
    local_instruction = str(instruction or "").strip()
    if mode in ("hot_fix", "both"):
        for kind, entries in local.items():
            for entry in entries:
                # A local reading replaces any conflicting dictionary rule of either kind.
                for word, reading in entry.items():
                    other_kind = (
                        "replace" if kind == "pronunciation" else "pronunciation"
                    )
                    rules[other_kind].pop(word, None)
                    rules[kind][word] = reading
    if mode == "instruction":
        for kind, entries in local.items():
            for entry in entries:
                for word, reading in entry.items():
                    rules["replace"].pop(word, None)
                    rules["pronunciation"].pop(word, None)
                    local_instruction += f" 将{json.dumps(word, ensure_ascii=False)}读作{json.dumps(reading, ensure_ascii=False)}。"
    if mode == "hot_fix" and local_instruction and not any(local.values()):
        raise TTSError(
            "当前单次模式为 hot_fix；请提供替换映射，或在设置中切换到 instruction/both。"
        )
    if mode in ("instruction", "both") and local_instruction:
        base_instruction = str(settings.get("instruction", "")).strip()
        settings["instruction"] = "\n".join(
            part for part in (base_instruction, local_instruction.strip()) if part
        )
    merged = {
        kind: [
            {word: reading}
            for word, reading in sorted(entries.items(), key=lambda item: -len(item[0]))
        ]
        for kind, entries in rules.items()
        if entries
    }
    settings["hot_fix_json"] = json.dumps(merged, ensure_ascii=False) if merged else ""
    return settings


def estimated_text(text, hot_fix):
    """Estimate replacement expansion once for the configured maximum text length.

    Returns:
        Text after simultaneous literal replacements, used only for length checking.
    """
    replacements = {}
    for entry in hot_fix.get("replace", []):
        replacements.update(entry)
    if not replacements:
        return text
    pattern = "|".join(
        re.escape(word) for word in sorted(replacements, key=len, reverse=True)
    )
    return re.sub(pattern, lambda match: replacements[match.group(0)], text)
