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

## RunPod client (`client/`)

The root `pyproject.toml` exposes a `runpod-demucs` script that wraps the serverless endpoint and writes stems locally.

```bash
uv sync
RUNPOD_API_KEY=rp_sk_... RUNPOD_ENDPOINT_ID=8cw1xzsn9rmbti \
  uv run runpod-demucs --input-url https://example.com/song.wav --save-dir stems
```

Flags let you provide a local `--input-file` (base64 uploads), override `--model-name`, and tweak `--shifts/--overlap`. `client/runpod_client.py` houses the implementation.

## Next steps

- Adjust `runpod-worker/handler.py` if you need to push stems to cloud storage instead of returning base64.
- Extend the `client` package with polling for async runs or integrations with DAWs.
- Keep `AGENTS.md` in sync with any new workflows so future contributors know which directory to touch.
