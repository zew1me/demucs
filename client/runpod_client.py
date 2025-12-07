import argparse
import base64
import os
import pathlib
import sys
from typing import Dict, Any

import requests

DEFAULT_MODEL = "htdemucs_ft"
DEFAULT_SHIFTS = 4
DEFAULT_OVERLAP = 0.25
DEFAULT_API_BASE = "https://api.runpod.ai/v2"


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
        print(f"wrote {output_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Invoke the RunPod Demucs endpoint and save returned stems.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--api-key", default=os.getenv("RUNPOD_API_KEY"), help="RunPod API token")
    parser.add_argument("--endpoint-id", default=os.getenv("RUNPOD_ENDPOINT_ID"), help="RunPod endpoint ID")
    parser.add_argument(
        "--endpoint-url",
        default=os.getenv("RUNPOD_ENDPOINT_URL"),
        help="Optional full endpoint URL (skips --api-base/--endpoint-id)",
    )
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="Base URL for RunPod API")
    parser.add_argument("--input-url", help="Publicly reachable audio URL")
    parser.add_argument("--input-file", help="Local file to base64 upload if no URL is provided")
    parser.add_argument("--model-name", default=DEFAULT_MODEL)
    parser.add_argument("--shifts", type=int, default=DEFAULT_SHIFTS)
    parser.add_argument("--overlap", type=float, default=DEFAULT_OVERLAP)
    parser.add_argument("--timeout", type=int, default=900, help="HTTP timeout in seconds")
    parser.add_argument("--save-dir", default="runpod-stems", help="Destination directory for decoded WAVs")

    args = parser.parse_args(argv)

    if not args.api_key:
        parser.error("Set --api-key or RUNPOD_API_KEY")
    if not (args.endpoint_url or args.endpoint_id):
        parser.error("Provide --endpoint-url or --endpoint-id/RUNPOD_ENDPOINT_ID")
    if not (args.input_url or args.input_file):
        parser.error("Provide --input-url or --input-file")

    payload: Dict[str, Any] = {
        "model_name": args.model_name,
        "shifts": args.shifts,
        "overlap": args.overlap,
    }
    if args.input_url:
        payload["audio_url"] = args.input_url
    else:
        input_path = pathlib.Path(args.input_file).expanduser().resolve()
        if not input_path.exists():
            parser.error(f"Input file not found: {input_path}")
        payload["audio_base64"] = _encode_file(input_path)

    endpoint_url = args.endpoint_url
    if not endpoint_url:
        endpoint_url = f"{args.api_base.rstrip('/')}/{args.endpoint_id}/runsync"

    headers = {
        "Authorization": f"Bearer {args.api_key}",
        "Content-Type": "application/json",
    }

    response = requests.post(endpoint_url, json={"input": payload}, headers=headers, timeout=args.timeout)
    response.raise_for_status()
    body = response.json()

    output = body.get("output") or body
    status = body.get("status") or output.get("status")
    if status and status not in ("COMPLETED", "success", "SUCCESS"):
        print(f"RunPod returned status {status}: {output}", file=sys.stderr)
        return 1
    if output.get("status") == "error":
        print(f"Worker error: {output.get('error')}", file=sys.stderr)
        return 1

    stems = output.get("stems")
    if not stems:
        print("No stems returned", file=sys.stderr)
        return 1

    destination = pathlib.Path(args.save_dir).expanduser().resolve()
    _write_stems(stems, destination)
    print(f"Decoded {len(stems)} stems to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
