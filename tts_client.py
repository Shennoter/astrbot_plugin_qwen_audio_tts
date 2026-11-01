# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""Dependency-free client for the workspace-specific non-streaming TTS API."""

import json
import math
import re
import struct
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


class TTSError(RuntimeError):
    """Represent a configuration or synthesis failure safe to show to users."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Reject redirects of requests containing an API credential.

        Returns:
            None, causing urllib to reject the redirect.
        """
        return


def workspace_base(value):
    """Validate a workspace ID or domain and return its HTTPS API base.

    Args:
        value: Workspace ID, domain, or API base URL.

    Returns:
        The normalized API base URL.

    Raises:
        TTSError: If the workspace address is invalid.
    """
    value = str(value).strip().rstrip("/")
    if re.fullmatch(r"ws-[A-Za-z0-9]+", value):
        value += ".cn-beijing.maas.aliyuncs.com"
    parsed = urlparse(value if "://" in value else "https://" + value)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.netloc != parsed.hostname
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/api/v1")
        or not re.fullmatch(
            r"ws-[a-zA-Z0-9]+\.(cn-beijing|ap-southeast-1)\.maas\.aliyuncs\.com",
            parsed.hostname or "",
        )
    ):
        raise TTSError("业务空间地址无效，请填写北京/新加坡业务空间完整域名。")
    return f"https://{parsed.hostname}/api/v1"


def number(value, low, high, label, integer=False):
    """Validate a numeric setting without silently changing its value.

    Returns:
        A validated integer or float.

    Raises:
        TTSError: If the setting is outside its documented range.
    """
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise TTSError(f"{label}必须是数字。") from None
    if (
        not math.isfinite(result)
        or not low <= result <= high
        or (integer and result != int(result))
    ):
        raise TTSError(
            f"{label}必须在 {low}～{high} 之间" + ("且为整数。" if integer else "。")
        )
    return int(result) if integer else result


def parse_hot_fix(value):
    """Validate an object or JSON string containing literal pronunciation rules.

    Returns:
        A new hot_fix mapping, with no reference to caller-owned objects.

    Raises:
        TTSError: If the value does not match the documented hot_fix structure.
    """
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise TTSError("发音修正必须是合法 JSON 对象。") from None
    if not isinstance(value, dict) or set(value) - {"pronunciation", "replace"}:
        raise TTSError("发音修正只支持 pronunciation 和 replace 两种字段。")
    result = {}
    for kind, entries in value.items():
        if not isinstance(entries, list):
            raise TTSError("发音修正的每个字段应是由词语映射对象组成的数组。")
        rules = []
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or not entry
                or any(
                    not isinstance(key, str)
                    or not key.strip()
                    or not isinstance(reading, str)
                    or not reading.strip()
                    for key, reading in entry.items()
                )
            ):
                raise TTSError("发音修正的词语和读音必须是非空字符串。")
            rules.append(dict(entry))
        result[kind] = rules
    return result


def model_id(value):
    """Keep model names open to old and future API models; require a value only."""
    if not isinstance(value, str) or not value.strip():
        raise TTSError("请填写语音模型 ID。")
    return value.strip()


def build_payload(model, voice, text, settings, language=None, instruction=None):
    """Build a validated request using the published non-streaming parameters.

    Args:
        model: User-supplied model ID for the workspace HTTP TTS API.
        voice: System or cloned voice ID bound to that model.
        text: Original text to read without translation.
        settings: Audio parameter mapping from plugin configuration.
        language: Optional per-request language code overriding the default.
        instruction: Optional per-request instruction overriding the default.

    Returns:
        The API request body.

    Raises:
        TTSError: If a model, voice, or setting is invalid.
    """
    model = model_id(model)
    if not voice or not text.strip():
        raise TTSError("音色和合成文字不能为空。")
    fmt = settings.get("format", "wav")
    if fmt not in ("wav", "mp3", "opus"):
        raise TTSError(
            "输出格式请选择 wav、mp3 或 opus；原始 PCM 无法直接作为语音文件发送。"
        )
    rate = number(settings.get("sample_rate", 24000), 8000, 48000, "采样率", True)
    allowed_rates = (8000, 12000, 16000, 22050, 24000, 44100, 48000)
    if rate not in allowed_rates or (
        fmt == "opus" and rate not in (8000, 12000, 16000, 24000, 48000)
    ):
        raise TTSError("采样率不在所选格式支持的范围中。")
    inp = {
        "text": text,
        "voice": voice,
        "format": fmt,
        "sample_rate": rate,
        "volume": number(settings.get("volume", 50), 0, 100, "音量", True),
        "rate": number(settings.get("rate", 1.0), 0.5, 2, "语速"),
        "pitch": number(settings.get("pitch", 1.0), 0.5, 2, "音调"),
        "enable_ssml": bool(settings.get("enable_ssml", False)),
    }
    seed = number(settings.get("seed", -1), -1, 65535, "随机种子", True)
    if seed >= 0:
        inp["seed"] = seed
    if fmt == "opus":
        inp["bit_rate"] = number(
            settings.get("bit_rate", 32), 6, 510, "Opus 码率", True
        )
    language = language or settings.get("language", "auto")
    if language not in ("auto", "zh", "en", "ja"):
        raise TTSError("语言请选择 auto、zh、en 或 ja。")
    if language != "auto":
        inp["language_hints"] = [language]
    instruction = (
        settings.get("instruction", "") if instruction is None else instruction
    )
    if str(instruction).strip():
        inp["instruction"] = str(instruction).strip()
    hot_fix = settings.get("hot_fix_json", "")
    if hot_fix:
        inp["hot_fix"] = parse_hot_fix(hot_fix)
    if settings.get("enable_aigc_tag", False):
        inp["enable_aigc_tag"] = True
        for field in ("aigc_propagator", "aigc_propagate_id"):
            if str(settings.get(field, "")).strip():
                inp[field] = str(settings[field]).strip()
    return {"model": model, "input": inp}


