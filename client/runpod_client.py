import base64
import pathlib
import uuid
from collections.abc import Mapping
from typing import Any, Dict, Iterable, Optional, Tuple

import boto3
import requests
import typer
from botocore.config import Config
from dynaconf import Dynaconf

DEFAULT_MODEL = "htdemucs_ft"
DEFAULT_SHIFTS = 4
DEFAULT_OVERLAP = 0.25
DEFAULT_API_BASE = "https://api.runpod.ai/v2"

settings = Dynaconf(envvar_prefix="RUNPOD", environments=True, load_dotenv=True)

app = typer.Typer(help="Invoke the RunPod Demucs endpoint and save returned stems locally.")


def _encode_file(path: pathlib.Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def _write_stems(stems: Mapping[str, Any], destination: pathlib.Path) -> None:
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


def _build_r2_client(
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    region: Optional[str],
) -> Any:
    config = Config(signature_version="s3v4", s3={"addressing_style": "path"})
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name=region or "auto",
        config=config,
    )


def _download_r2_stems(
    client: Any,
    bucket: str,
    stems: Mapping[str, Any],
    destination: pathlib.Path,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for stem_name, payload in stems.items():
        if not isinstance(payload, dict):
            continue
        key = payload.get("r2_key")
        filename = payload.get("filename") or f"{stem_name}.wav"
        if not key:
            continue
        output_path = destination / filename
        client.download_file(bucket, key, str(output_path))
        typer.echo(f"downloaded {output_path}")


def _delete_r2_objects(client: Any, bucket: str, keys: Iterable[str]) -> None:
    objects = [{"Key": key} for key in keys if key]
    if not objects:
        return
    errors = []
    for idx in range(0, len(objects), 1000):
        batch = objects[idx : idx + 1000]
        response = client.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
        errors.extend(response.get("Errors", []))
    if errors:
        raise RuntimeError(f"R2 object deletion failed: {errors}")


def _resolve_option(value: Optional[str], setting_key: str) -> Optional[str]:
    if value:
        return value
    resolved = settings.get(setting_key)
    if isinstance(resolved, str) and resolved.strip():
        return resolved
    return None


def _resolve_r2_settings(
    endpoint: Optional[str],
    bucket: Optional[str],
    access_key_id: Optional[str],
    secret_access_key: Optional[str],
    region: Optional[str],
    prefix: Optional[str],
) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    resolved_endpoint = _resolve_option(endpoint, "R2_ENDPOINT")
    resolved_bucket = _resolve_option(bucket, "R2_BUCKET")
    resolved_access_key_id = _resolve_option(access_key_id, "R2_ACCESS_KEY_ID")
    resolved_secret_access_key = _resolve_option(secret_access_key, "R2_SECRET_ACCESS_KEY")
    resolved_region = _resolve_option(region, "R2_REGION")
    resolved_prefix = _resolve_option(prefix, "R2_PREFIX")

    has_any = any(
        [
            resolved_endpoint,
            resolved_bucket,
            resolved_access_key_id,
            resolved_secret_access_key,
            resolved_region,
            resolved_prefix,
        ]
    )
    if not has_any:
        return None, None

    missing = [
        name
        for name, value in [
            ("--r2-endpoint", resolved_endpoint),
            ("--r2-bucket", resolved_bucket),
            ("--r2-access-key-id", resolved_access_key_id),
            ("--r2-secret-access-key", resolved_secret_access_key),
        ]
        if not value
    ]
    if (
        missing
        or not resolved_endpoint
        or not resolved_bucket
        or not resolved_access_key_id
        or not resolved_secret_access_key
    ):
        return None, f"Missing required R2 settings: {', '.join(missing)}"

    base_prefix = (resolved_prefix or "runpod-demucs").strip().strip("/")
    resolved_prefix = f"{base_prefix}/{uuid.uuid4().hex}" if base_prefix else uuid.uuid4().hex

    return (
        {
            "endpoint_url": resolved_endpoint,
            "bucket": resolved_bucket,
            "access_key_id": resolved_access_key_id,
            "secret_access_key": resolved_secret_access_key,
            "region": resolved_region or "auto",
            "prefix": resolved_prefix,
        },
        None,
    )


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
    r2_endpoint: Optional[str] = typer.Option(None, help="Cloudflare R2 endpoint URL"),
    r2_bucket: Optional[str] = typer.Option(None, help="Cloudflare R2 bucket"),
    r2_access_key_id: Optional[str] = typer.Option(None, help="Cloudflare R2 access key id"),
    r2_secret_access_key: Optional[str] = typer.Option(None, help="Cloudflare R2 secret access key"),
    r2_region: Optional[str] = typer.Option(None, help="Cloudflare R2 region (default: auto)"),
    r2_prefix: Optional[str] = typer.Option(None, help="Cloudflare R2 base key prefix"),
    r2_download: bool = typer.Option(
        True, "--r2-download/--no-r2-download", help="Download stems from R2 when returned"
    ),
    r2_cleanup: bool = typer.Option(False, help="Delete R2 input and downloaded stems"),
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

    r2_settings, r2_error = _resolve_r2_settings(
        r2_endpoint,
        r2_bucket,
        r2_access_key_id,
        r2_secret_access_key,
        r2_region,
        r2_prefix,
    )
    if r2_error:
        raise typer.BadParameter(r2_error, param_hint="--r2-endpoint")

    payload: Dict[str, Any] = {
        "model_name": model_name,
        "shifts": shifts,
        "overlap": overlap,
    }
    r2_client = None
    input_key = None
    output_prefix = None
    if r2_settings:
        r2_client = _build_r2_client(
            r2_settings["endpoint_url"],
            r2_settings["access_key_id"],
            r2_settings["secret_access_key"],
            r2_settings.get("region"),
        )
        input_key = f"{r2_settings['prefix']}/input/{input_path.name}"
        output_prefix = f"{r2_settings['prefix']}/stems"
        try:
            r2_client.upload_file(str(input_path), r2_settings["bucket"], input_key)
        except Exception as exc:  # pylint: disable=broad-except
            typer.secho(f"R2 upload failed: {exc}", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1) from exc
        payload["r2"] = {
            "input_key": input_key,
            "output_prefix": output_prefix,
        }
    else:
        payload["audio_base64"] = _encode_file(input_path)

    target_url = resolved_endpoint_url or f"{api_base.rstrip('/')}/{resolved_endpoint_id}/runsync"
    headers = {"Authorization": f"Bearer {resolved_api_key}", "Content-Type": "application/json"}

    try:
        try:
            response = requests.post(target_url, json={"input": payload}, headers=headers, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            typer.secho(f"RunPod request failed: {exc}", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1) from exc

        try:
            body = response.json()
        except ValueError as exc:
            typer.secho(f"Invalid RunPod JSON response: {exc}", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1) from exc
        if not isinstance(body, Mapping):
            typer.secho("Invalid RunPod response: expected a mapping", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1)
        output = body.get("output", body)
        if not isinstance(output, Mapping):
            typer.secho("Invalid RunPod output: expected a mapping", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1)
        status = body.get("status") or output.get("status")
        if status and status not in ("COMPLETED", "success", "SUCCESS"):
            typer.secho(f"RunPod returned status {status}: {output}", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1)
        if output.get("status") == "error":
            typer.secho(f"Worker error: {output.get('error')}", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1)

        stems = output.get("stems")
        if not isinstance(stems, Mapping) or not stems:
            typer.secho("No stems returned: expected a non-empty mapping", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1)
    except Exception:
        if r2_cleanup and r2_client and r2_settings and input_key:
            try:
                _delete_r2_objects(r2_client, r2_settings["bucket"], [input_key])
            except Exception as exc:  # preserve the original run failure
                typer.secho(f"R2 input cleanup failed: {exc}", err=True, fg=typer.colors.RED)
        raise

    destination = save_dir.expanduser().resolve()
    using_r2 = any(isinstance(value, dict) and value.get("r2_key") for value in stems.values())
    if using_r2:
        if not (r2_settings and r2_client):
            typer.secho("Worker returned R2 locations but no R2 settings were provided.", err=True)
            raise typer.Exit(code=1)
        r2_output = output.get("r2_output")
        if isinstance(r2_output, dict):
            bucket = r2_output.get("bucket")
            prefix = r2_output.get("prefix")
            if bucket and prefix:
                typer.secho(f"R2 output: s3://{bucket}/{prefix}", fg=typer.colors.CYAN)
        if r2_download:
            _download_r2_stems(r2_client, r2_settings["bucket"], stems, destination)
            typer.secho(f"Downloaded {len(stems)} stems to {destination}", fg=typer.colors.GREEN)
        else:
            typer.secho("Stems stored in R2; skipping download (--no-r2-download).", fg=typer.colors.YELLOW)
        if r2_cleanup:
            keys = [input_key] if input_key else []
            if r2_download:
                keys.extend(
                    value.get("r2_key")
                    for value in stems.values()
                    if isinstance(value, dict) and value.get("r2_key")
                )
            else:
                typer.secho("Keeping stem objects in R2 because download is disabled.", fg=typer.colors.YELLOW)
            _delete_r2_objects(r2_client, r2_settings["bucket"], keys)
            typer.secho("Deleted R2 input and any downloaded stems for this run.", fg=typer.colors.GREEN)
    else:
        _write_stems(stems, destination)
        typer.secho(f"Decoded {len(stems)} stems to {destination}", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
