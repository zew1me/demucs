import base64
import os
import pathlib
import subprocess
import tempfile
from typing import Any, Dict, cast

import requests

try:
    import runpod  # type: ignore[import]
except ModuleNotFoundError:  # pragma: no cover - runpod only exists in the worker image
    runpod = cast(Any, None)

INPUT_KEY_FILE = "audio_file.wav"
DEFAULT_MODEL = "htdemucs_ft"
DEFAULT_SHIFTS = 4
DEFAULT_OVERLAP = 0.25


def _download_audio(audio_url: str, destination: pathlib.Path) -> None:
    response = requests.get(audio_url, stream=True, timeout=120)
    response.raise_for_status()
    with destination.open("wb") as file_handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                file_handle.write(chunk)


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


def _find_stems_dir(out_root: pathlib.Path, model_name: str) -> pathlib.Path:
    model_root = out_root / model_name
    if not model_root.exists():
        raise FileNotFoundError(f"Demucs output missing for model '{model_name}'")
    candidates = [child for child in model_root.iterdir() if child.is_dir()]
    if not candidates:
        raise FileNotFoundError("No separated stems were produced")
    return candidates[0]


def handler(event: Dict[str, Any]) -> Dict[str, Any]:
    inputs = event.get("input") or {}

    audio_url_raw = inputs.get("audio_url")
    audio_url = audio_url_raw if isinstance(audio_url_raw, str) else None
    audio_b64_raw = inputs.get("audio_base64")
    audio_b64 = audio_b64_raw if isinstance(audio_b64_raw, str) else None
    model_name = inputs.get("model_name", DEFAULT_MODEL)
    shifts = int(inputs.get("shifts", DEFAULT_SHIFTS))
    overlap = float(inputs.get("overlap", DEFAULT_OVERLAP))

    if not (audio_url or audio_b64):
        return {"status": "error", "error": "Provide 'audio_url' or 'audio_base64'"}

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = pathlib.Path(tmp_dir)
        audio_path = tmp_path / INPUT_KEY_FILE
        output_root = tmp_path / "separations"

        try:
            if audio_url:
                _download_audio(audio_url, audio_path)
            elif audio_b64:
                _write_base64_audio(audio_b64, audio_path)
            else:  # safeguard for typing
                return {"status": "error", "error": "Provide 'audio_url' or 'audio_base64'"}

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
            stems_payload = _collect_stems(stems_dir)

            return {
                "status": "success",
                "model": model_name,
                "shifts": shifts,
                "overlap": overlap,
                "stem_count": len(stems_payload),
                "stems": stems_payload,
            }
        except Exception as exc:  # pylint: disable=broad-except
            return {"status": "error", "error": str(exc)}


if runpod:
    runpod.serverless.start({"handler": handler})
