# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""Bounded WAV validation and single-attempt, journaled voice enrollment."""

import asyncio
import base64
import hashlib
import json
import os
import re
import struct
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .tts_client import NoRedirect, TTSError, number, workspace_base

MAX_AUDIO = 10 * 1024 * 1024
MAX_MULTIPART = MAX_AUDIO + 64 * 1024


def validate_wav(data):
    """Accept bounded PCM16 WAV only; strip all non-audio chunks before upload."""
    if not 44 <= len(data) <= MAX_AUDIO:
        raise TTSError("WAV 文件需大于 44 字节且不超过 10 MiB。")
    if (
        data[:4] != b"RIFF"
        or data[8:12] != b"WAVE"
        or struct.unpack_from("<I", data, 4)[0] + 8 != len(data)
    ):
        raise TTSError("文件不是完整的 RIFF/WAVE，或文件头长度与实际不符。")
    position, chunks = 12, {}
    count = 0
    while position < len(data):
        count += 1
        if count > 256 or position + 8 > len(data):
            raise TTSError("WAV 数据块数量过多或结构无效。")
        name = data[position : position + 4]
        size = struct.unpack_from("<I", data, position + 4)[0]
        start, end = position + 8, position + 8 + size
        if end > len(data):
            raise TTSError("WAV 数据块被截断。")
        if name in (b"fmt ", b"data"):
            if name in chunks:
                raise TTSError("WAV 含重复的格式或音频块。")
            chunks[name] = data[start:end]
        position = end + (size & 1)
    fmt, pcm = chunks.get(b"fmt ", b""), chunks.get(b"data", b"")
    if len(fmt) < 16 or not pcm:
        raise TTSError("WAV 缺少格式或音频数据。")
    codec, channels, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", fmt)
    if (
        codec != 1
        or bits != 16
        or channels not in (1, 2)
        or not 16000 <= rate <= 192000
    ):
        raise TTSError("请使用 16-bit PCM、单/双声道、16～192 kHz 的 WAV。")
    if align != channels * 2 or byte_rate != rate * align or len(pcm) % align:
        raise TTSError("WAV 帧长度或采样信息不一致。")
    seconds = len(pcm) / byte_rate
    if not 5 <= seconds <= 60:
        raise TTSError("参考音频时长需在 5～60 秒内，推荐 10～20 秒清晰人声。")
    # This is a silence check, not speech recognition or speaker verification.
    if not any(
        pcm[offset : offset + 2] != b"\0\0" for offset in range(0, len(pcm), align)
    ):
        raise TTSError("音频首声道完全静音，请上传清晰人声。")
    fmt = fmt[:16]
    clean = (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<I", 16)
        + fmt
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )
    return clean, {
        "seconds": round(seconds, 3),
        "sample_rate": rate,
        "channels": channels,
        "bytes": len(clean),
    }


