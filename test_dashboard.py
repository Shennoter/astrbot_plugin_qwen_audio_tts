# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""Dashboard settings, upload security, enrollment recovery and retention tests."""

import asyncio
import base64
import copy
import io
import json
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError

from . import enrollment, settings
from . import test_plugin as h
from .tts_client import TTSError


def reference_wav(seconds=5, rate=16000, channels=1, width=2):
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        writer.writeframes(b"\x10" * (int(seconds * rate) * channels * width))
    return out.getvalue()


class Upload:
    def __init__(self, data, filename="sample.wav"):
        self.filename = filename
        self.stream = io.BytesIO(data)
        self.closed = False

    async def read(self, size):
        return self.stream.read(size)

    async def close(self):
        self.closed = True


class Files(dict):
    def getlist(self, key):
        return [self[key]] if key in self else []


class DashboardTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = h.PluginTests.asyncSetUp

    def manager(self):
        if getattr(self.plugin, "_enrollment_manager", None) is None:
            self.plugin._enrollment_manager = enrollment.EnrollmentManager(
                Path(self.tmp.name) / "enrollment"
            )
        return self.plugin._enrollment_manager

    def stage_upload(self, data=None, filename="sample.wav"):
        self.manager()
        upload = Upload(data if data is not None else reference_wav(), filename)
        self.request.headers = {
            "content-length": str(len(upload.stream.getvalue()) + 200),
            "content-type": "multipart/form-data; boundary=test",
        }
        self.request.files = AsyncMock(return_value=Files(file=upload))
        self.plugin._web.PluginUploadFile = Upload
        return upload

    async def test_complete_settings_redacted_and_model_is_free_text(self):
        value, status = await self.plugin.preview_settings()
        self.assertEqual(status, 200)
        self.assertEqual(value["config"]["connection"]["api_key"], "")
        self.assertNotIn("test-key", json.dumps(value))
        self.assertNotIn("options", value["schema"]["connection"]["items"]["model"])
        self.assertTrue(
            value["config"]["connection"]["workspace"].endswith(".aliyuncs.com")
        )
        self.assertFalse(value["config"]["connection"]["save_audio"])

    async def test_save_all_settings_preserves_secret_and_detects_conflicts(self):
        draft = settings.public_settings(self.config)["config"]
        draft["audio"]["rate"] = 1.25
        draft["connection"]["max_concurrent"] = 3
        before_revision = settings.revision(self.config)
        self.request.json.return_value = {"config": draft, "revision": before_revision}
        response, status = await self.plugin.save_settings()
        self.assertEqual(status, 200)
        self.assertEqual(self.config["connection"]["api_key"], "test-key")
        self.assertEqual(self.config["audio"]["rate"], 1.25)
        self.assertEqual(self.plugin._semaphore._value, 3)
        self.assertNotIn("test-key", json.dumps(response))
        self.assertEqual((await self.plugin.save_settings())[1], 409)

    async def test_new_key_and_explicit_clear_never_return_secret(self):
        for key, clear in (("changed-secret", False), ("", True)):
            self.request.json.return_value = {
                "config": {"connection": {"api_key": key}},
                "revision": settings.revision(self.config),
                "clear_api_key": clear,
            }
            response, status = await self.plugin.save_settings()
            self.assertEqual(status, 200)
            self.assertEqual(self.config["connection"]["api_key"], "" if clear else key)
            self.assertNotIn("changed-secret", json.dumps(response))

    async def test_bad_config_values_do_not_mutate_saved_state(self):
        before = copy.deepcopy(self.config)
        bad = [
            {"shell": {}},
            {"connection": {"unknown": True}},
            {"connection": {"workspace": "ws-short"}},
            {"connection": {"workspace": "https://127.0.0.1/"}},
            {
                "connection": {
                    "workspace": "ws-a.cn-beijing.maas.aliyuncs.com.evil.test"
                }
            },
            {"audio": {"rate": True}},
            {"audio": {"rate": 9}},
            {"reply": {"max_chars": 10.5}},
            {"reply": {"max_chars": -1}},
            {"scope": {"allowed_sessions": [23]}},
            {"audio": {"hot_fix_json": "bad"}},
            {"pronunciation": {"dictionary_json": '{"a":3}'}},
        ]
        for draft in bad:
            with self.subTest(draft=draft):
                self.request.json.return_value = {
                    "config": draft,
                    "revision": settings.revision(self.config),
                }
                self.assertEqual((await self.plugin.save_settings())[1], 400)
                self.assertEqual(self.config, before)

    async def test_save_failure_restores_complete_config(self):
        before = copy.deepcopy(self.config)
        self.request.json.return_value = {
            "config": {"reply": {"probability": 55}},
            "revision": settings.revision(self.config),
        }
        with patch.object(
            h.Config, "save_config", side_effect=OSError("disk unavailable")
        ):
            self.assertEqual((await self.plugin.save_settings())[1], 400)
        self.assertEqual(self.config, before)

    async def test_live_draft_controls_next_request_and_does_not_persist(self):
        before = copy.deepcopy(self.config)
        self.request.json.return_value = {
            "text": "Hello",
            "language": "en",
            "config": {"audio": {"rate": 1.35, "pitch": 0.8}},
        }
        with patch.object(
            h.main, "synthesize_http", return_value=h.wav_bytes()
        ) as network:
            result, status = await self.plugin.preview_generate()
        self.assertEqual(status, 200)
        self.assertEqual(network.call_args.args[2]["input"]["rate"], 1.35)
        self.assertEqual(network.call_args.args[2]["input"]["pitch"], 0.8)
        self.assertEqual(base64.b64decode(result["audio_base64"]), h.wav_bytes())
        self.assertEqual(self.config, before)
        self.assertEqual(list(self.plugin.audio_dir.iterdir()), [])

    async def test_retained_audio_and_temporary_chat_cleanup(self):
        with patch.object(h.main, "synthesize_http", return_value=h.wav_bytes()):
            path = await self.plugin._speak("temporary")
            event = h.Event()
            self.plugin._track_temporary(event, path)
            self.assertTrue(path.exists())
            await self.plugin.cleanup_sent_audio(event)
            self.assertFalse(path.exists())
            self.config["connection"]["save_audio"] = True
            self.request.json.return_value = {"text": "retained"}
            self.assertEqual((await self.plugin.preview_generate())[1], 200)
            retained = list(self.plugin.retained_audio_dir.glob("speech_*"))
            self.assertEqual(len(retained), 1)
            await self.plugin.terminate()
            self.assertTrue(retained[0].exists())

    async def test_older_model_id_works_with_matching_voice(self):
        draft = settings.validate_draft(
            self.config,
            {
                "connection": {
                    "model": "qwen-audio-3.0-tts-flash",
                    "voice": "qwen-audio-3.0-tts-flash-myvoice-test",
                }
            },
        )
        with patch.object(
            h.main, "synthesize_http", return_value=h.wav_bytes()
        ) as network:
            await self.plugin._speak("旧模型", config_snapshot=draft)
        self.assertEqual(network.call_args.args[2]["model"], "qwen-audio-3.0-tts-flash")

    async def test_all_dashboard_routes_require_login(self):
        self.request.username = None
        self.request.json = AsyncMock(side_effect=AssertionError("must not parse"))
        for endpoint in (
            self.plugin.save_settings,
            self.plugin.preview_settings,
            self.plugin.preview_generate,
            self.plugin.cloning_upload,
            self.plugin.cloning_create,
            self.plugin.cloning_jobs,
            self.plugin.cloning_query,
        ):
            self.assertEqual((await endpoint())[1], 401)
        self.request.json.assert_not_called()

    async def test_upload_headers_rejected_before_parsing(self):
        self.stage_upload()
        for headers, expected in (
            ({}, 411),
            ({"content-length": "-1"}, 411),
            ({"content-length": "999999999"}, 413),
            ({"content-length": "200", "transfer-encoding": "chunked"}, 411),
        ):
            self.request.headers = headers
            self.assertEqual((await self.plugin.cloning_upload())[1], expected)
        self.request.files.assert_not_called()

    async def test_upload_uses_random_private_path_and_closes_file(self):
        upload = self.stage_upload(filename="../../escape.wav")
        result, status = await self.plugin.cloning_upload()
        self.assertEqual(status, 200)
        self.assertTrue(upload.closed)
        entry = self.manager().uploads[result["upload_id"]]
        self.assertEqual(entry["path"].parent, self.manager().root)
        self.assertEqual(entry["path"].name, f"upload_{result['upload_id']}.wav")
        self.assertEqual(result["seconds"], 5)
        self.assertNotIn("path", result)
        self.assertEqual(self.manager().jobs, {})  # Upload alone never bills.

    async def test_fake_and_oversize_upload_never_staged(self):
        for data in (
            b"PK\x03\x04" + b"x" * 100,
            reference_wav()[:-2],
            b"x" * (enrollment.MAX_AUDIO + 1),
        ):
            upload = self.stage_upload(data)
            # A misleading small header still cannot bypass the byte-count check.
            self.request.headers["content-length"] = "200"
            _, status = await self.plugin.cloning_upload()
            self.assertIn(status, (400, 413))
            self.assertTrue(upload.closed)
            self.assertEqual(self.manager().uploads, {})

    async def test_upload_requires_login_but_no_enable_switch(self):
        self.stage_upload()
        self.config["cloning"].update(
            enabled=False, cooldown_seconds=3600, daily_limit=1
        )
        self.request.username = ""
        self.assertEqual((await self.plugin.cloning_upload())[1], 401)
        self.request.files.assert_not_called()
        self.request.username = "admin"
        self.assertEqual((await self.plugin.cloning_upload())[1], 200)
        items = settings.public_settings(self.config)["schema"]["cloning"]["items"]
        self.assertTrue(
            {"enabled", "cooldown_seconds", "daily_limit"}.isdisjoint(items)
        )


class EnrollmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manager = enrollment.EnrollmentManager(Path(self.tmp.name))
        self.addAsyncCleanup(self.manager.close)
        cfg = settings.snapshot(h.defaults())
        self.connection = cfg["connection"]
        self.connection.update(
            api_key="test-secret",
            workspace="ws-test.cn-beijing.maas.aliyuncs.com",
            voice="",
        )
        self.preferences = cfg["cloning"]
        self.clean, self.info = enrollment.validate_wav(reference_wav())

    def upload(self, owner="admin"):
        return self.manager.add_upload(owner, self.connection, self.clean, self.info)[
            "upload_id"
        ]

    async def drain(self):
        if self.manager.tasks:
            await asyncio.gather(*list(self.manager.tasks))

    async def test_create_is_single_attempt_and_id_survives_restart(self):
        token = self.upload()
        voice = self.connection["model"] + "-myvoice-example"
        with patch.object(
            enrollment, "enrollment_http", return_value={"voice_id": voice}
        ) as network:
            first = self.manager.create(
                "admin", self.connection, self.preferences, {"upload_id": token}
            )
            self.assertEqual(first["status"], "CREATING")
            self.manager.create(
                "admin", self.connection, self.preferences, {"upload_id": token}
            )
            await self.drain()
            again = self.manager.create(
                "admin", self.connection, self.preferences, {"upload_id": token}
            )
        self.assertEqual(network.call_count, 1)
        self.assertEqual(again["voice_id"], voice)
        self.assertTrue(
            network.call_args.kwargs["audio"].startswith("data:audio/wav;base64,")
        )
        self.assertEqual(list(self.manager.root.glob("upload_*.wav")), [])
        self.assertNotIn("test-secret", self.manager.journal.read_text())
        loaded = enrollment.EnrollmentManager(self.manager.root)
        self.assertEqual(
            loaded.visible_jobs("admin", self.connection)[0]["voice_id"], voice
        )

    async def test_network_failure_unknown_and_never_resubmitted(self):
        token = self.upload()
        with patch.object(
            enrollment, "enrollment_http", side_effect=TTSError("secret or network")
        ) as network:
            self.manager.create(
                "admin", self.connection, self.preferences, {"upload_id": token}
            )
            await self.drain()
            result = self.manager.create(
                "admin", self.connection, self.preferences, {"upload_id": token}
            )
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(network.call_count, 1)
        self.assertEqual(list(self.manager.root.glob("upload_*.wav")), [])

    async def test_owner_account_expiry_and_upload_quota(self):
        token = self.upload("alice")
        with self.assertRaises(TTSError):
            self.manager.create(
                "bob", self.connection, self.preferences, {"upload_id": token}
            )
        changed = {**self.connection, "api_key": "different"}
        with self.assertRaises(TTSError):
            self.manager.create(
                "alice", changed, self.preferences, {"upload_id": token}
            )
        self.upload()
        self.upload()
        with self.assertRaises(TTSError):
            self.upload()
        path = self.manager.uploads[token]["path"]
        self.manager.uploads[token]["expires"] = 0
        self.manager.purge()
        self.assertFalse(path.exists())

    async def test_consecutive_creations_ignore_removed_legacy_limits(self):
        self.preferences.update(enabled=False, daily_limit=1, cooldown_seconds=3600)
        with patch.object(
            enrollment,
            "enrollment_http",
            return_value={"voice_id": self.connection["model"] + "-test-one"},
        ) as network:
            for _ in range(3):
                token = self.upload()
                result = self.manager.create(
                    "admin", self.connection, self.preferences, {"upload_id": token}
                )
                self.assertEqual(result["status"], "CREATING")
                await self.drain()
            self.assertEqual(network.call_count, 3)

    async def test_invalid_options_never_call_cloud(self):
        token = self.upload()
        with patch.object(enrollment, "enrollment_http") as network:
            for body in (
                {"prefix": "../../x"},
                {"model": "https://evil"},
                {"enable_preprocess": "false"},
                {"max_prompt_audio_length": 100},
                {"audio": "https://127.0.0.1"},
            ):
                with self.assertRaises(TTSError):
                    self.manager.create(
                        "admin",
                        self.connection,
                        self.preferences,
                        {"upload_id": token, **body},
                    )
        network.assert_not_called()
        self.assertEqual(self.manager.jobs, {})

    async def test_journal_write_failure_prevents_billable_attempt(self):
        token = self.upload()
        with (
            patch.object(self.manager, "persist", side_effect=OSError()),
            patch.object(enrollment, "enrollment_http") as network,
            self.assertRaises(TTSError),
        ):
            self.manager.create(
                "admin", self.connection, self.preferences, {"upload_id": token}
            )
        network.assert_not_called()
        self.assertEqual(self.manager.jobs, {})

    async def test_query_filters_owner_and_confirms_model(self):
        token = self.upload()
        with patch.object(
            enrollment,
            "enrollment_http",
            return_value={"voice_id": self.connection["model"] + "-one"},
        ):
            self.manager.create(
                "admin", self.connection, self.preferences, {"upload_id": token}
            )
            await self.drain()
        with patch.object(
            enrollment,
            "enrollment_http",
            return_value={"target_model": self.connection["model"], "status": "OK"},
        ) as network:
            with self.assertRaises(TTSError):
                await self.manager.query("bob", self.connection, token)
            result = await self.manager.query("admin", self.connection, token)
            self.assertEqual(result["status"], "OK")
            with self.assertRaises(TTSError):
                await self.manager.query("admin", self.connection, token)
        self.assertEqual(network.call_count, 1)
        self.assertEqual(self.manager.visible_jobs("bob", self.connection), [])

    async def test_restart_marks_interrupted_attempt_unknown(self):
        token = self.upload()
        self.manager.jobs[token] = {"job_id": token, "status": "CREATING", "created": 1}
        self.manager.persist()
        loaded = enrollment.EnrollmentManager(self.manager.root)
        self.assertEqual(loaded.jobs[token]["status"], "UNKNOWN")
        self.assertEqual(list(self.manager.root.glob("upload_*.wav")), [])


