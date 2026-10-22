# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""Standalone Qwen-Audio-TTS, probabilistic voice replies, and speech tools."""

import asyncio
import json
import os
import random
import re
import time
import uuid
from importlib import import_module
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Plain, Record, Reply
from astrbot.api.star import Context, Star, register
from astrbot.core.message.message_event_result import ResultContentType
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.utils.astrbot_path import (
    get_astrbot_plugin_data_path,
    get_astrbot_temp_path,
)

from .delivery import (
    apply_direction,
    direction,
    parse_plan,
    plan_dynamic,
    synthesize_segments,
)
from .panel import PanelMixin
from .pronunciation import compose_settings, estimated_text, parse_dictionary
from .settings import parse_saved_voices, saved_voice_connection, snapshot
from .tts_client import TTSError, build_payload, number, synthesize_http, workspace_base

LANGUAGES = {
    "zh": "zh",
    "中文": "zh",
    "汉语": "zh",
    "普通话": "zh",
    "chinese": "zh",
    "en": "en",
    "英语": "en",
    "英文": "en",
    "english": "en",
    "ja": "ja",
    "日语": "ja",
    "日文": "ja",
    "日本語": "ja",
    "japanese": "ja",
}
MARKER = "qwen_audio_voice_handled"


@register("astrbot_plugin_qwen_audio_tts", "Shennoter", "阿里云TTS助手", "0.7.0")
class QwenAudioVoicePlugin(PanelMixin, Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._last_auto = {}
        self._last_command = {}
        self._auto_busy = set()
        self._warned = set()
        self._closed = False
        self._temporary_files = set()
        self._temporary_tasks = {}
        self._cleanup_tasks = set()
        self._preview_busy = False
        self._web_preview_at = -1e12
        limit = number(
            config.get("connection", {}).get("max_concurrent", 2), 1, 8, "并发数", True
        )
        self._semaphore = asyncio.Semaphore(limit)
        self.audio_dir = Path(get_astrbot_temp_path()).resolve() / "qwen_audio_voice"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.retained_audio_dir = (
            Path(get_astrbot_plugin_data_path()).resolve()
            / "astrbot_plugin_qwen_audio_tts"
            / "audio"
        )
        self._sweep_at = 0
        for old in self.audio_dir.glob("transient_*.*"):
            if (
                not old.is_symlink()
                and old.is_file()
                and time.time() - old.stat().st_mtime > 900
            ):
                old.unlink(missing_ok=True)

    async def initialize(self):
        """Register authenticated APIs for the official plugin visual view."""
        try:
            web = import_module("astrbot.api.web")
        except ImportError:
            logger.warning(
                "音色试听页面需要支持插件可视化视图及 astrbot.api.web 的 AstrBot 版本。"
            )
            return
        self._web = web
        task = asyncio.create_task(self._cleanup_enrollment_uploads())
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)
        for endpoint, handler, methods in (
            ("preview/settings", self.preview_settings, ["GET"]),
            ("preview/generate", self.preview_generate, ["POST"]),
            ("settings/save", self.save_settings, ["POST"]),
            ("cloning/upload", self.cloning_upload, ["POST"]),
            ("cloning/create", self.cloning_create, ["POST"]),
            ("cloning/jobs", self.cloning_jobs, ["GET"]),
            ("cloning/query", self.cloning_query, ["POST"]),
        ):
            self.context.register_web_api(
                f"/astrbot_plugin_qwen_audio_tts/{endpoint}",
                handler,
                methods,
                "阿里云TTS助手",
            )

    async def _cleanup_enrollment_uploads(self):
        while not self._closed:
            await asyncio.sleep(60)
            manager = getattr(self, "_enrollment_manager", None)
            if manager:
                manager.purge()

    async def terminate(self):
        """Stop queued synthesis without modifying any built-in provider."""
        self._closed = True
        if getattr(self, "_enrollment_manager", None):
            await self._enrollment_manager.close()
        for task in self._cleanup_tasks:
            task.cancel()
        if self._cleanup_tasks:
            await asyncio.gather(*self._cleanup_tasks, return_exceptions=True)
        for path in list(self._temporary_files):
            self._discard_temporary(path)
        self._auto_busy.clear()
        self._last_auto.clear()
        self._last_command.clear()
        apis = getattr(self.context, "registered_web_apis", None)
        if isinstance(apis, list):
            apis[:] = [
                api for api in apis if getattr(api[1], "__self__", None) is not self
            ]

    def _scope_allowed(self, event):
        """Check the configured session, platform, and chat-type scope.

        Returns:
            Whether the event is eligible for this plugin.
        """
        scope = self.config.get("scope", {})
        origin = event.unified_msg_origin
        if origin in scope.get("blocked_sessions", []):
            return False
        allowed = scope.get("allowed_sessions", [])
        platforms = scope.get("platforms", [])
        chat_type = scope.get("chat_type", "全部")
        return (
            (not allowed or origin in allowed)
            and (not platforms or event.get_platform_name() in platforms)
            and (chat_type != "仅私聊" or event.is_private_chat())
            and (chat_type != "仅群聊" or not event.is_private_chat())
        )

    async def _speak(
        self,
        text,
        language=None,
        automatic=False,
        hot_fix=None,
        instruction=None,
        config_snapshot=None,
        delivery_instruction=None,
        delivery_plan=None,
    ):
        """Generate audio exclusively from the plugin's own service configuration.

        Args:
            text: Original text to read.
            language: Optional target pronunciation language code.
            automatic: Whether to disable SSML for ordinary chat replies.
            hot_fix: Request-local pronunciation rules, never saved to configuration.
            instruction: Request-local voice instruction, never saved to configuration.
            config_snapshot: Optional isolated dashboard draft for this request.
            delivery_instruction: Request-local emotion and intonation guidance.
            delivery_plan: Optional segments with exact pauses and numeric controls.

        Returns:
            Path to the generated audio file.

        Raises:
            TTSError: If configuration, limits, or synthesis fail.
        """
        if self._closed:
            raise TTSError("插件已停用。")
        cfg = snapshot(config_snapshot if config_snapshot is not None else self.config)
        if len(text) > number(cfg["reply"]["max_chars"], 1, 10000, "最大字数", True):
            raise TTSError("原文超过最大字数。")
        mode = cfg["delivery"].get("mode", "混合")
        if mode not in ("固定参数", "混合", "动态参数"):
            raise TTSError("单句朗读控制模式无效。")
        if mode == "固定参数" and (
            delivery_instruction or delivery_plan or instruction or hot_fix
        ):
            raise TTSError("固定参数模式不接受聊天中的临时参数；请在设置中调整。")
        if mode == "动态参数":
            if not cfg["connection"]["api_key"] or not cfg["connection"]["voice"]:
                raise TTSError("请先配置 API Key 和音色。")
            workspace_base(cfg["connection"]["workspace"])
            delivery_instruction, delivery_plan = await plan_dynamic(
                self.context,
                text,
                delivery_instruction or instruction or "",
                delivery_plan,
                cfg,
            )
            cfg["delivery"]["mode"] = (
                "混合"  # Isolated snapshot; avoid replanning segments.
            )
        guide = direction(delivery_instruction, cfg["delivery"])
        segments = parse_plan(delivery_plan, text, cfg["delivery"])
        if segments:
            if len(text) > number(
                cfg["reply"]["max_chars"], 1, 10000, "最大字数", True
            ):
                raise TTSError("原文超过最大字数。")
            return await synthesize_segments(
                self, text, language, hot_fix, instruction, guide, segments, cfg
            )
        connection = cfg.get("connection", {})
        settings = compose_settings(
            cfg.get("audio", {}),
            cfg.get("pronunciation", {}),
            language=language,
            hot_fix=hot_fix,
            instruction=instruction,
        )
        max_chars = number(
            cfg.get("reply", {}).get("max_chars", 200),
            1,
            10000,
            "最大字数",
            True,
        )
        if len(text) > max_chars:
            raise TTSError(
                f"文字长度 {len(text)} 超过最大字数 {max_chars}，请缩短或调整设置。"
            )
        settings = apply_direction(settings, guide, cfg["delivery"])
        if automatic:
            settings["enable_ssml"] = False
        model = str(connection.get("model", "qwen-audio-3.1-tts-plus")).strip()
        voice = str(connection.get("voice", "")).strip()
        key = str(connection.get("api_key", "")).strip()
        if not key:
            raise TTSError("请在插件的语音服务配置中填写 API Key。")
        if not voice:
            raise TTSError("请在插件的语音服务配置中填写对应模型的音色 ID。")
        base = workspace_base(connection.get("workspace", ""))
        timeout = number(connection.get("timeout", 60), 5, 300, "超时时间")
        number(connection.get("keep_hours", 24), 1, 720, "文件保留小时")
        payload = build_payload(model, voice, text, settings, language=language)
        if len(estimated_text(text, payload["input"].get("hot_fix", {}))) > max_chars:
            raise TTSError(
                "替换读音后的预计文字长度超过最大字数，请缩短词典读音或调整上限。"
            )
        async with self._semaphore:
            if self._closed:
                raise TTSError("插件已停用。")
            content = await asyncio.to_thread(
                synthesize_http, base, key, payload, timeout
            )
        return self._store_audio(content, payload["input"]["format"], connection)

    def _store_audio(self, content, fmt, connection):
        """Store one finished file with the configured retention policy."""
        if self._closed:
            raise TTSError("插件已停用。")
        keep_hours = number(connection.get("keep_hours", 24), 1, 720, "文件保留小时")
        prefix = "speech" if connection.get("save_audio", False) else "transient"
        output_dir = self.retained_audio_dir if prefix == "speech" else self.audio_dir
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = output_dir / f"{prefix}_{uuid.uuid4().hex}.{fmt}"
        with os.fdopen(
            os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
        ) as writer:
            writer.write(content)
        if prefix == "transient":
            self._temporary_files.add(path)
            task = asyncio.create_task(self._expire_temporary(path))
            self._cleanup_tasks.add(task)
            self._temporary_tasks[path] = task
            task.add_done_callback(self._cleanup_tasks.discard)
        now = time.time()
        if now >= self._sweep_at:
            self._sweep_at = now + 3600
            monotonic_now = time.monotonic()
            self._last_auto = {
                key: used
                for key, used in self._last_auto.items()
                if monotonic_now - used < 3600
            }
            self._last_command = {
                key: used
                for key, used in self._last_command.items()
                if monotonic_now - used < 3600
            }
            # Delete only files generated in this plugin's own directory.
            for old in (
                *self.audio_dir.glob("speech_*.*"),
                *self.retained_audio_dir.glob("speech_*.*"),
            ):
                if old.suffix not in (".wav", ".mp3", ".opus") or old.is_symlink():
                    continue
                try:
                    if old.is_file() and now - old.stat().st_mtime > keep_hours * 3600:
                        old.unlink()
                except OSError:
                    logger.warning("Unable to remove an expired plugin audio file.")
        return path

    async def _record(self, path, text):
        """Create a record component, optionally exposing it through file service.

        Returns:
            A platform-neutral voice message component.
        """
        record = Record.fromFileSystem(str(path))
        record.text = text
        if self.config.get("connection", {}).get("use_file_service", False):
            url = await record.register_to_file_service()
            record = Record.fromURL(url)
            record.text = text
        return record

    def _discard_temporary(self, path):
        if path in self._temporary_files:
            try:
                path.unlink(missing_ok=True)
                self._temporary_files.discard(path)
                task = self._temporary_tasks.pop(path, None)
                if task and task is not asyncio.current_task():
                    task.cancel()
            except OSError:
                logger.warning("Unable to remove temporary TTS audio.")

    async def _expire_temporary(self, path):
        await asyncio.sleep(900)
        self._discard_temporary(path)

    def _track_temporary(self, event, path):
        if path in self._temporary_files:
            paths = event.get_extra("qwen_audio_temporary_paths", [])
            event.set_extra("qwen_audio_temporary_paths", [*paths, path])

    @filter.after_message_sent()
    async def cleanup_sent_audio(self, event: AstrMessageEvent):
        paths = event.get_extra("qwen_audio_temporary_paths", [])
        event.set_extra("qwen_audio_temporary_paths", [])
        if paths and self.config.get("connection", {}).get("use_file_service", False):
            # Remote adapters can fetch the file-service URL after acknowledging send.
            async def later():
                await asyncio.sleep(60)
                for path in paths:
                    self._discard_temporary(path)

            task = asyncio.create_task(later())
            self._cleanup_tasks.add(task)
            task.add_done_callback(self._cleanup_tasks.discard)
        else:
            for path in paths:
                self._discard_temporary(path)

    @filter.on_decorating_result()
    async def random_voice_reply(self, event: AstrMessageEvent):
        """Convert an eligible complete reply once, retaining text on any failure."""
        reply = self.config.get("reply", {})
        if (
            self._closed
            or not reply.get("enabled", True)
            or not self._scope_allowed(event)
        ):
            return
        result = event.get_result()
        if not result or not result.chain or event.get_extra(MARKER, False):
            return
        if result.result_content_type in (
            ResultContentType.STREAMING_RESULT,
            ResultContentType.STREAMING_FINISH,
        ) or event.get_extra("_streaming_finished", False):
            return
        if reply.get("only_llm", True) and not result.is_llm_result():
            return
        if any(not isinstance(comp, (Plain, At, Reply)) for comp in result.chain):
            return
        core_tts = self.context.get_config(event.unified_msg_origin).get(
            "provider_tts_settings", {}
        )
        if core_tts.get("enable", False):
            if "native_tts" not in self._warned:
                self._warned.add("native_tts")
                logger.warning(
                    "Random voice replies skipped: disable AstrBot native automatic TTS to avoid duplicate synthesis."
                )
            return
        text = "\n".join(c.text for c in result.chain if isinstance(c, Plain)).strip()
        if reply.get("skip_code", True) and "```" in text:
            return
        if reply.get("clean_markdown", True):
            text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
            text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
            text = re.sub(r"https?://\S+", "", text)
            text = re.sub(r"(?m)^\s{0,3}(?:#{1,6}\s+|>\s*)", "", text)
            text = text.replace("**", "").replace("__", "").replace("`", "").strip()
        try:
            limit = number(reply.get("max_chars", 200), 1, 10000, "最大字数", True)
            probability = number(reply.get("probability", 20), 0, 100, "语音概率") / 100
            cooldown = number(
                reply.get("cooldown_seconds", 10), 0, 3600, "自动语音冷却"
            )
        except TTSError as exc:
            logger.warning("Random voice configuration error: %s", exc)
            return
        if not text or len(text) > limit or random.random() >= probability:
            return
        origin = event.unified_msg_origin
        now = time.monotonic()
        if (
            origin in self._auto_busy
            or now - self._last_auto.get(origin, -1e12) < cooldown
        ):
            return
        # Mark the result before awaiting so it cannot be processed twice.
        event.set_extra(MARKER, True)
        self._auto_busy.add(origin)
        original_chain = list(result.chain)
        try:
            path = await self._speak(
                text,
                automatic=True,
                delivery_instruction=getattr(event, "message_str", "")
                if self.config.get("delivery", {}).get("mode") == "动态参数"
                else None,
            )
            self._track_temporary(event, path)
            record = await self._record(path, text)
            if event.get_result() is not result:
                return
            if reply.get("dual_output", False):
                result.chain = [record, *original_chain]
            else:
                result.chain = [
                    record,
                    *[c for c in original_chain if not isinstance(c, Plain)],
                ]
            # Avoid a later text-to-image conversion when dual output is enabled.
            result.use_t2i_ = False
            self._last_auto[origin] = time.monotonic()
        except Exception as exc:  # noqa: BLE001 - Any adapter failure must preserve the original reply.
            # Do not clear the original message or expose raw SDK responses.
            logger.warning(
                "Random TTS failed; keeping original text (%s).", type(exc).__name__
            )
        finally:
            self._auto_busy.discard(origin)

    @filter.command("说话", alias={"朗读", "qvoice"})
    async def speak_command(
        self, event: AstrMessageEvent, language: str, text: GreedyStr
    ):
        """Read user-provided text in Chinese, English, or Japanese without translation."""
        event.should_call_llm(False)
        event.set_extra(MARKER, True)
        try:
            chain = await self._speech_chain(event, str(text), language)
            yield event.chain_result(chain)
        except TTSError as exc:
            yield event.plain_result("朗读失败：" + str(exc))
        except Exception as exc:  # noqa: BLE001 - Keep adapter details out of chat.
            logger.warning("Speech command failed (%s).", type(exc).__name__)
            yield event.plain_result("朗读失败，请检查插件语音服务配置和网络。")

    async def _speech_chain(
        self,
        event,
        text,
        language,
        hot_fix=None,
        instruction=None,
        preview=False,
        delivery_instruction=None,
        delivery_plan=None,
    ):
        """Apply shared command/tool policy and synthesize one speech message.

        Returns:
            Voice components and optionally their unchanged original text.

        Raises:
            TTSError: If permission, scope, cooldown, or synthesis validation fails.
        """
        command = self.config.get("preview" if preview else "command", {})
        if not command.get("enabled", True):
            raise TTSError(
                "试听功能已关闭。" if preview else "指定语言朗读功能已关闭。"
            )
        if command.get("admin_only", False) and not event.is_admin():
            raise TTSError("此功能仅管理员可用。")
        if not self._scope_allowed(event):
            raise TTSError("当前会话不在语音功能启用范围内。")
        code = (
            self.config.get("audio", {}).get("language", "auto")
            if language == "auto"
            else LANGUAGES.get(language.lower())
        )
        if not code:
            raise TTSError(
                "用法：/说话 zh 你好；/说话 en Hello world；/说话 ja こんにちは。只朗读原文，不翻译。"
            )
        text = str(text).strip()
        if not text:
            raise TTSError("请输入要朗读的句子。")
        user = (event.unified_msg_origin, event.get_sender_id())
        cooldown = number(command.get("cooldown_seconds", 3), 0, 3600, "朗读冷却")
        now = time.monotonic()
        if now - self._last_command.get(user, -1e12) < cooldown:
            raise TTSError("朗读请求过于频繁，请稍后重试。")
        self._last_command[user] = now
        if self.config.get("delivery", {}).get("mode") == "动态参数":
            delivery_instruction = "\n".join(
                part
                for part in (delivery_instruction, getattr(event, "message_str", ""))
                if part
            )
        if delivery_instruction or delivery_plan:
            path = await self._speak(
                text,
                language=code,
                hot_fix=hot_fix,
                instruction=instruction,
                delivery_instruction=delivery_instruction,
                delivery_plan=delivery_plan,
            )
        elif hot_fix is None and instruction is None:
            path = await self._speak(text, language=code)
        else:
            path = await self._speak(
                text, language=code, hot_fix=hot_fix, instruction=instruction
            )
        self._track_temporary(event, path)
        record = await self._record(path, text)
        chain = [record]
        if self.config.get("reply", {}).get("dual_output", False):
            chain.append(Plain(text))
        return chain

    @filter.command("说话参数")
    async def speech_with_parameters(
        self, event: AstrMessageEvent, language: str, parameters: GreedyStr
    ):
        """Read text with request-local hot_fix/instruction supplied as a JSON object."""
        event.should_call_llm(False)
        event.set_extra(MARKER, True)
        try:
            values = json.loads(str(parameters))
            if not isinstance(values, dict) or set(values) - {
                "text",
                "hot_fix",
                "instruction",
            }:
                raise TTSError("参数只支持 text、hot_fix、instruction。")
            if not isinstance(values.get("text"), str) or not isinstance(
                values.get("instruction", ""), str
            ):
                raise TTSError("text 和 instruction 必须是字符串。")
            chain = await self._speech_chain(
                event,
                values["text"],
                language,
                values.get("hot_fix"),
                values.get("instruction"),
            )
            yield event.chain_result(chain)
        except (TTSError, ValueError) as exc:
            yield event.plain_result("朗读失败：" + str(exc))
        except Exception as exc:  # noqa: BLE001 - Preserve a readable command failure.
            logger.warning("Parameterized speech failed (%s).", type(exc).__name__)
            yield event.plain_result("朗读失败，请检查网络和插件设置。")

    @filter.llm_tool(name="qwen_speak")
    async def natural_speech(
        self,
        event: AstrMessageEvent,
        text: str,
        language: str = "auto",
        hot_fix_json: str = "",
        instruction: str = "",
        delivery_instruction: str = "",
        delivery_json: str = "",
    ) -> str:
        """按用户明确要求发送语音；支持中英日朗读、单次指定读音和语气，不翻译。

        仅在用户要求朗读/用语音说/指定读音时调用。不要把用户未要求朗读的普通聊天自动交给本工具。
        发音通道由插件设置决定；给日语名字指定读音时优先构造 replace 假名映射。
        语气/停顿/升降调要求放入 delivery_instruction，独立于读音修正模式。
        精确停顿或分段音调变化时生成 delivery_json；各段 text 拼接必须逐字等于原文。
        每段调用一次并计费，最多按插件设置分段，最终只发送一条 WAV；不要随意多切段。
        pause_ms 是该段结束后追加的静音；pitch/rate/volume 是该段数值参数。
        低沉/激昂/句尾上扬等情感和轮廓用各段 instruction 描述，不承诺精确声学曲线。
        只影响本次，不修改持久设置。

        Args:
            text(string): 用户要朗读的原文，保持原文，不进行翻译。
            language(string): auto 跟随默认语言；或 zh、en、ja、中文/英语/日语；未指定用 auto。
            hot_fix_json(string): 单次 hot_fix 的 JSON 对象字符串，例如 {"replace":[{"鳳凰院凶真":"ほうおういん きょうま"}]}；没有则填空字符串。
            instruction(string): 单次读音/方言指令，按读音修正模式选取；语气和停顿使用 delivery_instruction。
            delivery_instruction(string): 本句朗读指导，例如前半句低沉，后半句激昂，句尾上扬；无则空字符串。
            delivery_json(string): 精细分段 JSON 字符串，例如 {"segments":[{"text":"你好，","pause_ms":500,"pitch":0.9,"instruction":"低沉"},{"text":"出发吧！","pitch":1.2,"instruction":"激昂，句尾上扬"}]}；仅在明确停顿或分段变化时填写，否则空字符串。

        Returns:
            Delivery confirmation or a readable failure, never a fabricated success.
        """
        if not self.config.get("natural", {}).get("enabled", True):
            return "语音自然语言工具已关闭。"
        event.set_extra(MARKER, True)
        try:
            chain = await self._speech_chain(
                event,
                text,
                language,
                hot_fix_json or None,
                instruction or None,
                delivery_instruction=delivery_instruction or None,
                delivery_plan=delivery_json or None,
            )
            try:
                await event.send(event.chain_result(chain))
            finally:
                await self.cleanup_sent_audio(event)
            return "语音已发送。单次参数未写入词典；不要再次合成或重复发送这段语音。"
        except TTSError as exc:
            return "语音未发送：" + str(exc)
        except Exception as exc:  # noqa: BLE001 - Never claim success when delivery fails.
            logger.warning("Natural speech failed (%s).", type(exc).__name__)
            return "语音发送失败，请检查插件语音服务配置、网络和适配器。"

    async def natural_preview(self, event: AstrMessageEvent) -> str:
        """用户明确要求试听当前音色或测试语音配置时，发送插件设置中的试听句子。

        使用当前 API Key、模型、音色及音频/词典参数，不修改设置，不受随机概率限制。
        若用户指定了要朗读的句子，应使用 qwen_speak，而不是本工具。

        Returns:
            Actual preview delivery result or a readable error.
        """
        if not self.config.get("natural", {}).get("enabled", True):
            return "语音自然语言工具已关闭。"
        event.set_extra(MARKER, True)
        preview = self.config.get("preview", {})
        try:
            chain = await self._speech_chain(
                event,
                preview.get("text", "你好，这是当前音色的试听。"),
                preview.get("language", "auto"),
                preview=True,
            )
            try:
                await event.send(event.chain_result(chain))
            finally:
                await self.cleanup_sent_audio(event)
            return "试听语音已发送；使用的是插件当前保存的语音服务和音频参数。"
        except TTSError as exc:
            return "试听未发送：" + str(exc)
        except Exception as exc:  # noqa: BLE001 - Never claim success on delivery failure.
            logger.warning("Natural preview failed (%s).", type(exc).__name__)
            return "试听发送失败，请检查插件语音服务配置、网络和适配器。"

    def _save_group(self, group, values):
        """Persist one configuration group, restoring memory if saving fails."""
        previous = self.config.get(group)
        self.config[group] = values
        try:
            self.config.save_config()
        except Exception:  # noqa: BLE001 - Restore the group on every persistence failure.
            if previous is None:
                self.config.pop(group, None)
            else:
                self.config[group] = previous
            raise TTSError("保存设置失败，已恢复内存中的原设置。") from None

    def _update_setting(self, name, value):
        """Validate and save a user-requested global voice setting.

        Returns:
            The canonical setting name and saved value.

        Raises:
            TTSError: If the setting name or value is unsupported.
        """
        aliases = {
            "概率": "probability",
            "字数": "max_chars",
            "开关": "enabled",
            "发音模式": "request_mode",
            "语速": "rate",
            "音调": "pitch",
            "音量": "volume",
            "默认语言": "language",
            "双输出": "dual_output",
            "词典开关": "dictionary_enabled",
            "默认指令": "instruction",
            "音色": "saved_voice",
        }
        name = aliases.get(name, name)
        if name == "saved_voice":
            self._save_group(
                "connection", saved_voice_connection(self.config, str(value))
            )
            return name, value
        bounds = {
            "probability": ("reply", 0, 100, False),
            "max_chars": ("reply", 1, 10000, True),
            "cooldown_seconds": ("reply", 0, 3600, False),
            "rate": ("audio", 0.5, 2, False),
            "pitch": ("audio", 0.5, 2, False),
            "volume": ("audio", 0, 100, True),
            "seed": ("audio", -1, 65535, True),
            "sample_rate": ("audio", 8000, 48000, True),
            "bit_rate": ("audio", 6, 510, True),
        }
        if name in bounds:
            group, low, high, integer = bounds[name]
            result = number(value, low, high, name, integer)
        elif name in ("enabled", "dual_output", "dictionary_enabled"):
            booleans = {
                "开": True,
                "关": False,
                "on": True,
                "off": False,
                "true": True,
                "false": False,
            }
            if str(value).lower() not in booleans:
                raise TTSError("开关值请填写 开/关、on/off 或 true/false。")
            group = "pronunciation" if name == "dictionary_enabled" else "reply"
            result = booleans[str(value).lower()]
        elif name == "request_mode":
            group = "pronunciation"
            result = {"两者": "both", "结合": "both"}.get(value, value)
            if result not in ("hot_fix", "instruction", "both"):
                raise TTSError("发音模式请选择 hot_fix、instruction 或 both。")
        elif name in ("language", "format", "instruction", "hot_fix_json"):
            group, result = "audio", str(value)
            if name == "language":
                result = (
                    "auto"
                    if value == "auto"
                    else LANGUAGES.get(str(value).lower(), "invalid")
                )
        elif name == "dictionary_language":
            group, result = "pronunciation", str(value)
            if result not in ("all", "zh", "en", "ja"):
                raise TTSError("词典语言范围请选择 all、zh、en 或 ja。")
        else:
            raise TTSError(
                "不支持该设置。可修改概率、字数、开关、双输出、冷却、发音模式、词典开关及常用音频参数。"
            )
        values = dict(self.config.get(group, {}))
        values[name] = result
        if group == "audio":
            build_payload("qwen-audio-3.1-tts-plus", "voice", "设置校验", values)
        self._save_group(group, values)
        return name, result

    @filter.llm_tool(name="qwen_voice_control")
    async def natural_voice_control(
        self, event: AstrMessageEvent, setting: str, value: str = ""
    ) -> str:
        """查看语音状态，或按管理员明确的长期设置要求修改全局语音配置。

        只有用户明确说设置、以后、一直、默认、关闭、开启等长期修改意图时才修改。
        单次朗读或单次读音要求必须使用 qwen_speak，不能通过本工具永久修改默认指令。
        管理员要求切换已保存音色时，先用 saved_voices 查看名称，再用 saved_voice 同时切换模型和音色；不得杜撰名称。

        Args:
            setting(string): status 查看状态；修改可选 probability、max_chars、enabled、dual_output、cooldown_seconds、request_mode、dictionary_enabled、dictionary_language、rate、pitch、volume、seed、sample_rate、bit_rate、format、language、instruction、hot_fix_json；saved_voices 列出已保存音色，saved_voice 按名称切换模型与音色。
            value(string): 新值字符串，概率按百分比 0～100，开关用 on/off，模式用 hot_fix/instruction/both；status 或 saved_voices 时填空字符串；saved_voice 填已保存音色的完整名称。

        Returns:
            Actual saved settings, state, or a permission/error message.
        """
        if not self.config.get("natural", {}).get("enabled", True):
            return "语音自然语言工具已关闭。"
        event.set_extra(MARKER, True)
        if not self._scope_allowed(event):
            return "当前会话不在语音功能启用范围内。"
        if setting == "status":
            return json.dumps(
                {
                    "reply": self.config.get("reply", {}),
                    "audio": self.config.get("audio", {}),
                    "request_mode": self.config.get("pronunciation", {}).get(
                        "request_mode", "hot_fix"
                    ),
                },
                ensure_ascii=False,
            )
        if not event.is_admin():
            return "只有管理员可以修改全局语音设置。"
        try:
            if setting == "saved_voices":
                entries = parse_saved_voices(
                    snapshot(self.config)["voice_library"]["entries_json"]
                )
                return json.dumps(
                    [
                        {"name": entry["name"], "model": entry["model"]}
                        for entry in entries
                    ],
                    ensure_ascii=False,
                )
            name, saved = self._update_setting(setting, value)
            return f"已持久保存全局设置 {name}={saved}。"
        except TTSError as exc:
            return "设置未保存：" + str(exc)

    @filter.llm_tool(name="qwen_voice_dictionary")
    async def natural_dictionary(
        self, event: AstrMessageEvent, operation: str, word: str = "", reading: str = ""
    ) -> str:
        """查看持久替换词典，或按管理员明确要求记住/修改/删除以后使用的词语读音。

        仅当用户明确要求长期记住、加入词典、以后都这样读或删除词条时才写入。
        “这次这样读”只能用 qwen_speak 的单次 hot_fix，不得写入词典。

        Args:
            operation(string): list 查看词典；set 新增或更新；delete 删除一个词条。
            word(string): 原文词语，例如鳳凰院凶真；list 时填空字符串。
            reading(string): 替换成的读音，例如ほうおういん きょうま；list/delete 时填空字符串。

        Returns:
            The current dictionary or the actual persistence result.
        """
        if not self.config.get("natural", {}).get("enabled", True):
            return "语音自然语言工具已关闭。"
        event.set_extra(MARKER, True)
        if not self._scope_allowed(event):
            return "当前会话不在语音功能启用范围内。"
        try:
            preferences = dict(self.config.get("pronunciation", {}))
            dictionary = parse_dictionary(preferences.get("dictionary_json", "{}"))
            if operation == "list":
                return json.dumps(dictionary, ensure_ascii=False, indent=2)
            if not event.is_admin():
                return "只有管理员可以修改持久替换词典。"
            if operation == "set":
                dictionary.update(parse_dictionary({word: reading}))
            elif operation == "delete":
                if word not in dictionary:
                    return "词典中没有这个词条，未修改。"
                del dictionary[word]
            else:
                return "词典操作只支持 list、set、delete。"
            preferences["dictionary_json"] = json.dumps(
                dictionary, ensure_ascii=False, indent=2
            )
            self._save_group("pronunciation", preferences)
            return "持久替换词典已保存；后续是否应用以词典开关和语言范围设置为准。"
        except TTSError as exc:
            return "词典未保存：" + str(exc)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("语音设置")
    async def voice_settings(self, event: AstrMessageEvent, item: str, value: str):
        """Allow administrators to change global probability, limits, and enablement."""
        event.should_call_llm(False)
        event.set_extra(MARKER, True)
        if not event.is_admin():
            yield event.plain_result("只有管理员可以修改全局语音设置。")
            return
        try:
            self._update_setting(item, value)
            yield event.plain_result("语音设置已保存。")
        except (TTSError, OSError) as exc:
            yield event.plain_result("设置失败：" + str(exc))