def enrollment_http(connection, action, **fields):
    """Call a fixed HTTPS endpoint once; never retry a possibly billed create."""
    if action not in ("create_voice", "query_voice", "list_voice"):
        raise TTSError("不支持的音色操作。")
    key = connection.get("api_key", "").strip()
    if not key:
        raise TTSError("请先保存 API Key 和业务空间域名。")
    endpoint = (
        workspace_base(connection.get("workspace", ""))
        + "/services/audio/tts/customization"
    )
    payload = {"model": "voice-enrollment", "input": {"action": action, **fields}}
    request = Request(
        endpoint,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        with build_opener(NoRedirect()).open(
            request, timeout=number(connection.get("timeout", 60), 5, 300, "超时")
        ) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("oversized response")
        result = json.loads(raw)
        if (
            not isinstance(result, dict)
            or result.get("code")
            or not isinstance(result.get("output"), dict)
        ):
            raise ValueError("invalid response")
        return result["output"]
    except HTTPError as exc:
        # Service errors can echo audio or credentials: report only the HTTP code.
        raise TTSError(
            f"云端音色接口返回 HTTP {exc.code}，请在百炼控制台检查权限或参数。"
        ) from None
    except (URLError, OSError, TimeoutError, ValueError):
        raise TTSError(
            "云端连接失败或响应无效；创建结果可能未知，请勿直接重复创建。"
        ) from None


def account_scope(connection):
    base = workspace_base(connection.get("workspace", ""))
    return hashlib.sha256(
        (base + "\0" + connection.get("api_key", "")).encode()
    ).hexdigest()


class EnrollmentManager:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.journal = self.root / "jobs.json"
        self.uploads = {}
        self.tasks = set()
        self.query_at = {}
        try:
            self.jobs = (
                json.loads(self.journal.read_text(encoding="utf8"))
                if self.journal.exists()
                else {}
            )
            if not isinstance(self.jobs, dict):
                raise TypeError("Invalid job journal")
        except (TypeError, ValueError, OSError):
            # Failing closed preserves duplicate-creation protection and daily quotas.
            raise TTSError(
                "音色任务记录损坏，已停止复刻；请先备份并修复记录。"
            ) from None
        changed = False
        for job in self.jobs.values():
            if job["status"] == "CREATING":
                job["status"] = "UNKNOWN"
                changed = True
        for path in self.root.glob("upload_*.wav"):
            if not path.is_symlink():
                path.unlink(missing_ok=True)
        if changed:
            self.persist()

    def persist(self):
        target = self.root / ("jobs_" + uuid.uuid4().hex + ".tmp")
        try:
            with target.open("x", encoding="utf8") as writer:
                json.dump(self.jobs, writer, ensure_ascii=False)
            target.replace(self.journal)
        finally:
            target.unlink(missing_ok=True)

    def purge(self):
        for token, item in list(self.uploads.items()):
            if item["expires"] < time.time():
                item["path"].unlink(missing_ok=True)
                del self.uploads[token]

    def add_upload(self, owner, connection, clean, info):
        self.purge()
        scope = account_scope(connection)
        if len(self.uploads) >= 3:
            raise TTSError("暂存录音已达上限，请先创建音色或等待 15 分钟后重试。")
        token = uuid.uuid4().hex
        path = self.root / f"upload_{token}.wav"
        try:
            with os.fdopen(
                os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
            ) as writer:
                writer.write(clean)
        except OSError:
            path.unlink(missing_ok=True)
            raise
        self.uploads[token] = {
            "owner": owner,
            "account": scope,
            "path": path,
            "expires": time.time() + 900,
            "info": info,
        }
        return {"upload_id": token, **info, "expires_in": 900}

    def public_job(self, job):
        return {
            key: job.get(key)
            for key in (
                "job_id",
                "model",
                "prefix",
                "status",
                "voice_id",
                "created",
                "error",
            )
        }

    def visible_jobs(self, owner, connection):
        scope = account_scope(connection)
        return [
            self.public_job(job)
            for job in sorted(self.jobs.values(), key=lambda item: -item["created"])
            if job["owner"] == owner and job["account"] == scope
        ][:100]

    def create(self, owner, connection, preferences, body):
        self.purge()
        if set(body) - {
            "upload_id",
            "model",
            "prefix",
            "language",
            "enable_preprocess",
            "enable_volume_normalization",
            "max_prompt_audio_length",
        }:
            raise TTSError("复刻请求包含未知字段。")
        token = body.get("upload_id")
        if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token):
            raise TTSError("上传标识无效。")
        scope = account_scope(connection)
        old = self.jobs.get(token)
        if old and old["owner"] == owner and old["account"] == scope:
            return self.public_job(old)
        upload = self.uploads.get(token)
        if not upload or upload["owner"] != owner or upload["account"] != scope:
            raise TTSError("录音不存在、已过期或不属于当前登录用户及账号。")
        if any(job["status"] == "CREATING" for job in self.jobs.values()):
            raise TTSError("已有音色正在创建，请等待完成。")
        prefix = body.get("prefix", preferences["prefix"])
        model = body.get("model", connection["model"])
        language = body.get("language", preferences["language"])
        if not isinstance(prefix, str) or not re.fullmatch(
            r"[A-Za-z0-9]{1,10}", prefix
        ):
            raise TTSError("前缀需为 1～10 位英文字母或数字。")
        if (
            not isinstance(model, str)
            or not re.fullmatch(r"qwen-audio-[A-Za-z0-9.-]{1,100}", model)
            or "-tts-" not in model
        ):
            raise TTSError("请输入 Qwen-Audio-TTS 目标模型 ID。")
        if language not in ("auto", "zh", "en", "ja"):
            raise TTSError("参考语言不支持。")
        options = {}
        for key in ("enable_preprocess", "enable_volume_normalization"):
            value = body.get(key, preferences[key])
            if type(value) is not bool:
                raise TTSError("预处理选项必须为布尔值。")
            options[key] = (
                str(value).lower() if key == "enable_volume_normalization" else value
            )
        options["max_prompt_audio_length"] = number(
            body.get("max_prompt_audio_length", preferences["max_prompt_audio_length"]),
            3,
            30,
            "参考时长",
        )
        now = time.time()
        today = datetime.now(UTC).date().isoformat()
        if sum(job["date"] == today for job in self.jobs.values()) >= number(
            preferences["daily_limit"], 1, 100, "每日上限", True
        ):
            raise TTSError("今天的音色创建次数已达上限。")
        if now - max(
            (job["created"] for job in self.jobs.values()), default=0
        ) < number(preferences["cooldown_seconds"], 10, 3600, "创建冷却"):
            raise TTSError("创建过于频繁，请稍后重试。")
        if len(self.jobs) >= 5000:
            raise TTSError("任务记录已达上限，请由管理员归档后再创建。")
        job = {
            "job_id": token,
            "owner": owner,
            "account": scope,
            "model": model,
            "prefix": prefix,
            "status": "CREATING",
            "created": now,
            "date": today,
            "voice_id": None,
        }
        self.jobs[token] = job
        try:
            self.persist()  # Must be durable before the billable request starts.
        except OSError:
            del self.jobs[token]
            raise TTSError("无法保存任务记录，未调用云端创建。") from None
        del self.uploads[token]
        task = asyncio.create_task(
            self._create_job(job, upload["path"], dict(connection), language, options)
        )
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return self.public_job(job)

    async def _create_job(self, job, path, connection, language, options):
        try:
            audio = "data:audio/wav;base64," + base64.b64encode(
                path.read_bytes()
            ).decode("ascii")
            fields = {
                "audio": audio,
                "target_model": job["model"],
                "prefix": job["prefix"],
                **options,
            }
            if language != "auto":
                fields["language_hints"] = [language]
            result = await asyncio.to_thread(
                enrollment_http, connection, "create_voice", **fields
            )
            voice = result.get("voice_id", "")
            if (
                not isinstance(voice, str)
                or not voice.startswith(job["model"] + "-")
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", voice)
            ):
                raise TTSError("云端未返回有效音色 ID，请在百炼控制台核查。")
            job.update(voice_id=voice, status="DEPLOYING")
        except (Exception, asyncio.CancelledError):  # noqa: BLE001 - A possibly billed attempt must never be retried automatically.
            job.update(
                status="UNKNOWN",
                error="创建结果未知，请在云端核查；同一上传不会再次提交创建。",
            )
        finally:
            path.unlink(missing_ok=True)
            self.persist()

    async def query(self, owner, connection, job_id):
        if not isinstance(job_id, str):
            raise TTSError("任务标识格式错误。")
        job = self.jobs.get(job_id)
        if (
            not job
            or job["owner"] != owner
            or job["account"] != account_scope(connection)
        ):
            raise TTSError("任务不存在或无权访问。")
        if not job.get("voice_id"):
            return self.public_job(job)
        now = time.monotonic()
        if now - self.query_at.get(job_id, -1e12) < 3:
            raise TTSError("查询过于频繁，请稍后重试。")
        self.query_at[job_id] = now
        result = await asyncio.to_thread(
            enrollment_http, dict(connection), "query_voice", voice_id=job["voice_id"]
        )
        if result.get("target_model") != job["model"]:
            raise TTSError("云端返回的音色模型与任务不匹配。")
        status = result.get("status")
        if status not in ("OK", "DEPLOYING", "UNDEPLOYED"):
            raise TTSError("云端返回未知状态，请在控制台核查。")
        job["status"] = status
        self.persist()
        return self.public_job(job)

    async def close(self):
        if self.tasks:
            # Cancellation never retries create; the journal records UNKNOWN.
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
        for item in self.uploads.values():
            item["path"].unlink(missing_ok=True)
        self.uploads.clear()
