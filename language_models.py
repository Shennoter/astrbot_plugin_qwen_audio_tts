# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""Detect the language of a request without changing saved settings."""

import re

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
