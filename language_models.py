# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""Resolve request-local language, model and voice without changing saved settings."""

import re

from .tts_client import TTSError, model_id

LANGUAGE_NAMES = {"zh": "汉语", "en": "英语", "ja": "日语"}
KANA = r"[\u3041-\u3096\u30a1-\u30fa\uff66-\uff9d]"


def looks_like_chinese_sentence(text):
    """Flag clear Chinese prose, while allowing ambiguous kanji names/labels.

    This is a conservative guard, not a general language classifier. Japanese
    shares Han characters with Chinese; requiring kana would reject real names.
    """
    text = re.sub(r"<[^>]*>", "", text)
    if re.search(KANA, text):
        return False
    return bool(re.search(
        r"这[是个些里样次]|這[是個些裡樣次]|那[个些里样個裡樣]|"
        r"[我你他她它][们們]|[你您][好是的要在能]|你好|謝謝|谢谢|"
        r"怎么|怎麼|什么|什麼|为什么|為什麼|"
        r"[请請](?:问|問|用|把|你|您|稍)|"
        r"没有|沒有|已经|已經|但是|还是|還是|不是|可以|需要|觉得|覺得|"
        r"[我你您他她它](?:知道|今天|现在|現在|想|要|是|在|会|會|有|很|也|不|能)|"
        r"今天|明天|现在|現在|好的|没关系|沒關係|"
        r"[吗嗎呢吧](?:[。！？?!…\s]|$)",
        text,
    ))


def language_pair(preferences, code):
    """Require a complete override so a default voice never crosses models."""
    values = [preferences.get(f"{code}_{key}", "") for key in ("model", "voice")]
    if any(not isinstance(value, str) for value in values):
        raise TTSError(f"{LANGUAGE_NAMES[code]}模型和音色 ID 必须为文字。")
    model, voice = (value.strip() for value in values)
    if bool(model) != bool(voice):
        raise TTSError(f"{LANGUAGE_NAMES[code]}模型和音色 ID 必须一起填写，或一起留空以使用默认配对。")
    if model:
        model = model_id(model)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", voice):
            raise TTSError(f"{LANGUAGE_NAMES[code]}音色 ID 格式不正确。")
    return model, voice


def detect_language(text):
    """Use script hints only; kanji-only Japanese needs an explicit language."""
    text = re.sub(r"<[^>]*>", "", text)
    if re.search(KANA, text):
        return "ja"
    if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002fa1f]", text):
        return "zh"
    # Avoid treating another alphabet plus a Latin abbreviation as English.
    letters = [char for char in text if char.isalpha()]
    if letters and all(char.isascii() for char in letters):
        return "en"
    return "auto"


def speech_connection(config, text, language=None):
    """Select the final speech language and an independent connection copy."""
    target = config.get("translation", {}).get("language", "不翻译")
    code = {name: code for code, name in LANGUAGE_NAMES.items()}.get(target)
    code = code or language or config.get("audio", {}).get("language", "auto")
    connection = dict(config.get("connection", {}))
    preferences = config.get("language_models", {})
    if preferences.get("enabled", False):
        if code == "auto":
            code = config.get("audio", {}).get("language", "auto")
            if code == "auto":
                code = detect_language(text)
        if code in LANGUAGE_NAMES:
            model, voice = language_pair(preferences, code)
            if model:
                connection.update(model=model, voice=voice)
    return code, connection
