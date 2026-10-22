import base64
import os
import pathlib
import subprocess
import tempfile
from typing import Any, Dict, Optional, cast

import boto3
from botocore.config import Config

try:
    import runpod  # type: ignore[import]
except ModuleNotFoundError:  # pragma: no cover - runpod only exists in the worker image
    runpod = cast(Any, None)

INPUT_KEY_FILE = "audio_file.wav"
DEFAULT_MODEL = "htdemucs_ft"
DEFAULT_SHIFTS = 4
DEFAULT_OVERLAP = 0.25
R2_REQUIRED_FIELDS = ("endpoint_url", "access_key_id", "secret_access_key", "bucket")


def _write_base64_audio(audio_b64: str, destination: pathlib.Path) -> None:
    data = base64.b64decode(audio_b64)
    destination.write_bytes(data)


def _collect_stems(output_dir: pathlib.Path) -> Dict[str, Dict[str, str]]:
    stems: Dict[str, Dict[str, str]] = {}
    for wav_file in output_dir.glob("*.wav"):
        stems[wav_file.stem] = {
            "filename": wav_file.name,
            "base64": base64.b64encode(wav_file.read_bytes()).decode("utf-8"),
        }
    return stems


def _build_r2_client(r2_config: Dict[str, str]) -> Any:
    config = Config(signature_version="s3v4", s3={"addressing_style": "path"})
    return boto3.client(
        "s3",
        endpoint_url=r2_config["endpoint_url"],
        aws_access_key_id=r2_config["access_key_id"],
        aws_secret_access_key=r2_config["secret_access_key"],
        region_name=r2_config.get("region") or "auto",
        config=config,
    )


def _download_r2_object(client: Any, bucket: str, key: str, destination: pathlib.Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    client.download_file(bucket, key, str(destination))


def _upload_stems_to_r2(
    client: Any, bucket: str, output_prefix: str, stems_dir: pathlib.Path
) -> Dict[str, Dict[str, str]]:
    stems: Dict[str, Dict[str, str]] = {}
    prefix = output_prefix.strip("/")
    for wav_file in stems_dir.glob("*.wav"):
        key = f"{prefix}/{wav_file.name}"
        client.upload_file(str(wav_file), bucket, key, ExtraArgs={"ContentType": "audio/wav"})
        stems[wav_file.stem] = {"filename": wav_file.name, "r2_key": key}
    return stems


def _find_stems_dir(out_root: pathlib.Path, model_name: str) -> pathlib.Path:
    model_root = out_root / model_name
    if not model_root.exists():
        raise FileNotFoundError(f"Demucs output missing for model '{model_name}'")
    candidates = [child for child in model_root.iterdir() if child.is_dir()]
    if not candidates:
        raise FileNotFoundError("No separated stems were produced")
    return candidates[0]


def _normalize_r2_config(raw: Any) -> tuple[Optional[Dict[str, str]], Optional[str]]:
    if not isinstance(raw, dict):
        return None, None
    if any(field in raw for field in (*R2_REQUIRED_FIELDS, "region")):
        return None, "R2 endpoint, credentials, bucket and region must be configured on the worker"
    normalized: Dict[str, str] = {}
    missing = []
    for field in R2_REQUIRED_FIELDS:
        env_key = f"RUNPOD_R2_{'ENDPOINT' if field == 'endpoint_url' else field.upper()}"
        value = os.environ.get(env_key)
        if value and value.strip():
            normalized[field] = value
        else:
            missing.append(env_key)
    if missing:
        return None, f"Missing worker R2 settings: {', '.join(missing)}"
    normalized["region"] = os.environ.get("RUNPOD_R2_REGION") or "auto"
    allowed_prefix = os.environ.get("RUNPOD_R2_PREFIX", "runpod-demucs").strip().strip("/")
    if not allowed_prefix:
        return None, "Worker RUNPOD_R2_PREFIX must not be empty"
    for field in ("input_key", "output_prefix"):
        value = raw.get(field)
        if value is None:
            continue
        if (
            not isinstance(value, str)
            or not value.startswith(f"{allowed_prefix}/")
            or not value[len(allowed_prefix) + 1 :].strip("/")
            or any(part in (".", "..") for part in value.split("/"))
        ):
            return None, f"r2.{field} must be within the worker's permitted R2 prefix"
        normalized[field] = value
    return normalized, None


def handler(event: Dict[str, Any]) -> Dict[str, Any]:
    inputs = event.get("input") or {}

    audio_b64_raw = inputs.get("audio_base64")
    audio_b64 = audio_b64_raw if isinstance(audio_b64_raw, str) else None
    r2_config, r2_error = _normalize_r2_config(inputs.get("r2"))
    model_name = inputs.get("model_name", DEFAULT_MODEL)
    shifts = int(inputs.get("shifts", DEFAULT_SHIFTS))
    overlap = float(inputs.get("overlap", DEFAULT_OVERLAP))

    if r2_error:
        return {"status": "error", "error": r2_error}

    if not (audio_b64 or (r2_config and r2_config.get("input_key"))):
        return {
            "status": "error",
            "error": "Provide 'audio_base64' or r2.input_key",
        }

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = pathlib.Path(tmp_dir)
        audio_path = tmp_path / INPUT_KEY_FILE
        output_root = tmp_path / "separations"

        try:
            r2_client = _build_r2_client(r2_config) if r2_config else None
            if r2_config and r2_config.get("input_key"):
                if not r2_client:
                    raise RuntimeError("R2 client configuration failed")
                _download_r2_object(
                    r2_client,
                    r2_config["bucket"],
                    r2_config["input_key"],
                    audio_path,
                )
            elif audio_b64:
                _write_base64_audio(audio_b64, audio_path)
            else:  # safeguard for typing
                return {"status": "error", "error": "Provide 'audio_base64' or r2.input_key"}

            env = os.environ.copy()
            env.setdefault("TORCHAUDIO_USE_SOUND_FILE", "1")

            cmd = [
                "demucs",
                "--name",
                model_name,
                "--shifts",
                str(shifts),
                "--overlap",
                str(overlap),
                "--out",
                str(output_root),
                str(audio_path),
            ]

            subprocess.run(cmd, check=True, env=env, cwd=tmp_dir)

            stems_dir = _find_stems_dir(output_root, model_name)
            if r2_config and r2_config.get("output_prefix"):
                if not r2_client:
                    raise RuntimeError("R2 client configuration failed")
                stems_payload = _upload_stems_to_r2(
                    r2_client,
                    r2_config["bucket"],
                    r2_config["output_prefix"],
                    stems_dir,
                )
                r2_output = {
                    "bucket": r2_config["bucket"],
                    "prefix": r2_config["output_prefix"].strip("/"),
                }
            else:
                stems_payload = _collect_stems(stems_dir)
                r2_output = None

            response: Dict[str, Any] = {
                "status": "success",
                "model": model_name,
                "shifts": shifts,
                "overlap": overlap,
                "stem_count": len(stems_payload),
                "stems": stems_payload,
            }
            if r2_output:
                response["r2_output"] = r2_output
            return response
        except Exception as exc:  # pylint: disable=broad-except
            return {"status": "error", "error": str(exc)}


if runpod:
    runpod.serverless.start({"handler": handler})
