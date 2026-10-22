# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""Voice direction isolation, precise pause frames, validation and failure tests."""

import copy
import io
import json
import struct
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from . import test_plugin as h
from .delivery import apply_direction, join_wav, parse_plan
from .tts_client import TTSError


class PlanTests(unittest.TestCase):
    def test_direction_is_independent_of_pronunciation_mode(self):
        source = {"instruction": "全局要求"}
        result = apply_direction(source, "低沉，后半句激昂，句尾上扬", {})
        self.assertIn("全局要求", result["instruction"])
        self.assertIn("激昂", result["instruction"])
        self.assertEqual(source, {"instruction": "全局要求"})
        with self.assertRaises(TTSError):
            apply_direction(source, "低沉", {"enabled": False})

    def test_plan_requires_unchanged_text_and_bounded_controls(self):
        good = {
            "segments": [
                {"text": "你好，", "pause_ms": 500, "pitch": 0.9},
                {"text": "出发！", "pitch": 1.2, "instruction": "激昂，句尾上扬"},
            ]
        }
        self.assertEqual(len(parse_plan(json.dumps(good), "你好，出发！", {})), 2)
        for bad in (
            {"segments": []},
            {"segments": [{"text": "改写"}]},
            {"segments": [{"text": "你好，出发！", "pause_ms": True}]},
            {"segments": [{"text": "你好，出发！", "pause_ms": 10001}]},
            {"segments": [{"text": "你好，出发！", "pitch": 5}]},
            {"segments": [{"text": "你好，出发！", "rate": "0.8"}]},
            {"segments": [{"text": "你好，出发！", "url": "https://example.com"}]},
        ):
            with self.subTest(bad=bad), self.assertRaises(TTSError):
                parse_plan(bad, "你好，出发！", {})
        with self.assertRaises(TTSError):
            parse_plan(good, "你好，出发！", {"max_segments": 1})
        with self.assertRaises(TTSError):
            parse_plan(good, "你好，出发！", {"max_total_pause_ms": 100})

    def test_exact_pause_frames_and_first_clip_metadata_survive_join(self):
        raw = h.wav_bytes()
        chunk = b"LIST" + struct.pack("<I", 4) + b"AIGC"
        tagged = raw + chunk
        tagged = tagged[:4] + struct.pack("<I", len(tagged) - 8) + tagged[8:]
        result = join_wav([(tagged, 500), (raw, 0)])
        with wave.open(io.BytesIO(result), "rb") as reader:
            self.assertEqual(reader.getnframes(), 2400 * 2 + 12000)
            pcm = reader.readframes(reader.getnframes())
        self.assertEqual(pcm[4800 : 4800 + 24000], b"\0" * 24000)
        self.assertIn(b"LIST\x04\0\0\0AIGC", result)
        self.assertEqual(struct.unpack_from("<I", result, 4)[0] + 8, len(result))

    def test_join_rejects_invalid_or_incompatible_audio(self):
        raw = h.wav_bytes()
        other_rate = raw[:24] + struct.pack("<I", 16000) + raw[28:]
        with self.assertRaises(TTSError):
            join_wav([(raw, 0), (other_rate, 0)])
        with self.assertRaises(TTSError):
            join_wav([(b"not wav", 0)])


class DirectedSpeechTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = h.PluginTests.asyncSetUp

    def plan(self):
        return {
            "segments": [
                {
                    "text": "你好，",
                    "pause_ms": 500,
                    "pitch": 0.9,
                    "instruction": "低沉缓慢",
                },
                {"text": "出发！", "pitch": 1.2, "instruction": "激昂，句尾上扬"},
            ]
        }

    async def test_single_direction_works_in_default_hot_fix_mode(self):
        before = copy.deepcopy(self.config)
        with patch.object(
            h.main, "synthesize_http", return_value=h.wav_bytes()
        ) as network:
            event = h.Event()
            result = await self.plugin.natural_speech(
                event, "你好", delivery_instruction="低沉，最后两个字上扬"
            )
        self.assertIn("已发送", result)
        self.assertEqual(network.call_count, 1)
        self.assertIn(
            "最后两个字上扬", network.call_args.args[2]["input"]["instruction"]
        )
        self.assertEqual(self.config, before)
        self.assertEqual(list(self.plugin.audio_dir.iterdir()), [])

    async def test_segment_parameters_and_original_display_text(self):
        self.config["reply"]["dual_output"] = True
        self.config["audio"].update(format="mp3", enable_ssml=True)
        before = copy.deepcopy(self.config)
        with patch.object(
            h.main, "synthesize_http", return_value=h.wav_bytes()
        ) as network:
            path = await self.plugin._speak(
                "你好，出发！",
                language="zh",
                delivery_instruction="请按指定段落朗读",
                delivery_plan=self.plan(),
            )
        self.assertEqual(network.call_count, 2)
        inputs = [call.args[2]["input"] for call in network.call_args_list]
        self.assertEqual([item["text"] for item in inputs], ["你好，", "出发！"])
        self.assertEqual([item["pitch"] for item in inputs], [0.9, 1.2])
        self.assertTrue(
            all(item["format"] == "wav" and not item["enable_ssml"] for item in inputs)
        )
        self.assertIn("低沉缓慢", inputs[0]["instruction"])
        self.assertIn("激昂", inputs[1]["instruction"])
        with wave.open(str(path), "rb") as reader:
            self.assertEqual(reader.getnframes(), 16800)
        self.assertEqual(path.suffix, ".wav")
        self.assertEqual(self.config, before)
        self.assertEqual(list(self.plugin.audio_dir.iterdir()), [path])

    async def test_invalid_later_segment_is_rejected_before_any_paid_call(self):
        plan = self.plan()
        plan["segments"][1]["pitch"] = 6
        with (
            patch.object(h.main, "synthesize_http") as network,
            self.assertRaises(TTSError),
        ):
            await self.plugin._speak("你好，出发！", delivery_plan=plan)
        network.assert_not_called()

    async def test_partial_failure_has_no_final_audio_and_never_retries(self):
        with (
            patch.object(
                h.main,
                "synthesize_http",
                side_effect=[h.wav_bytes(), TTSError("第二段网络失败")],
            ) as network,
            self.assertRaisesRegex(TTSError, "第 2 段"),
        ):
            await self.plugin._speak("你好，出发！", delivery_plan=self.plan())
        self.assertEqual(network.call_count, 2)
        self.assertEqual(list(self.plugin.audio_dir.iterdir()), [])

    async def test_only_merged_file_is_retained(self):
        self.config["connection"]["save_audio"] = True
        with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()):
            path = await self.plugin._speak("你好，出发！", delivery_plan=self.plan())
        self.assertTrue(path.is_relative_to(self.plugin.retained_audio_dir))
        self.assertEqual(list(self.plugin.audio_dir.iterdir()), [])
        self.assertEqual(list(self.plugin.retained_audio_dir.iterdir()), [path])

    async def test_direction_disabled_and_total_chars_not_bypassed(self):
        with patch.object(h.main, "synthesize_http") as network:
            self.config["delivery"]["mode"] = "固定参数"
            with self.assertRaises(TTSError):
                await self.plugin._speak("你好", delivery_instruction="低沉")
            self.config["delivery"]["mode"] = "混合"
            self.config["reply"]["max_chars"] = 3
            with self.assertRaises(TTSError):
                await self.plugin._speak("你好，出发！", delivery_plan=self.plan())
        network.assert_not_called()

    async def test_preview_passes_ephemeral_guidance_and_reports_segments(self):
        before = copy.deepcopy(self.config)
        self.request.json.return_value = {
            "text": "你好，出发！",
            "language": "zh",
            "delivery_instruction": "先低沉后激昂",
            "delivery_plan": self.plan(),
        }
        with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()):
            value, status = await self.plugin.preview_generate()
        self.assertEqual(status, 200)
        self.assertEqual(value["segments"], 2)
        self.assertEqual(value["added_pause_ms"], 500)
        self.assertEqual(value["mime_type"], "audio/wav")
        self.assertEqual(self.config, before)
        self.assertEqual(list(self.plugin.audio_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()


class DeliveryModeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = h.PluginTests.asyncSetUp

    async def test_fixed_rejects_chat_guidance_before_network(self):
        self.config["delivery"]["mode"] = "固定参数"
        with patch.object(h.main, "synthesize_http") as network:
            with self.assertRaisesRegex(TTSError, "固定参数"):
                await self.plugin._speak("你好", delivery_instruction="激昂")
            network.assert_not_called()

    async def test_dynamic_selected_llm_once_and_isolated(self):
        self.config["delivery"].update(mode="动态参数", provider_id="registered")
        before = copy.deepcopy(self.config)
        llm = AsyncMock(
            return_value=SimpleNamespace(
                completion_text=json.dumps(
                    {
                        "rate": 1.1,
                        "pitch": 0.9,
                        "volume": 60,
                        "instruction": "低沉",
                        "segments": [],
                    }
                )
            )
        )
        self.plugin.context = SimpleNamespace(
            get_provider_by_id=lambda p: object() if p == "registered" else None,
            llm_generate=llm,
        )
        with patch.object(
            h.main, "synthesize_http", return_value=h.wav_bytes()
        ) as network:
            await self.plugin._speak("你好", delivery_instruction="沉稳")
            self.assertEqual(network.call_args.args[2]["input"]["pitch"], 0.9)
        self.assertEqual(llm.await_count, 1)
        self.assertEqual(llm.call_args.kwargs["chat_provider_id"], "registered")
        self.assertEqual(self.config, before)

    async def test_dynamic_invalid_output_never_calls_tts(self):
        self.config["delivery"].update(mode="动态参数", provider_id="registered")
        self.plugin.context = SimpleNamespace(
            get_provider_by_id=lambda p: object(),
            llm_generate=AsyncMock(
                return_value=SimpleNamespace(
                    completion_text='{"rate":99,"pitch":1,"volume":50}'
                )
            ),
        )
        with patch.object(h.main, "synthesize_http") as network:
            with self.assertRaises(TTSError):
                await self.plugin._speak("你好")
            network.assert_not_called()
