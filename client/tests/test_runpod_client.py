import base64
import pathlib
import tempfile
import unittest
import uuid
from contextlib import ExitStack
from unittest.mock import Mock, patch

import requests
from typer.testing import CliRunner

from client import runpod_client as client


class R2HelpersTests(unittest.TestCase):
    def test_delete_collects_errors_from_all_batches(self):
        r2 = Mock()
        r2.delete_objects.side_effect = [
            {"Errors": [{"Key": "0", "Code": "AccessDenied"}]},
            {},
            {"Errors": [{"Key": "2000", "Code": "InternalError"}]},
        ]
        with self.assertRaises(RuntimeError) as raised:
            client._delete_r2_objects(r2, "bucket", (str(i) for i in range(2001)))
        self.assertIn("AccessDenied", str(raised.exception))
        self.assertIn("InternalError", str(raised.exception))
        batches = r2.delete_objects.call_args_list
        self.assertEqual(
            [len(call.kwargs["Delete"]["Objects"]) for call in batches], [1000, 1000, 1]
        )
        self.assertTrue(all(call.kwargs["Bucket"] == "bucket" for call in batches))
        self.assertTrue(all(call.kwargs["Delete"]["Quiet"] for call in batches))

    def test_empty_delete_does_not_call_r2(self):
        r2 = Mock()
        client._delete_r2_objects(r2, "bucket", ["", ""])
        r2.delete_objects.assert_not_called()

    def test_successful_delete(self):
        r2 = Mock()
        r2.delete_objects.return_value = {}
        client._delete_r2_objects(r2, "bucket", ["input", "", "stem"])
        self.assertEqual(
            r2.delete_objects.call_args.kwargs["Delete"]["Objects"],
            [{"Key": "input"}, {"Key": "stem"}],
        )

    def test_prefixes_are_normalized_and_unique(self):
        with patch.object(client, "settings", Mock(get=Mock(return_value=None))):
            for prefix, base in [
                (None, "runpod-demucs"),
                (" /custom/base/ ", "custom/base"),
                ("///", ""),
            ]:
                with self.subTest(prefix=prefix):
                    resolved = []
                    for _ in range(2):
                        config, error = client._resolve_r2_settings(
                            "https://r2.example",
                            "bucket",
                            "local-id",
                            "local-secret",
                            None,
                            prefix,
                        )
                        self.assertIsNone(error)
                        assert config is not None
                        actual = config["prefix"]
                        self.assertTrue(
                            actual.startswith(f"{base}/") if base else "/" not in actual
                        )
                        self.assertEqual(
                            uuid.UUID(actual.rsplit("/", 1)[-1]).hex,
                            actual.rsplit("/", 1)[-1],
                        )
                        resolved.append(actual)
                    self.assertNotEqual(*resolved)

    def test_environment_prefix_also_gets_run_segment(self):
        with patch.object(
            client,
            "settings",
            Mock(
                get=Mock(
                    side_effect=lambda key: "/shared/" if key == "R2_PREFIX" else None
                )
            ),
        ):
            config, error = client._resolve_r2_settings(
                "https://r2.example", "bucket", "local-id", "local-secret", None, None
            )
        self.assertIsNone(error)
        assert config is not None
        self.assertTrue(config["prefix"].startswith("shared/"))
        self.assertEqual(len(config["prefix"].split("/")), 2)


