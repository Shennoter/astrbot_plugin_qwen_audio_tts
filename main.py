# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""AstrBot compatibility patch, probabilistic voice replies, and speech commands."""

import asyncio
import functools
import json
import random
import re
import time
import uuid
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Plain, Record, Reply
from astrbot.api.star import Context, Star, register
from astrbot.core.message.message_event_result import ResultContentType
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from .pronunciation import compose_settings, estimated_text, parse_dictionary
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


@register("astrbot_plugin_qwen_audio_tts", "Shennoter", "Qwen Audio 语音助手", "0.2.0")
class QwenAudioVoicePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._original = None
        self._wrapper = None
        self._provider_class = None
        self._last_auto = {}
        self._last_command = {}
        self._auto_busy = set()
        self._warned = set()
        self._closed = False
        limit = number(
            config.get("connection", {}).get("max_concurrent", 2), 1, 8, "并发数", True
        )
        self._semaphore = asyncio.Semaphore(limit)
        self.audio_dir = Path(get_astrbot_temp_path()).resolve() / "qwen_audio_voice"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self._sweep_at = 0

    async def initialize(self):
        """Install an instance-owned wrapper for the built-in provider class."""
        if self._provider_class and self._provider_class.get_audio is self._wrapper:
            return
        if not self.config.get("connection", {}).get("patch_builtin", True):
            return
        from astrbot.core.provider.sources.dashscope_tts import ProviderDashscopeTTSAPI

        original = ProviderDashscopeTTSAPI.get_audio
        if getattr(original, "_qwen_audio_voice_patch", False):
            raise RuntimeError(
                "A Qwen Audio compatibility patch is already active; disable duplicate plugin copies."
            )

        @functools.wraps(original)
        async def wrapped(provider, text):
            model = provider.get_model().lower()
            if not self._closed and model.startswith("qwen-audio-") and "tts" in model:
                return str(await self._speak(provider, text))
            return await original(provider, text)

        wrapped._qwen_audio_voice_patch = True
        self._original, self._wrapper = original, wrapped
        self._provider_class = ProviderDashscopeTTSAPI
        ProviderDashscopeTTSAPI.get_audio = wrapped
        logger.info("Qwen Audio HTTP compatibility patch installed.")

    async def terminate(self):
        """Restore only the wrapper installed by this plugin instance."""
        self._closed = True
        if self._provider_class and self._provider_class.get_audio is self._wrapper:
            self._provider_class.get_audio = self._original
        self._auto_busy.clear()
        self._last_auto.clear()
        self._last_command.clear()

    async def _provider(self, event):
        """Resolve the configured provider or the session's selected TTS provider.

        Returns:
            A configured Qwen-Audio-TTS provider instance.

        Raises:
            TTSError: If the selected provider is missing or incompatible.
        """
        provider_id = str(
            self.config.get("connection", {}).get("provider_id", "")
        ).strip()
        if provider_id:
            provider = self.context.get_provider_by_id(provider_id)
        else:
            resolver = getattr(self.context, "get_using_tts_provider_async", None)
            if resolver:
                provider = await resolver(event.unified_msg_origin)
            else:
                provider = self.context.get_using_tts_provider(event.unified_msg_origin)
        if not provider or not hasattr(provider, "get_model"):
            raise TTSError(
                "找不到 TTS 提供商，请启用对应模型，并在插件设置中填写提供商 ID。"
            )
        if not provider.get_model().lower().startswith("qwen-audio-"):
            raise TTSError("插件当前选择的提供商不是 Qwen-Audio-TTS，请检查提供商 ID。")
        return provider

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
        provider,
        text,
        language=None,
        automatic=False,
        hot_fix=None,
        instruction=None,
    ):
        """Generate an audio file using this provider's own credentials.

        Args:
            provider: The selected built-in provider instance.
            text: Original text to read.
            language: Optional target pronunciation language code.
            automatic: Whether to disable SSML for ordinary chat replies.
            hot_fix: Request-local pronunciation rules, never saved to configuration.
            instruction: Request-local voice instruction, never saved to configuration.

        Returns:
            Path to the generated audio file.

        Raises:
            TTSError: If configuration, limits, or synthesis fail.
        """
        if self._closed:
            raise TTSError("插件已停用。")
        connection = self.config.get("connection", {})
        settings = compose_settings(
            self.config.get("audio", {}),
            self.config.get("pronunciation", {}),
            language=language,
            hot_fix=hot_fix,
            instruction=instruction,
        )
        max_chars = number(
            self.config.get("reply", {}).get("max_chars", 200),
            1,
            10000,
            "最大字数",
            True,
        )
        if len(text) > max_chars:
            raise TTSError(
                f"文字长度 {len(text)} 超过最大字数 {max_chars}，请缩短或调整设置。"
            )
        if automatic:
            settings["enable_ssml"] = False
        config = getattr(provider, "provider_config", {})
        model = provider.get_model()
        voice = str(
            config.get("dashscope_tts_voice", getattr(provider, "voice", ""))
        ).strip()
        key = str(
            config.get("api_key", getattr(provider, "chosen_api_key", ""))
        ).strip()
        base = workspace_base(connection.get("workspace", ""))
        timeout = number(connection.get("timeout", 60), 5, 300, "超时时间")
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
        path = (
            self.audio_dir / f"speech_{uuid.uuid4().hex}.{payload['input']['format']}"
        )
        path.write_bytes(content)
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
            keep_hours = number(
                connection.get("keep_hours", 24), 1, 720, "文件保留小时"
            )
            # Delete only files generated in this plugin's own directory.
            for old in self.audio_dir.glob("speech_*.*"):
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
            provider = await self._provider(event)
            path = await self._speak(provider, text, automatic=True)
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
            yield event.plain_result("朗读失败，请检查提供商、网络和插件设置。")

    async def _speech_chain(
        self, event, text, language, hot_fix=None, instruction=None
    ):
        """Apply shared command/tool policy and synthesize one speech message.

        Returns:
            Voice components and optionally their unchanged original text.

        Raises:
            TTSError: If permission, scope, cooldown, or synthesis validation fails.
        """
        command = self.config.get("command", {})
        if not command.get("enabled", True):
            raise TTSError("指定语言朗读功能已关闭。")
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
        provider = await self._provider(event)
        if hot_fix is None and instruction is None:
            path = await self._speak(provider, text, language=code)
        else:
            path = await self._speak(
                provider, text, language=code, hot_fix=hot_fix, instruction=instruction
            )
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
    ) -> str:
        """按用户明确要求发送语音；支持中英日朗读、单次指定读音和语气，不翻译。

        仅在用户要求朗读/用语音说/指定读音时调用。不要把用户未要求朗读的普通聊天自动交给本工具。
        发音通道由插件设置决定；给日语名字指定读音时优先构造 replace 假名映射。
        可同时填写两种单次参数，插件按当前模式选取。参数只影响这次，不修改持久词典。

        Args:
            text(string): 用户要朗读的原文，保持原文，不进行翻译。
            language(string): auto 跟随默认语言；或 zh、en、ja、中文/英语/日语；未指定用 auto。
            hot_fix_json(string): 单次 hot_fix 的 JSON 对象字符串，例如 {"replace":[{"鳳凰院凶真":"ほうおういん きょうま"}]}；没有则填空字符串。
            instruction(string): 单次语气或读音指令，例如温柔地读；没有则填空字符串。

        Returns:
            Delivery confirmation or a readable failure, never a fabricated success.
        """
        if not self.config.get("natural", {}).get("enabled", True):
            return "语音自然语言工具已关闭。"
        event.set_extra(MARKER, True)
        try:
            chain = await self._speech_chain(
                event, text, language, hot_fix_json or None, instruction or None
            )
            await event.send(event.chain_result(chain))
            return "语音已发送。单次参数未写入词典；不要再次合成或重复发送这段语音。"
        except TTSError as exc:
            return "语音未发送：" + str(exc)
        except Exception as exc:  # noqa: BLE001 - Never claim success when delivery fails.
            logger.warning("Natural speech failed (%s).", type(exc).__name__)
            return "语音发送失败，请检查网络、适配器和插件设置。"

    def _save_group(self, group, values):
        """Persist one configuration group, restoring memory if saving fails."""
        previous = self.config.get(group)
        self.config[group] = values
        try:
            self.config.save_config()
        except OSError:
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
        }
        name = aliases.get(name, name)
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

        Args:
            setting(string): status 查看状态；修改可选 probability、max_chars、enabled、dual_output、cooldown_seconds、request_mode、dictionary_enabled、dictionary_language、rate、pitch、volume、seed、sample_rate、bit_rate、format、language、instruction、hot_fix_json。
            value(string): 新值字符串，概率按百分比 0～100，开关用 on/off，模式用 hot_fix/instruction/both；status 时填空字符串。

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

    @filter.command("语音状态")
    async def voice_status(self, event: AstrMessageEvent):
        """Show useful state without exposing credentials."""
        event.should_call_llm(False)
        event.set_extra(MARKER, True)
        reply = self.config.get("reply", {})
        connection = self.config.get("connection", {})
        native = (
            self.context.get_config(event.unified_msg_origin)
            .get("provider_tts_settings", {})
            .get("enable", False)
        )
        yield event.plain_result(
            f"随机语音：{'开启' if reply.get('enabled', True) else '关闭'}\n"
            f"概率：{reply.get('probability', 20)}%\n最大字数：{reply.get('max_chars', 200)}\n"
            f"提供商：{connection.get('provider_id') or '跟随会话'}\n"
            f"单次发音模式：{self.config.get('pronunciation', {}).get('request_mode', 'hot_fix')}\n"
            f"当前会话：{event.unified_msg_origin}\n"
            + (
                "提示：内置自动 TTS 已开启，插件随机转换将跳过；请关闭内置自动 TTS。"
                if native
                else "请关闭聊天流式输出，以便转换完整回复。"
            )
        )

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