class WavAndHTTPTests(unittest.TestCase):
    def test_wav_structure_and_metadata_stripping(self):
        raw = reference_wav()
        addition = b"JUNK" + struct.pack("<I", 4) + b"test"
        extended = raw + addition
        extended = extended[:4] + struct.pack("<I", len(extended) - 8) + extended[8:]
        clean, info = enrollment.validate_wav(extended)
        self.assertEqual(clean, raw)
        self.assertEqual(info["seconds"], 5)
        for invalid in (
            reference_wav(seconds=4),
            reference_wav(seconds=61),
            reference_wav(rate=8000),
            reference_wav(width=1),
            raw[:-4],
            raw + b"trailing",
            b"x" * 100,
        ):
            with self.subTest(size=len(invalid)), self.assertRaises(TTSError):
                enrollment.validate_wav(invalid)

    def test_fixed_endpoint_data_uri_and_redacted_http_errors(self):
        connection = {
            "api_key": "secret",
            "workspace": "ws-test.cn-beijing.maas.aliyuncs.com",
            "timeout": 60,
        }
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"output":{"voice_id":"example"}}'
        opener = Mock()
        opener.open.return_value = response
        with patch.object(enrollment, "build_opener", return_value=opener) as build:
            result = enrollment.enrollment_http(
                connection, "create_voice", audio="data:audio/wav;base64,AA=="
            )
        self.assertEqual(result["voice_id"], "example")
        self.assertIsInstance(build.call_args.args[0], enrollment.NoRedirect)
        req = opener.open.call_args.args[0]
        self.assertEqual(
            req.full_url,
            "https://ws-test.cn-beijing.maas.aliyuncs.com/api/v1/services/audio/tts/customization",
        )
        self.assertIn("data:audio/wav;base64", req.data.decode())
        opener.open.side_effect = HTTPError(
            req.full_url, 400, "bad", {}, io.BytesIO(b'{"message":"secret"}')
        )
        with (
            patch.object(enrollment, "build_opener", return_value=opener),
            self.assertRaises(TTSError) as error,
        ):
            enrollment.enrollment_http(connection, "create_voice")
        self.assertNotIn("secret", str(error.exception))


if __name__ == "__main__":
    unittest.main()