def normalize_audio(data, fmt):
    """Validate audio and repair WAV placeholder lengths without removing metadata.

    Returns:
        Audio bytes with actual WAV lengths, or unchanged MP3/Opus bytes.

    Raises:
        TTSError: If the response is empty, incomplete, or not the requested audio.
    """
    if not data:
        raise TTSError("服务返回空音频。")
    if fmt == "wav":
        if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            raise TTSError("下载内容不是 WAV 音频。")
        fixed = bytearray(data)
        struct.pack_into("<I", fixed, 4, len(data) - 8)
        offset, found = 12, False
        while offset + 8 <= len(fixed):
            size = struct.unpack_from("<I", fixed, offset + 4)[0]
            available = len(fixed) - offset - 8
            if fixed[offset : offset + 4] == b"data":
                if not size or not available:
                    raise TTSError("WAV 不包含有效音频。")
                if size > available:
                    struct.pack_into("<I", fixed, offset + 4, available)
                found = True
                break
            if size > available:
                raise TTSError("WAV 文件不完整。")
            offset += 8 + size + size % 2
        if not found:
            raise TTSError("WAV 缺少音频数据块。")
        return bytes(fixed)
    if fmt == "mp3" and not (
        data[:3] == b"ID3" or data[0] == 255 and len(data) > 1 and data[1] & 224 == 224
    ):
        raise TTSError("下载内容不是 MP3 音频。")
    if fmt == "opus" and not (data[:4] == b"OggS" and b"OpusHead" in data[:512]):
        raise TTSError("下载内容不是 Ogg Opus 音频。")
    return data


def synthesize_http(base, api_key, payload, timeout=60):
    """Synthesize once and download audio without forwarding API credentials.

    Args:
        base: Validated workspace API base URL.
        api_key: The plugin configuration's API key.
        payload: Validated API request body.
        timeout: Timeout in seconds for each network operation.

    Returns:
        Validated audio bytes.

    Raises:
        TTSError: If the service request or audio download fails.
    """
    if not api_key.strip():
        raise TTSError("请在插件的语音服务配置中填写 API Key。")
    request = Request(
        base + "/services/audio/tts/SpeechSynthesizer",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "User-Agent": "AstrBot-Qwen-Audio-Plugin/0.9.1",
        },
        method="POST",
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            data = json.loads(response.read())
    except HTTPError as exc:
        try:
            error = json.loads(exc.read())
        except ValueError:
            error = {}
        code = str(error.get("code", "HTTPError"))
        message = str(error.get("message", "接口请求失败"))
        raise TTSError(
            f"HTTP {exc.code} {code}: {message}".replace(api_key, "[REDACTED]")[:800]
        ) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise TTSError(
            "语音服务连接失败或返回无效数据，请检查业务空间、网络和超时设置。"
        ) from None
    if (
        not isinstance(data, dict)
        or data.get("code")
        or not isinstance(data.get("output"), dict)
    ):
        raise TTSError("语音合成未返回有效 output，请检查模型、音色和账户权限。")
    audio = data["output"].get("audio") or {}
    parsed = urlparse(audio.get("url", ""))
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise TTSError("合成结果缺少有效音频下载链接。")
    url = parsed._replace(scheme="https").geturl()
    # The object-storage download has no Authorization header or logged signed URL.
    try:
        with build_opener().open(Request(url), timeout=timeout) as response:
            content = response.read()
    except (HTTPError, URLError, TimeoutError, OSError):
        raise TTSError("音频已合成，但下载失败；请检查网络。") from None
    return normalize_audio(content, payload["input"]["format"])


def models_url(base):
    """Return the OpenAI-compatible model list URL for a validated API base.

    Args:
        base: Validated workspace API base URL ending in ``/api/v1``.

    Returns:
        The model list URL sharing the workspace hostname.
    """
    return f"https://{urlparse(base).hostname}/v1/models"


def _model_entries(body):
    """Extract model entries from an OpenAI-style or bare list response.

    Returns:
        A list of ``(id, owned_by)`` string pairs, possibly empty.
    """
    entries = body.get("data") if isinstance(body, dict) else body
    if not isinstance(entries, list):
        return []
    result = []
    for item in entries:
        name = item.get("id") if isinstance(item, dict) else item
        if isinstance(name, str) and name.strip():
            owner = item.get("owned_by") if isinstance(item, dict) else ""
            result.append((name.strip()[:120], str(owner or "")[:60]))
    return result


