# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""Dashboard settings and preview endpoints, using isolated request snapshots."""

import asyncio
import base64
import copy
import re
import time
from pathlib import Path

from astrbot.api import logger

from .delivery import parse_plan
from .voices import default_pair, speech_connection
from .enrollment import MAX_AUDIO, MAX_MULTIPART, EnrollmentManager, validate_wav
from .settings import public_settings, revision, snapshot, validate_draft, save_profile
from .tts_client import TTSError


class PanelMixin:
    def _enrollment(self):
        if getattr(self, "_enrollment_manager", None) is None:
            from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

            root = (
                Path(get_astrbot_plugin_data_path())
                / "astrbot_plugin_qwen_audio_tts"
                / "enrollment"
            )
            self._enrollment_manager = EnrollmentManager(root)
        return self._enrollment_manager

    async def _persona_names(self):
        """List AstrBot persona ids so the voice rules editor can offer them."""
        manager = getattr(self.context, "persona_manager", None)
        getter = getattr(manager, "get_all_personas", None)
        if getter is None:
            return []
        try:
            entries = await getter()
        except Exception as exc:  # noqa: BLE001 - The editor still accepts manual ids.
            logger.debug("Persona list unavailable (%s).", type(exc).__name__)
            return []
        names = []
        for entry in entries or ():
            name = (
                entry.get("persona_id")
                if isinstance(entry, dict)
                else getattr(entry, "persona_id", None)
            )
            if isinstance(name, str) and name and name not in names:
                names.append(name)
        return names

    def _clone_connection(self):
        if self._closed:
            raise TTSError("插件已停用。")
        cfg = snapshot(self.config)
        if not cfg["connection"]["api_key"] or not cfg["connection"]["workspace"]:
            raise TTSError("请先保存 API Key 和业务空间完整域名。")
        # Enrollment logs the model, so expose the resolved default pair here.
        model, voice = default_pair(cfg)
        cfg["connection"].update(model=model, voice=voice)
        return cfg

    async def cloning_upload(self):
        web = self._web
        if not web.request.username:
            return web.error_response("请先登录 AstrBot 管理面板。", status_code=401)
        files = None
        self._upload_busy = getattr(self, "_upload_busy", False)
        if self._upload_busy:
            return web.error_response("正在处理上传，请稍后重试。", status_code=429)
        try:
            cfg = self._clone_connection()
            headers = web.request.headers
            length = headers.get("content-length", "")
            if not re.fullmatch(r"[0-9]{1,10}", length) or headers.get(
                "transfer-encoding"
            ):
                return web.error_response(
                    "上传需要有效 Content-Length，且不支持分块传输。", status_code=411
                )
            if not 44 <= int(length) <= MAX_MULTIPART:
                return web.error_response(
                    "上传请求超过 10 MiB 文件限制。", status_code=413
                )
            if (
                not headers.get("content-type", "")
                .lower()
                .startswith("multipart/form-data;")
            ):
                raise TTSError("请通过文件上传接口提交 WAV。")
            self._upload_busy = True
            manager = self._enrollment()
            manager.purge()
            if len(manager.uploads) >= 3:
                return web.error_response(
                    "暂存文件已满，请先完成复刻或稍后重试。", status_code=429
                )
            # Check before multipart parsing. AstrBot's Quart compatibility layer
            # may already have buffered the body; gateway limits remain necessary.
            files = await web.request.files()
            if list(files.keys()) != ["file"] or len(files.getlist("file")) != 1:
                raise TTSError("每次只允许上传一个 file 字段。")
            upload = files.get("file")
            if not isinstance(upload, web.PluginUploadFile) or not str(
                upload.filename or ""
            ).lower().endswith(".wav"):
                raise TTSError("只允许上传 .wav 文件。")
            chunks, size = [], 0
            while True:
                part = await upload.read(64 * 1024)
                if not part:
                    break
                size += len(part)
                if size > MAX_AUDIO:
                    return web.error_response("WAV 文件超过 10 MiB。", status_code=413)
                chunks.append(part)
            clean, info = await asyncio.to_thread(validate_wav, b"".join(chunks))
            result = manager.add_upload(
                web.request.username, cfg["connection"], clean, info
            )
            return web.json_response(result)
        except Exception as exc:  # noqa: BLE001 - Return safe errors at the authenticated API boundary.
            return self._panel_error(exc)
        finally:
            if files:
                await asyncio.gather(
                    *(
                        upload.close()
                        for key in files.keys()  # noqa: SIM118 - PluginMultiDict is not iterable.
                        for upload in files.getlist(key)
                        if isinstance(upload, web.PluginUploadFile)
                    ),
                    return_exceptions=True,
                )
            self._upload_busy = False

    async def cloning_create(self):
        web = self._web
        if not web.request.username:
            return web.error_response("请先登录 AstrBot 管理面板。", status_code=401)
        try:
            cfg = self._clone_connection()
            body = await web.request.json(default={})
            if not isinstance(body, dict):
                raise TTSError("复刻请求格式错误。")
            return web.json_response(
                self._enrollment().create(
                    web.request.username, cfg["connection"], cfg["cloning"], body
                )
            )
        except Exception as exc:  # noqa: BLE001 - Return safe errors at the authenticated API boundary.
            return self._panel_error(exc)

    async def cloning_jobs(self):
        web = self._web
        if not web.request.username:
            return web.error_response("请先登录 AstrBot 管理面板。", status_code=401)
        try:
            cfg = self._clone_connection()
            return web.json_response(
                {
                    "jobs": self._enrollment().visible_jobs(
                        web.request.username, cfg["connection"]
                    )
                }
            )
        except Exception as exc:  # noqa: BLE001 - Return safe errors at the authenticated API boundary.
            return self._panel_error(exc)

    async def cloning_query(self):
        web = self._web
        if not web.request.username:
            return web.error_response("请先登录 AstrBot 管理面板。", status_code=401)
        try:
            cfg = self._clone_connection()
            body = await web.request.json(default={})
            if not isinstance(body, dict) or set(body) != {"job_id"}:
                raise TTSError("查询请求格式错误。")
            result = await self._enrollment().query(
                web.request.username, cfg["connection"], body["job_id"]
            )
            return web.json_response(result)
        except Exception as exc:  # noqa: BLE001 - Return safe errors at the authenticated API boundary.
            return self._panel_error(exc)

    def _panel_error(self, exc, keys=()):
        if isinstance(exc, TTSError):
            message = str(exc)
            for key in (self.config.get("connection", {}).get("api_key", ""), *keys):
                if key:
                    message = message.replace(key, "[已隐藏]")
            return self._web.error_response(message[:800], status_code=400)
        logger.warning("TTS dashboard request failed (%s).", type(exc).__name__)
        return self._web.error_response(
            "操作失败，请检查设置、网络或服务端日志。", status_code=502
        )

    async def preview_settings(self):
        web = self._web
        if not web.request.username:
            return web.error_response("请先登录 AstrBot 管理面板。", status_code=401)
        public = public_settings(self.config)
        cfg = public["config"]
        model, voice = default_pair(cfg)
        return web.json_response(
            {
                **public,
                "providers": [
                    {"id": p.meta().id} for p in self.context.get_all_providers()
                ]
                if hasattr(self.context, "get_all_providers")
                else [],
                "enabled": cfg["preview"]["enabled"],
                "text": cfg["preview"]["text"],
                "language": cfg["preview"]["language"],
                "max_chars": cfg["reply"]["max_chars"],
                "personas": await self._persona_names(),
                "model": model,
                "voice": voice,
                "configured": bool(
                    public["api_key_set"] and cfg["connection"]["workspace"] and voice
                ),
                "audio": cfg["audio"],
            }
        )

    async def save_settings(self):
        web = self._web
        if not web.request.username:
            return web.error_response("请先登录 AstrBot 管理面板。", status_code=401)
        try:
            body = await web.request.json(default={})
            if not isinstance(body, dict) or set(body) - {
                "config",
                "revision",
                "clear_api_key",
                "source_profile_id",
                "profile_id",
                "profile_name",
            }:
                raise TTSError("设置请求格式错误。")
            if body.get("revision") != revision(self.config):
                return web.error_response(
                    "设置已被其他页面或聊天修改，请重新加载后再保存。", status_code=409
                )
            candidate = validate_draft(
                self.config, body.get("config"), body.get("clear_api_key", False),
                body.get("source_profile_id")
            )
            # The shared index is server-owned; never trust a redacted browser copy.
            candidate["profiles"] = snapshot(self.config)["profiles"]
            if "profile_id" in body:
                candidate = save_profile(self.config, candidate, body["profile_id"], body.get("profile_name"))
            previous = copy.deepcopy(dict(self.config))
            old_limit = previous.get("connection", {}).get("max_concurrent", 2)
            self.config.clear()
            self.config.update(candidate)
            try:
                self.config.save_config()
            except Exception:  # noqa: BLE001 - Restore the complete configuration on any save failure.
                self.config.clear()
                self.config.update(previous)
                raise TTSError("保存失败，已恢复原设置。") from None
            if candidate["connection"]["max_concurrent"] != old_limit:
                self._semaphore = asyncio.Semaphore(
                    candidate["connection"]["max_concurrent"]
                )
            return web.json_response(public_settings(self.config))
        except Exception as exc:  # noqa: BLE001 - Return safe errors at the authenticated API boundary.
            return self._panel_error(exc)

    async def preview_generate(self):
        web = self._web
        if not web.request.username:
            return web.error_response("请先登录 AstrBot 管理面板。", status_code=401)
        keys = []
        try:
            body = await web.request.json(default={})
            if not isinstance(body, dict) or set(body) - {
                "text",
                "language",
                "config",
                "clear_api_key",
                "source_profile_id",
                "delivery_instruction",
                "delivery_plan",
            }:
                raise TTSError("试听请求只支持 text、language 和设置草稿。")
            cfg = snapshot(self.config)
            if "config" in body:
                cfg = validate_draft(
                    cfg, body["config"], body.get("clear_api_key", False),
                    body.get("source_profile_id")
                )
            keys.append(cfg["connection"]["api_key"])
            preview = cfg["preview"]
            text = body.get("text", preview["text"])
            language = body.get("language", preview["language"])
            if not isinstance(text, str) or not text.strip():
                raise TTSError("请输入要试听的文字。")
            if language not in ("auto", "zh", "en", "ja"):
                raise TTSError("试听语言必须是 auto、zh、en 或 ja。")
            if self._preview_busy:
                return web.error_response("试听正在生成，请等待完成。", status_code=429)
            now = time.monotonic()
            self._preview_busy = True
            self._web_preview_at = now
            try:
                code = cfg["audio"]["language"] if language == "auto" else language
                plan = parse_plan(
                    body.get("delivery_plan"), text.strip(), cfg["delivery"]
                )
                models = list(dict.fromkeys(
                    speech_connection(cfg, item["text"], code)[1].get("model", "")
                    for item in (plan or [{"text": text.strip()}])
                ))
                path = await self._speak(
                    text.strip(),
                    language=code,
                    config_snapshot=cfg,
                    delivery_instruction=body.get("delivery_instruction"),
                    delivery_plan=body.get("delivery_plan"),
                )
                try:
                    mime = {
                        ".wav": "audio/wav",
                        ".mp3": "audio/mpeg",
                        ".opus": "audio/ogg",
                    }[path.suffix]
                    return web.json_response(
                        {
                            "audio_base64": base64.b64encode(path.read_bytes()).decode(
                                "ascii"
                            ),
                            "mime_type": mime,
                            "filename": "tts-preview" + path.suffix,
                            "model": " / ".join(models),
                            "models": models,
                            "segments": len(plan) if plan else 1,
                            "added_pause_ms": sum(
                                item.get("pause_ms", 0) for item in plan
                            )
                            if plan
                            else 0,
                        }
                    )
                finally:
                    self._discard_temporary(path)
            finally:
                self._preview_busy = False
        except Exception as exc:  # noqa: BLE001 - Return safe errors at the authenticated API boundary.
            return self._panel_error(exc, keys)
