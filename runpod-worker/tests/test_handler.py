import base64
import importlib.util
import os
import pathlib
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "demucs_handler", pathlib.Path(__file__).parents[1] / "handler.py"
)
assert spec is not None and spec.loader is not None
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

WORKER_ENV = {
    "RUNPOD_R2_ENDPOINT": "https://worker-r2.example",
    "RUNPOD_R2_BUCKET": "worker-bucket",
    "RUNPOD_R2_ACCESS_KEY_ID": "worker-id",
    "RUNPOD_R2_SECRET_ACCESS_KEY": "worker-secret",
}
JOB_R2 = {
    "input_key": "runpod-demucs/run/input/song.wav",
    "output_prefix": "runpod-demucs/run/stems",
}


class WorkerR2Tests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, WORKER_ENV, clear=True))

    def test_worker_configuration_supplies_destination_and_credentials(self):
        config, error = worker._normalize_r2_config(JOB_R2)
        self.assertIsNone(error)
        assert config is not None
        self.assertEqual(config["bucket"], "worker-bucket")
        self.assertEqual(config["endpoint_url"], "https://worker-r2.example")
        self.assertEqual(config["access_key_id"], "worker-id")
        self.assertEqual(config["secret_access_key"], "worker-secret")
        self.assertEqual(config["region"], "auto")

    def test_caller_cannot_override_worker_configuration(self):
        for field in [
            "endpoint_url",
            "bucket",
            "access_key_id",
            "secret_access_key",
            "region",
        ]:
            with self.subTest(field=field), patch.object(
                worker, "_build_r2_client"
            ) as build:
                response = worker.handler(
                    {"input": {"r2": {**JOB_R2, field: "caller-value"}}}
                )
                self.assertEqual(response["status"], "error")
                build.assert_not_called()

    def test_rejects_keys_outside_permitted_scope_before_io(self):
        for field in ["input_key", "output_prefix"]:
            for value in [
                "other/run/stems",
                "runpod-demucs-other/stems",
                "runpod-demucs",
                "runpod-demucs/",
                "/runpod-demucs/stems",
                "runpod-demucs/../other",
                "",
                42,
            ]:
                with self.subTest(field=field, value=value), patch.object(
                    worker, "_build_r2_client"
                ) as build:
                    response = worker.handler(
                        {"input": {"r2": {**JOB_R2, field: value}}}
                    )
                    self.assertEqual(response["status"], "error")
                    self.assertIn("permitted R2 prefix", response["error"])
                    build.assert_not_called()

    def test_custom_worker_prefix_and_region(self):
        with patch.dict(
            os.environ,
            {"RUNPOD_R2_PREFIX": " /shared/base/ ", "RUNPOD_R2_REGION": "custom"},
        ):
            config, error = worker._normalize_r2_config(
                {
                    "input_key": "shared/base/run/input.wav",
                    "output_prefix": "shared/base/run/stems",
                }
            )
            self.assertIsNone(error)
            assert config is not None
            self.assertEqual(config["region"], "custom")
            _, error = worker._normalize_r2_config(JOB_R2)
            self.assertIsNotNone(error)

    def test_empty_worker_prefix_is_rejected(self):
        with patch.dict(os.environ, {"RUNPOD_R2_PREFIX": " /// "}):
            _, error = worker._normalize_r2_config(JOB_R2)
            self.assertIn("must not be empty", error)

    def test_missing_worker_secrets_fail_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            response = worker.handler({"input": {"r2": JOB_R2}})
        self.assertEqual(response["status"], "error")
        self.assertIn("RUNPOD_R2_ENDPOINT", response["error"])

    @staticmethod
    def run_demucs(command, **kwargs):
        root = pathlib.Path(command[command.index("--out") + 1])
        model = command[command.index("--name") + 1]
        stems = root / model / "audio_file"
        stems.mkdir(parents=True)
        (stems / "vocals.wav").write_bytes(b"stem fixture")

    def test_r2_flow_uses_worker_endpoint_bucket_and_scoped_keys(self):
        r2 = Mock()

        def download(bucket, key, path):
            pathlib.Path(path).write_bytes(b"audio fixture")

        r2.download_file.side_effect = download
        with patch.object(
            worker.boto3, "client", return_value=r2
        ) as build, patch.object(worker.subprocess, "run", side_effect=self.run_demucs):
            response = worker.handler({"input": {"r2": JOB_R2}})
        self.assertEqual(response["status"], "success", response)
        self.assertEqual(
            build.call_args.kwargs["endpoint_url"], WORKER_ENV["RUNPOD_R2_ENDPOINT"]
        )
        self.assertEqual(
            build.call_args.kwargs["aws_secret_access_key"],
            WORKER_ENV["RUNPOD_R2_SECRET_ACCESS_KEY"],
        )
        self.assertEqual(
            r2.download_file.call_args.args[:2], ("worker-bucket", JOB_R2["input_key"])
        )
        self.assertEqual(
            r2.upload_file.call_args.args[1:],
            ("worker-bucket", "runpod-demucs/run/stems/vocals.wav"),
        )
        self.assertEqual(
            response["stems"]["vocals"]["r2_key"], "runpod-demucs/run/stems/vocals.wav"
        )
        self.assertEqual(
            response["r2_output"],
            {"bucket": "worker-bucket", "prefix": JOB_R2["output_prefix"]},
        )
        self.assertNotIn("worker-secret", str(response))

    def test_base64_flow_needs_no_r2_configuration(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
            worker.subprocess, "run", side_effect=self.run_demucs
        ), patch.object(worker, "_build_r2_client") as build:
            response = worker.handler(
                {"input": {"audio_base64": base64.b64encode(b"audio fixture").decode()}}
            )
        self.assertEqual(response["status"], "success", response)
        self.assertEqual(
            base64.b64decode(response["stems"]["vocals"]["base64"]), b"stem fixture"
        )
        build.assert_not_called()