def list_models(base, api_key, timeout=20):
    """List the model ids the configured credential can reach.

    The synthesis route lives under ``/api/v1`` while gateways usually expose the
    catalogue on the OpenAI-compatible ``/v1/models`` route, so both orders are
    tried. This is a convenience lookup only: callers keep accepting any model id
    typed by hand.

    Args:
        base: Validated workspace API base URL.
        api_key: The plugin configuration's API key.
        timeout: Timeout in seconds for each attempt.

    Returns:
        A list of ``{"id": ..., "owned_by": ...}`` mappings sorted by id.

    Raises:
        TTSError: If no candidate endpoint returns a usable model list.
    """
    if not str(api_key).strip():
        raise TTSError("请先在插件的语音服务配置中填写 API Key。")
    headers = {
        "Authorization": "Bearer " + str(api_key).strip(),
        "Accept": "application/json",
        "User-Agent": "AstrBot-Qwen-Audio-Plugin/1.2.2",
    }
    reasons = []
    for url in (models_url(base), base.rstrip("/") + "/models"):
        try:
            request = Request(url, headers=headers)
            with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
                body = json.loads(response.read(1_000_000))
        except HTTPError as exc:
            reasons.append(f"HTTP {exc.code}")
            continue
        except (URLError, TimeoutError, OSError, ValueError):
            reasons.append("连接失败")
            continue
        unique = {}
        for name, owner in _model_entries(body):
            unique.setdefault(name, {"id": name, "owned_by": owner})
        if unique:
            return sorted(unique.values(), key=lambda item: item["id"])
        reasons.append("没有返回模型")
    raise TTSError(
        "无法获取模型列表（"
        + "；".join(dict.fromkeys(reasons))
        + "），请手动填写模型 ID。"
    )


def _post_json(url, api_key, payload, timeout, failure):
    """POST JSON with the plugin credential and return the parsed mapping.

    Args:
        url: Absolute request URL.
        api_key: The plugin configuration's API key.
        payload: JSON-serialisable request body.
        timeout: Timeout in seconds for the request.
        failure: User-facing message for a transport or shape failure.

    Returns:
        The decoded response mapping.

    Raises:
        TTSError: If the request fails or the response is not a usable mapping.
    """
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + str(api_key).strip(),
            "Content-Type": "application/json",
            "User-Agent": "AstrBot-Qwen-Audio-Plugin/1.2.3",
        },
        method="POST",
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            data = json.loads(response.read(2_000_000))
    except HTTPError as exc:
        try:
            error = json.loads(exc.read())
        except ValueError:
            error = {}
        code = str(error.get("code", "HTTPError"))
        message = str(error.get("message", "接口请求失败"))
        raise TTSError(
            f"HTTP {exc.code} {code}: {message}".replace(
                str(api_key), "[REDACTED]"
            )[:800]
        ) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise TTSError(failure) from None
    if not isinstance(data, dict) or data.get("code"):
        raise TTSError(failure)
    return data


def list_voices(base, api_key, timeout=20, limit=200):
    """List the voice replicas the API created for this workspace.

    ``services/audio/tts/customization`` is the only documented way to enumerate
    voices; pages are read until the reported total is reached.

    Args:
        base: Validated workspace API base URL.
        api_key: The plugin configuration's API key.
        timeout: Timeout in seconds for each page request.
        limit: Maximum number of voices to collect.

    Returns:
        A list of ``{"voice_id": ..., "target_model": ..., "status": ...}``.

    Raises:
        TTSError: If a page cannot be read or the payload has an unexpected shape.
    """
    if not str(api_key).strip():
        raise TTSError("请先在插件的语音服务配置中填写 API Key。")
    page_size = 50
    voices = []
    for page in range(max(1, (limit + page_size - 1) // page_size)):
        data = _post_json(
            base + "/services/audio/tts/customization",
            api_key,
            {
                "model": "voice-enrollment",
                "input": {
                    "action": "list_voice",
                    "page_index": page,
                    "page_size": page_size,
                },
            },
            timeout,
            "音色列表返回无效数据，请检查业务空间、网络和超时设置。",
        )
        output = data.get("output")
        entries = output.get("voice_list") if isinstance(output, dict) else None
        if not isinstance(entries, list):
            raise TTSError("音色列表返回格式无效，请检查账户权限。")
        for item in entries:
            name = item.get("voice_id") if isinstance(item, dict) else None
            if isinstance(name, str) and name.strip():
                voices.append(
                    {
                        "voice_id": name.strip()[:120],
                        "target_model": str(item.get("target_model", ""))[:80],
                        "status": str(item.get("status", ""))[:20],
                    }
                )
        total = output.get("total_count")
        if len(entries) < page_size or not isinstance(total, int):
            break
        if len(voices) >= min(total, limit):
            break
    return voices[:limit]
