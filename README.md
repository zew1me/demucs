# Demucs serverless toolkit

This repository now acts as a monorepo for three complementary flows that all rely on the root-level `pyproject.toml`:

1. **`local-run/`** – documentation plus a scratch space for running the official Demucs CLI locally (outputs land here).
2. **`runpod-worker/`** – the GPU-ready serverless worker that RunPod clones, builds, and deploys.
3. **`client/`** – a thin command-line helper for hitting your RunPod endpoint and saving the returned stems.

## Repository layout

| Path | Purpose |
| --- | --- |
| `local-run/` | Docs + separation outputs for local experimentation; uses the root `pyproject.toml`. |
| `runpod-worker/` | Dockerfile, handler, and `runpod.yaml` describing the serverless worker. |
| `client/` | Python CLI (`runpod-demucs`) that calls the deployed endpoint and writes WAVs. |
| `AGENTS.md` | Contributor and agent workflow guide for this repo. |

UV is configured (via `uv.toml`) to keep its cache in `.uv/cache`, ensuring everything lives inside the repo; the `.uv/` directory is ignored by git.

## Pre-commit + linting

We gate merges with a `pre-commit` job that runs the standard formatting hooks plus `ruff` and `pyrefly` type checking. Run these locally before pushing:

```bash
uv run pre-commit install      # one-time git hook install
uv run pre-commit run --all-files
```

CI mirrors the same command in `.github/workflows/pre-commit.yml`. Mark the resulting **pre-commit** GitHub check as required in branch protections so pull requests must pass before merging into `master`/`main`.

## Local development (`local-run/`)

The existing workflow moved intact under `local-run/`, but everything is powered by the repo-root `pyproject.toml`. Quick start:

```bash
cd local-run
uv sync
TORCHAUDIO_USE_SOUND_FILE=1 uv run demucs --name htdemucs ~/Downloads/song.wav
```

Outputs land in `local-run/separations/<timestamp>/...`. The subdirectory README covers the details.

## RunPod serverless worker (`runpod-worker/`)

Key files:

- `Dockerfile` – installs CUDA-enabled PyTorch 2.4.1/torchaudio 2.4.1, Demucs, ffmpeg, and the RunPod SDK.
- `handler.py` – downloads the audio (URL or base64), runs `demucs --name <model> --shifts --overlap`, base64-encodes the stems, and returns them.
- `runpod.yaml` – instructs RunPod to launch `handler.py` and call its `handler` function.

Build/test locally:

```bash
cd runpod-worker
docker build -t demucs-worker .
docker run --rm demucs-worker  # RunPod will pass events, but this validates the image
```

Deploy checklist:

1. Push this repo to GitHub (public or private works).
2. In the RunPod dashboard choose **Deploy Serverless Endpoint → Connect GitHub** and select the repo.
3. Provide any required environment variables (none are mandatory today) and deploy.
4. RunPod returns an endpoint ID such as `8cw1xzsn9rmbti`. Use it with `client/runpod_client.py` or plain curl.

The handler returns:

```json
{
  "status": "success",
  "model": "htdemucs_ft",
  "stems": {
    "vocals": {"filename": "vocals.wav", "base64": "..."},
    "drums": {"filename": "drums.wav", "base64": "..."},
    "bass": {"filename": "bass.wav", "base64": "..."},
    "other": {"filename": "other.wav", "base64": "..."}
  }
}
```

### Testing the worker with curl

RunPod's sync endpoint always expects a JSON body, so even when you have a local WAV you must
base64-encode it and send it as `audio_base64`. One sample workflow:

```bash
AUDIO_B64=$(base64 -i ~/Downloads/song.wav | tr -d '\n')
PAYLOAD=$(jq -n --arg audio "$AUDIO_B64" '{input:{audio_base64:$audio,model_name:"htdemucs_ft"}}')
curl -X POST \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  --data "$PAYLOAD" \
  "https://api.runpod.ai/v2/$RUNPOD_ENDPOINT_ID/runsync"
```

You can reuse the same payload for local smoke tests by placing it into
`runpod-worker/test_input.json` before running `docker run --rm -e RUNPOD_TEST=1 demucs-worker`.
If you prefer to avoid base64 payloads, use the Cloudflare R2 transfer mode below.

