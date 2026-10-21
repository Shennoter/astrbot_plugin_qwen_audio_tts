# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""Validate and snapshot dashboard drafts without exposing stored credentials."""

import copy
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from .pronunciation import compose_settings, parse_dictionary
from .tts_client import TTSError, build_payload, number, workspace_base

SCHEMA = json.loads(
    Path(__file__).with_name("_conf_schema.json").read_text(encoding="utf8")
)
RANGES = {
    "connection": {
        "timeout": (5, 300),
        "max_concurrent": (1, 8),
        "keep_hours": (1, 720),
    },
    "reply": {
        "probability": (0, 100),
        "max_chars": (1, 10000),
        "cooldown_seconds": (0, 3600),
    },
    "command": {"cooldown_seconds": (0, 3600)},
    "preview": {"cooldown_seconds": (0, 3600)},
    "audio": {
        "sample_rate": (8000, 48000),
        "volume": (0, 100),
        "rate": (0.5, 2),
        "pitch": (0.5, 2),
        "bit_rate": (6, 510),
        "seed": (-1, 65535),
    },
    "cloning": {
        "max_prompt_audio_length": (3, 30),
        "cooldown_seconds": (10, 3600),
        "daily_limit": (1, 100),
    },
}


def snapshot(config):
    """Read a complete, independent snapshot, migrating old short workspace IDs."""
    result = {
        group: {
            key: copy.deepcopy(config.get(group, {}).get(key, field["default"]))
            for key, field in meta["items"].items()
        }
        for group, meta in SCHEMA.items()
    }
    address = result["connection"]["workspace"]
    if re.fullmatch(r"ws-[A-Za-z0-9]+", address):
        result["connection"]["workspace"] = address + ".cn-beijing.maas.aliyuncs.com"
    return result


def revision(config):
    return hashlib.sha256(
        json.dumps(snapshot(config), ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def public_settings(config):
    value = snapshot(config)
    key_set = bool(value["connection"]["api_key"])
    value["connection"]["api_key"] = ""
    return {
        "config": value,
        "schema": SCHEMA,
        "revision": revision(config),
        "api_key_set": key_set,
    }


def validate_draft(current, patch, clear_api_key=False):
    """Apply only schema fields, with strict types, bounds and semantic checks."""
    if not isinstance(patch, dict) or set(patch) - set(SCHEMA):
        raise TTSError("设置包含未知分类或格式错误。")
    if len(json.dumps(patch, ensure_ascii=False).encode()) > 128 * 1024:
        raise TTSError("设置内容超过 128 KiB 限制。")
    result = snapshot(current)
    for group, values in patch.items():
        fields = SCHEMA[group]["items"]
        if not isinstance(values, dict) or set(values) - set(fields):
            raise TTSError(f"{group} 包含未知设置。")
        for key, value in values.items():
            field = fields[key]
            kind = field["type"]
            valid = {
                "bool": type(value) is bool,
                "int": type(value) is int,
                "float": type(value) in (int, float),
                "string": isinstance(value, str),
                "text": isinstance(value, str),
                "list": isinstance(value, list),
            }.get(kind, False)
            if not valid:
                raise TTSError(f"{field['description']} 的数据类型不正确。")
            if isinstance(value, str) and len(value) > (
                60000 if kind == "text" else 2048
            ):
                raise TTSError(f"{field['description']} 内容过长。")
            if kind == "list" and (
                len(value) > 500
                or any(not isinstance(item, str) or len(item) > 500 for item in value)
            ):
                raise TTSError(
                    f"{field['description']} 必须是不超过 500 项的字符串列表。"
                )
            if "options" in field and value not in field["options"]:
                raise TTSError(f"{field['description']} 不在支持的选项内。")
            limits = RANGES.get(group, {}).get(key)
            if limits:
                number(value, *limits, field["description"], kind == "int")
            if group == "connection" and key == "api_key" and not value:
                continue  # Empty means keep; explicit clearing uses a separate flag.
            result[group][key] = copy.deepcopy(value)
    if type(clear_api_key) is not bool:
        raise TTSError("清除密钥标志必须为布尔值。")
    if clear_api_key:
        result["connection"]["api_key"] = ""
    c = result["connection"]
    if any(ch in c["api_key"] for ch in "\r\n"):
        raise TTSError("API Key 不能包含换行。")
    if c["workspace"]:
        address = c["workspace"].strip()
        if re.fullmatch(r"ws-[A-Za-z0-9]+", address):
            raise TTSError("请填写业务空间完整域名，而不是短 ID。")
        c["workspace"] = urlparse(workspace_base(address)).hostname
    if (
        not re.fullmatch(r"qwen-audio-[A-Za-z0-9.-]+", c["model"])
        or "-tts-" not in c["model"]
    ):
        raise TTSError("请填写使用此 HTTP 接口的 qwen-audio-*-tts-* 模型 ID。")
    if c["voice"] and not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", c["voice"]):
        raise TTSError("音色 ID 格式不正确。")
    parse_dictionary(result["pronunciation"]["dictionary_json"])
    audio = compose_settings(result["audio"], result["pronunciation"])
    build_payload(c["model"], c["voice"] or "validation", "验证", audio)
    if not re.fullmatch(r"[A-Za-z0-9]{1,10}", result["cloning"]["prefix"]):
        raise TTSError("音色前缀需为 1～10 位英文字母或数字。")
    return result
