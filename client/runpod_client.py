import base64
import pathlib
from typing import Any, Dict, Optional

import requests
import typer
from dynaconf import Dynaconf

DEFAULT_MODEL = "htdemucs_ft"
DEFAULT_SHIFTS = 4
DEFAULT_OVERLAP = 0.25
DEFAULT_API_BASE = "https://api.runpod.ai/v2"

settings = Dynaconf(envvar_prefix="RUNPOD", environments=True, load_dotenv=True)

app = typer.Typer(help="Invoke the RunPod Demucs endpoint and save returned stems locally.")


def _encode_file(path: pathlib.Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def _write_stems(stems: Dict[str, Any], destination: pathlib.Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for key, value in stems.items():
        if isinstance(value, dict):
            filename = value.get("filename") or f"{key}.wav"
            blob = value.get("base64")
        else:
            filename = f"{key}.wav"
            blob = value
        if not blob:
            continue
        output_path = destination / filename
        output_path.write_bytes(base64.b64decode(blob))
        typer.echo(f"wrote {output_path}")


def _resolve_option(value: Optional[str], setting_key: str) -> Optional[str]:
    if value:
        return value
    resolved = settings.get(setting_key)
    if isinstance(resolved, str) and resolved.strip():
        return resolved
    return None


@app.command()
def main(
    api_key: Optional[str] = typer.Option(None, help="RunPod API token"),
    endpoint_id: Optional[str] = typer.Option(None, help="RunPod endpoint ID"),
    endpoint_url: Optional[str] = typer.Option(None, help="Override full RunPod endpoint URL"),
    api_base: str = typer.Option(DEFAULT_API_BASE, help="Base URL for RunPod API"),
    input_file: Optional[pathlib.Path] = typer.Option(None, help="Local file path to upload"),
    model_name: str = typer.Option(DEFAULT_MODEL),
    shifts: int = typer.Option(DEFAULT_SHIFTS),
    overlap: float = typer.Option(DEFAULT_OVERLAP),
    timeout: int = typer.Option(900, help="HTTP timeout in seconds"),
    save_dir: pathlib.Path = typer.Option(pathlib.Path("runpod-stems"), help="Destination directory"),
) -> None:
    """Call the RunPod Demucs worker via sync API and store returned stems."""

    resolved_api_key = _resolve_option(api_key, "API_KEY")
    if not resolved_api_key:
        raise typer.BadParameter("Set --api-key or configure RUNPOD_API_KEY", param_hint="--api-key")

    resolved_endpoint_url = _resolve_option(endpoint_url, "ENDPOINT_URL")
    resolved_endpoint_id = _resolve_option(endpoint_id, "ENDPOINT_ID")
    if not (resolved_endpoint_url or resolved_endpoint_id):
        raise typer.BadParameter(
            "Provide --endpoint-url or configure --endpoint-id/RUNPOD_ENDPOINT_ID",
            param_hint="--endpoint-url / --endpoint-id",
        )

    if not input_file:
        raise typer.BadParameter("Provide --input-file", param_hint="--input-file")

    input_path = input_file.expanduser().resolve()
    if not input_path.exists():
        raise typer.BadParameter(f"Input file not found: {input_path}", param_hint="--input-file")

    payload: Dict[str, Any] = {
        "model_name": model_name,
        "shifts": shifts,
        "overlap": overlap,
        "audio_base64": _encode_file(input_path),
    }

    target_url = resolved_endpoint_url or f"{api_base.rstrip('/')}/{resolved_endpoint_id}/runsync"
    headers = {"Authorization": f"Bearer {resolved_api_key}", "Content-Type": "application/json"}

    try:
        response = requests.post(target_url, json={"input": payload}, headers=headers, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        typer.secho(f"RunPod request failed: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    body = response.json()
    output = body.get("output") or body
    status = body.get("status") or output.get("status")
    if status and status not in ("COMPLETED", "success", "SUCCESS"):
        typer.secho(f"RunPod returned status {status}: {output}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)
    if output.get("status") == "error":
        typer.secho(f"Worker error: {output.get('error')}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    stems = output.get("stems")
    if not stems:
        typer.secho("No stems returned", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    destination = save_dir.expanduser().resolve()
    _write_stems(stems, destination)
    typer.secho(f"Decoded {len(stems)} stems to {destination}", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