## RunPod client (`client/`)

The root `pyproject.toml` exposes a `runpod-demucs` script that wraps the serverless endpoint and writes stems locally. Usage is consumption-first:

```bash
uv sync
uv run runpod-demucs \
  --input-file "~/Downloads/Screen Recording 2025-11-05 at 10.20.42 PM.wav" \
  --save-dir stems
```

Dynaconf loads `RUNPOD_API_KEY`, `RUNPOD_ENDPOINT_ID`, and `RUNPOD_ENDPOINT_URL` from your environment or `.env` files, so you can avoid repeating secrets. See the [Dynaconf env var docs](https://www.dynaconf.com/envvars/) for supported sources. If you prefer to pass credentials explicitly, add `--api-key` and `--endpoint-id` flags. Additional knobs like `--model-name`, `--shifts`, and `--overlap` remain available; `client/runpod_client.py` houses the implementation.
RUNPOD_API_KEY=rp_sk_... RUNPOD_ENDPOINT_ID=8cw1xzsn9rmbti \
  uv run runpod-demucs --input-file ~/Downloads/song.wav --save-dir stems
```

You can either set `RUNPOD_API_KEY`/`RUNPOD_ENDPOINT_ID` (or a `.env` file read by Dynaconf), or
pass the matching flags explicitly. Flags also let you override `--model-name` and tweak
`--shifts/--overlap`. `client/runpod_client.py` houses the implementation.

Configuration is powered by [Dynaconf](https://www.dynaconf.com/), so the CLI will read
`RUNPOD_API_KEY`, `RUNPOD_ENDPOINT_ID`, and `RUNPOD_ENDPOINT_URL` from your environment or `.env`
files automatically. The Typer-powered interface mirrors those knobs as flags if you prefer to pass
them explicitly.

### Cloudflare R2 transfer mode

The CLI can upload inputs to Cloudflare R2, send object keys to the worker, and then download
stems back locally. Provide client R2 credentials as flags or environment variables with the
`RUNPOD_` prefix (for example `RUNPOD_R2_ENDPOINT`). Credentials are never included in job payloads.

Configure these secrets on the **worker** before using R2 mode:

- `RUNPOD_R2_ENDPOINT`, `RUNPOD_R2_BUCKET`, `RUNPOD_R2_ACCESS_KEY_ID`, `RUNPOD_R2_SECRET_ACCESS_KEY`
- Optional `RUNPOD_R2_REGION` (default: `auto`)
- `RUNPOD_R2_PREFIX`: permitted base key prefix (default: `runpod-demucs`; must not be empty)

The client and worker must use the same endpoint and bucket, with credentials permitting the
required reads and writes. Set the client's `--r2-prefix` or `RUNPOD_R2_PREFIX` within the worker's
permitted prefix. Each client run appends a fresh UUID to its base prefix. The worker rejects
keys outside its permitted prefix and job-supplied endpoint, bucket, region, or credentials.
Deploy the updated client and worker together; older credential-bearing jobs are rejected.

```bash
RUNPOD_API_KEY=rp_sk_... RUNPOD_ENDPOINT_ID=8cw1xzsn9rmbti \
RUNPOD_R2_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com \
RUNPOD_R2_BUCKET=demucs-stems \
RUNPOD_R2_ACCESS_KEY_ID=... \
RUNPOD_R2_SECRET_ACCESS_KEY=... \
  uv run runpod-demucs --input-file ~/Downloads/song.wav --save-dir stems \
  --r2-download --r2-cleanup
```

The worker responds with `r2_output` metadata (bucket + prefix). The client uses its local
credentials to download the stems. `--r2-cleanup` deletes the uploaded input on a failed request
or worker response, and deletes the input plus downloaded stems after success. With
`--no-r2-download --r2-cleanup`, only the input is deleted; stem objects remain in R2.

## Next steps

- Adjust `runpod-worker/handler.py` if you need to push stems to other cloud storage instead of R2.
- Extend the `client` package with polling for async runs or integrations with DAWs.
- Keep `AGENTS.md` in sync with any new workflows so future contributors know which directory to touch.