class R2FlowTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = pathlib.Path(temporary.name).resolve()
        self.input_path = self.directory / "song.wav"
        self.input_path.write_bytes(b"audio fixture")
        self.r2 = Mock()
        self.r2.delete_objects.return_value = {}
        self.stems = {
            "vocals": {
                "filename": "vocals.wav",
                "r2_key": "runpod-demucs/run/stems/vocals.wav",
            }
        }
        self.response = Mock()
        self.response.json.return_value = {
            "status": "COMPLETED",
            "output": {"status": "success", "stems": self.stems},
        }
        self.post = self.stack.enter_context(
            patch.object(client.requests, "post", return_value=self.response)
        )
        self.stack.enter_context(
            patch.object(client, "_build_r2_client", return_value=self.r2)
        )
        self.stack.enter_context(
            patch.object(client, "settings", Mock(get=Mock(return_value=None)))
        )

    def invoke(self, *flags):
        return CliRunner().invoke(
            client.app,
            [
                "--api-key",
                "test-token",
                "--endpoint-id",
                "test-endpoint",
                "--input-file",
                str(self.input_path),
                "--save-dir",
                str(self.directory / "stems"),
                "--r2-endpoint",
                "https://r2.example",
                "--r2-bucket",
                "bucket",
                "--r2-access-key-id",
                "local-id",
                "--r2-secret-access-key",
                "local-secret",
                *flags,
            ],
        )

    def deleted_keys(self):
        return [
            entry["Key"]
            for entry in self.r2.delete_objects.call_args.kwargs["Delete"]["Objects"]
        ]

    def test_payload_contains_only_object_keys(self):
        result = self.invoke("--no-r2-download")
        self.assertEqual(result.exit_code, 0, result.output)
        payload = self.post.call_args.kwargs["json"]["input"]
        self.assertEqual(set(payload["r2"]), {"input_key", "output_prefix"})
        self.assertEqual(
            payload["r2"]["input_key"], self.r2.upload_file.call_args.args[2]
        )
        self.assertNotIn("local-secret", str(payload))
        self.assertNotIn("https://r2.example", str(payload))
        self.r2.delete_objects.assert_not_called()

    def test_success_downloads_and_deletes_input_and_outputs(self):
        result = self.invoke("--r2-cleanup")
        self.assertEqual(result.exit_code, 0, result.output)
        self.r2.download_file.assert_called_once_with(
            "bucket",
            self.stems["vocals"]["r2_key"],
            str(self.directory / "stems" / "vocals.wav"),
        )
        self.assertEqual(
            self.deleted_keys(),
            [self.r2.upload_file.call_args.args[2], self.stems["vocals"]["r2_key"]],
        )

    def test_without_download_cleanup_preserves_stems(self):
        result = self.invoke("--no-r2-download", "--r2-cleanup")
        self.assertEqual(result.exit_code, 0, result.output)
        self.r2.download_file.assert_not_called()
        self.assertEqual(self.deleted_keys(), [self.r2.upload_file.call_args.args[2]])
        self.assertIn("Keeping stem objects in R2", result.output)

    def test_unwrapped_worker_response_succeeds(self):
        self.response.json.return_value = self.response.json.return_value["output"]
        result = self.invoke("--r2-cleanup")
        self.assertEqual(result.exit_code, 0, result.output)
        self.r2.download_file.assert_called_once()
        self.assertEqual(
            self.deleted_keys(),
            [self.r2.upload_file.call_args.args[2], self.stems["vocals"]["r2_key"]],
        )

    def test_base64_stems_are_written(self):
        encoded = base64.b64encode(b"stem fixture").decode()
        for wrapped in [False, True]:
            with self.subTest(wrapped=wrapped):
                output = {
                    "status": "success",
                    "stems": {
                        "vocals": {"filename": "voice.wav", "base64": encoded},
                        "drums": encoded + "\n",
                    },
                }
                self.response.json.return_value = {"output": output} if wrapped else output
                result = self.invoke()
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn("Decoded 2 stems", result.output)
                for filename in ["voice.wav", "drums.wav"]:
                    self.assertEqual(
                        (self.directory / "stems" / filename).read_bytes(), b"stem fixture"
                    )
                self.r2.download_file.assert_not_called()

    def test_malformed_response_cleanup_is_opt_in(self):
        cases: list[tuple[ValueError | None, object, str]] = [
            (ValueError("invalid JSON"), None, "Invalid RunPod JSON response")
        ]
        for value in [None, [], ["invalid"], "invalid", 1, False]:
            cases.append((None, value, "Invalid RunPod response"))
            cases.append(
                (
                    None,
                    {"output": value, "stems": self.stems},
                    "Invalid RunPod output",
                )
            )
        for value in [None, {}, [], ["invalid"], "invalid", 1, False]:
            cases.append((None, {"output": {"stems": value}}, "No stems returned"))
        invalid_values = [None, "", " ", 0, 1, False, True, [], ["audio"], {}]
        invalid_audio = [*invalid_values, "!!!", "invalid", "☃"]
        invalid_stems = [*invalid_audio, {"filename": "vocals.wav"}]
        invalid_stems.extend({"base64": value} for value in invalid_audio)
        invalid_stems.extend({"r2_key": value} for value in invalid_values)
        valid_stem_groups: list[dict[str, object]] = [{}, {"drums": "c3RlbQ=="}, dict(self.stems)]
        for value in invalid_stems:
            for valid_stems in valid_stem_groups:
                cases.append(
                    (
                        None,
                        {"output": {"stems": {**valid_stems, "bass": value}}},
                        "Invalid RunPod stem",
                    )
                )
        cases.append(
            (
                None,
                {"output": {"stems": {**self.stems, "drums": "c3RlbQ=="}}},
                "Invalid RunPod stem",
            )
        )
        for json_error, body, message in cases:
            for cleanup in [False, True]:
                with self.subTest(json_error=json_error, body=body, cleanup=cleanup):
                    self.r2.reset_mock()
                    self.response.json.side_effect = json_error
                    self.response.json.return_value = body
                    result = self.invoke(*(["--r2-cleanup"] if cleanup else []))
                    self.assertEqual(result.exit_code, 1, result.output)
                    self.assertIsInstance(result.exception, SystemExit)
                    self.assertIn(message, result.output)
                    self.assertNotIn("Decoded", result.output)
                    self.assertNotIn("Downloaded", result.output)
                    self.r2.upload_file.assert_called_once()
                    self.r2.download_file.assert_not_called()
                    self.assertFalse((self.directory / "stems").exists())
                    if cleanup:
                        self.r2.delete_objects.assert_called_once()
                        self.assertEqual(
                            self.deleted_keys(), [self.r2.upload_file.call_args.args[2]]
                        )
                    else:
                        self.r2.delete_objects.assert_not_called()

    def test_failure_cleanup_is_opt_in(self):
        cases = [
            (requests.ConnectionError("offline"), None, None),
            (None, requests.HTTPError("bad gateway"), None),
            (None, None, {"status": "FAILED", "output": {"error": "failed"}}),
            (
                None,
                None,
                {
                    "status": "COMPLETED",
                    "output": {"status": "error", "error": "failed"},
                },
            ),
            (None, None, {"status": "COMPLETED", "output": {"stems": {}}}),
        ]
        for request_error, http_error, body in cases:
            for cleanup in [False, True]:
                with self.subTest(
                    request_error=request_error,
                    http_error=http_error,
                    body=body,
                    cleanup=cleanup,
                ):
                    self.r2.reset_mock()
                    self.post.side_effect = request_error
                    self.response.raise_for_status.side_effect = http_error
                    self.response.json.return_value = body
                    result = self.invoke(*(["--r2-cleanup"] if cleanup else []))
                    self.assertEqual(result.exit_code, 1, result.output)
                    self.r2.upload_file.assert_called_once()
                    if cleanup:
                        self.r2.delete_objects.assert_called_once()
                        self.assertEqual(
                            self.deleted_keys(), [self.r2.upload_file.call_args.args[2]]
                        )
                    else:
                        self.r2.delete_objects.assert_not_called()

    def test_cleanup_failure_reports_error_and_preserves_run_failure(self):
        self.post.side_effect = requests.ConnectionError("offline")
        self.r2.delete_objects.return_value = {
            "Errors": [{"Key": "input", "Code": "AccessDenied"}]
        }
        result = self.invoke("--r2-cleanup")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("RunPod request failed", result.output)
        self.assertIn("R2 input cleanup failed", result.output)
        self.assertIn("AccessDenied", result.output)

    def test_upload_failure_does_not_submit_or_delete(self):
        self.r2.upload_file.side_effect = RuntimeError("upload failed")
        result = self.invoke("--r2-cleanup")
        self.assertEqual(result.exit_code, 1)
        self.post.assert_not_called()
        self.r2.delete_objects.assert_not_called()
