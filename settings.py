# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""Validate and snapshot dashboard drafts without exposing stored credentials."""

import copy
import hashlib
import json
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

from .pronunciation import compose_settings, parse_dictionary
from .tts_client import TTSError, build_payload, model_id, number, workspace_base

SCHEMA = json.loads(
    Path(__file__).with_name("_conf_schema.json").read_text(encoding="utf8")
)
# Internal preview defaults are not exposed as plugin settings.
SCHEMA["preview"] = {
    "description": "音色试听",
    "type": "object",
    "items": {
        "enabled": {
            "description": "启用试听功能",
            "type": "bool",
            "default": True,
            "hint": "在“阿里云TTS助手”可视化页面生成并试听。实时调整使用页面草稿，不自动保存或重复生成。",
        },
        "text": {
            "description": "试听文案",
            "type": "text",
            "default": "你好，这是当前音色的试听。希望这个声音自然、清晰。",
            "hint": "可视化页面的默认文案；页面内可临时改写。受 TTS 最大字数限制，遵循翻译模式。",
        },
        "language": {
            "description": "试听语言",
            "type": "string",
            "default": "auto",
            "options": ["auto", "zh", "en", "ja"],
            "hint": "auto 跟随音频参数中的默认朗读语言。",
        },
        "admin_only": {
            "description": "聊天中仅管理员可调用自然语言试听",
            "type": "bool",
            "default": True,
            "hint": "只影响聊天工具；WebUI 试听始终要求登录 AstrBot 管理面板，不受聊天会话范围限制。",
        },
        "cooldown_seconds": {
            "description": "试听冷却（秒）",
            "type": "float",
            "default": 3.0,
            "hint": "0～3600；WebUI 试听共用一个冷却并防止重复提交，聊天试听按用户和会话计算，与朗读共享记录。",
        },
    },
}
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
    "delivery": {"max_segments": (1, 12), "max_total_pause_ms": (0, 30000)},
    "cloning": {
        "max_prompt_audio_length": (3, 30),
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
    value["preview"]["enabled"] = True
    key_set = bool(value["connection"]["api_key"])
    value["connection"]["api_key"] = ""
    profiles = profile_entries(config)
    for entry in profiles:
        entry["api_key_set"] = bool(entry["config"]["connection"]["api_key"])
        entry["config"]["connection"]["api_key"] = ""
    value["profiles"]["entries_json"] = "[]"
    return {
        "config": value,
        "schema": {k: v for k, v in SCHEMA.items() if k != "preview"},
        "revision": revision(config),
        "api_key_set": key_set,
        "profiles": profiles,
    }


def parse_saved_voices(raw):
    """Validate saved model/voice pairs without storing service credentials."""
    if not isinstance(raw, str) or len(raw.encode("utf8")) > 60000:
        raise TTSError("已保存音色列表超过 60 KiB 或格式错误。")
    try:
        entries = json.loads(raw)
    except ValueError:
        raise TTSError("已保存音色列表必须是有效 JSON。") from None
    if not isinstance(entries, list) or len(entries) > 100:
        raise TTSError("已保存音色列表最多包含 100 项。")
    result, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {
            "name",
            "model",
            "voice",
            "workspace",
        }:
            raise TTSError("每个音色只支持 name、model、voice 和 workspace。")
        if any(
            not isinstance(entry.get(key), str) for key in ("name", "model", "voice")
        ):
            raise TTSError("音色名称、模型和音色 ID 必须为文字。")
        name, model, voice = (entry[key].strip() for key in ("name", "model", "voice"))
        if not name or len(name) > 80:
            raise TTSError("音色名称需为 1～80 字。")
        model = model_id(model)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", voice):
            raise TTSError("已保存音色的音色 ID 格式不正确。")
        workspace = entry.get("workspace", "")
        if not isinstance(workspace, str):
            raise TTSError("已保存音色的业务空间需为完整域名。")
        workspace = (
            urlparse(workspace_base(workspace)).hostname if workspace.strip() else ""
        )
        identity = (workspace, model, voice)
        if identity in seen:
            raise TTSError("同一业务空间的模型与音色配对不能重复保存。")
        seen.add(identity)
        result.append(
            {"name": name, "model": model, "voice": voice, "workspace": workspace}
        )
    return result


def saved_voice_connection(config, name):
    """Resolve a unique saved voice and return one atomic connection update."""
    cfg = snapshot(config)
    matches = [
        entry
        for entry in parse_saved_voices(cfg["voice_library"]["entries_json"])
        if entry["name"] == name
    ]
    if len(matches) != 1:
        raise TTSError("未找到唯一的已保存音色名称，请查看列表或修改重名音色。")
    entry = matches[0]
    connection = dict(cfg["connection"])
    if entry["workspace"]:
        current = urlparse(workspace_base(connection["workspace"])).hostname
        if entry["workspace"] != current:
            raise TTSError(
                "该音色属于其他业务空间，请先在后台配置对应的业务空间和 API Key。"
            )
    connection.update(model=entry["model"], voice=entry["voice"])
    return connection


def validate_draft(current, patch, clear_api_key=False, source_profile_id=None):
    """Apply only schema fields, with strict types, bounds and semantic checks."""
    if not isinstance(patch, dict) or set(patch) - set(SCHEMA):
        raise TTSError("设置包含未知分类或格式错误。")
    if len(json.dumps(patch, ensure_ascii=False).encode()) > 128 * 1024:
        raise TTSError("设置内容超过 128 KiB 限制。")
    result = snapshot(current)
    if source_profile_id is not None:
        matches = [p for p in profile_entries(current) if p["id"] == source_profile_id]
        if len(matches) != 1:
            raise TTSError("配置文件不存在，请重新加载。")
        result.update(copy.deepcopy(matches[0]["config"]))
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
    c["model"] = model_id(c["model"])
    if c["voice"] and not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", c["voice"]):
        raise TTSError("音色 ID 格式不正确。")
    result["voice_library"]["entries_json"] = json.dumps(
        parse_saved_voices(result["voice_library"]["entries_json"]),
        ensure_ascii=False,
        indent=2,
    )
    parse_dictionary(result["pronunciation"]["dictionary_json"])
    audio = compose_settings(result["audio"], result["pronunciation"])
    build_payload(c["model"], c["voice"] or "validation", "验证", audio)
    if not re.fullmatch(r"[A-Za-z0-9]{1,10}", result["cloning"]["prefix"]):
        raise TTSError("音色前缀需为 1～10 位英文字母或数字。")
    return result


def profile_config(config):
    """A profile owns every setting, excluding the shared profile/legacy indexes."""
    return {
        k: v for k, v in snapshot(config).items()
        if k not in ("profiles", "voice_library")
    }


def profile_entries(config):
    cfg = snapshot(config)
    raw = cfg["profiles"]["entries_json"]
    try:
        entries = json.loads(raw)
    except (ValueError, TypeError):
        raise TTSError("配置文件列表格式错误。") from None
    if (
        not isinstance(entries, list) or len(entries) > 200
        or len(raw.encode("utf8")) > 8 * 1024 * 1024
    ):
        raise TTSError("配置文件最多 200 项，总大小不能超过 8 MiB。")
    seen = set()
    for entry in entries:
        if (
            not isinstance(entry, dict) or set(entry) != {"id", "name", "config"}
            or not isinstance(entry["id"], str) or not entry["id"]
            or entry["id"] in seen or not isinstance(entry["name"], str)
            or not 1 <= len(entry["name"].strip()) <= 80
            or not isinstance(entry["config"], dict)
            or set(entry["config"]) - set(profile_config(cfg))
        ):
            raise TTSError("配置文件的标识、名称或设置格式错误。")
        seen.add(entry["id"])
        # Schema projection also supplies fields added by later plugin versions.
        entry["config"] = profile_config(entry["config"])
    if entries:
        return entries
    entries = [{"id": "default", "name": "默认配置", "config": profile_config(cfg)}]
    for index, voice in enumerate(parse_saved_voices(cfg["voice_library"]["entries_json"])):
        migrated = profile_config(cfg)
        connection = migrated["connection"]
        connection.update(model=voice["model"], voice=voice["voice"])
        if voice["workspace"] and voice["workspace"] != connection["workspace"]:
            connection.update(workspace=voice["workspace"], api_key="")
        entries.append(
            {"id": f"legacy-{index}", "name": voice["name"], "config": migrated}
        )
    return entries


def save_profile(current, candidate, profile_id, name):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise TTSError("配置文件名称需为 1～80 字。")
    if not isinstance(profile_id, str):
        raise TTSError("配置文件标识格式错误。")
    entries = profile_entries(current)
    if profile_id and not any(p["id"] == profile_id for p in entries):
        raise TTSError("配置文件不存在，请重新加载。")
    if any(p["name"] == name.strip() and p["id"] != profile_id for p in entries):
        raise TTSError("配置文件名称已存在，请使用其他名称。")
    identity = profile_id or uuid.uuid4().hex
    entry = {"id": identity, "name": name.strip(), "config": profile_config(candidate)}
    entries = [entry if p["id"] == identity else p for p in entries]
    if not profile_id:
        entries.append(entry)
    candidate["profiles"] = {
        "active_id": identity,
        "entries_json": json.dumps(entries, ensure_ascii=False),
    }
    profile_entries(candidate)
    return candidate
