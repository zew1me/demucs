# Repository Guidelines

## Project Structure & Module Organization
The repo now houses three distinct workflows that all rely on the root-level `pyproject.toml` / `uv.lock`:
- `local-run/` – documentation plus a scratch space where stems land (`separations/`). No Python deps live here anymore.
- `runpod-worker/` – Docker context for the GPU worker (`Dockerfile`, `handler.py`, `runpod.yaml`, `requirements.txt`). Nothing else should bleed into this folder.
- `client/` – Python module (`client/__init__.py`, `runpod_client.py`) that exposes the `runpod-demucs` CLI via the root project scripts table.
Top-level docs (`README.md`, `AGENTS.md`) describe how the pieces interact; avoid adding executable code directly in the repo root outside of the shared `pyproject`. UV’s cache is redirected to `.uv/cache` via `uv.toml`, and the entire `.uv/` directory stays untracked.

## Build, Test, and Development Commands
- `uv sync`: run once from anywhere in the repo to hydrate both the Demucs CLI and the RunPod client scripts into `.uv/`.
- `cd local-run && TORCHAUDIO_USE_SOUND_FILE=1 uv run demucs --name htdemucs ~/Downloads/export.wav`: validates the workstation workflow; outputs land under `local-run/separations/`.
- `cd runpod-worker && docker build -t demucs-worker .`: ensure Docker contexts build cleanly; RunPod performs the same operation server-side.
- `docker run --rm -e RUNPOD_TEST=1 demucs-worker`: lightweight smoke test that the handler imports (the actual event loop only triggers inside RunPod, but this surfaces import/runtime errors early).
- `uv sync && RUNPOD_API_KEY=... RUNPOD_ENDPOINT_ID=... uv run runpod-demucs --input-url <http>`: confirms the CLI can talk to a deployed endpoint and decode stems locally.

## Coding Style & Naming Conventions
- Prefer 4-space indentation and black-compatible formatting for Python (both the worker and client). Keep modules snake_case and functions/methods snake_case; classes stay PascalCase.
- Keep workflow-specific assets scoped to their directories (e.g., Docker helper scripts in `runpod-worker/`, CLI utilities live under `client/`). Avoid shared state via `../`.
- For doc updates, keep Markdown succinct and scoped—per-folder READMEs should only describe that folder's jobs.
- Environment variables (`RUNPOD_API_KEY`, `RUNPOD_ENDPOINT_ID`, `TORCHAUDIO_USE_SOUND_FILE`) should remain configurable via CLI flags; never bake secrets into code.

## Testing Guidelines
- `local-run/`: use a short WAV inside `~/Downloads/` and confirm four stems populate `local-run/separations/<timestamp>/htdemucs/...`.
- `runpod-worker/`: for now manual smoke tests are sufficient—`docker run` should finish without stack traces. If you add complex logic, create fixtures under `runpod-worker/tests/` and run `pytest` before cutting a release.
- `client/`: add unit tests under `client/tests/` as the CLI grows; follow `test_<module>.py` naming. Until tests exist, exercise the CLI against a staging endpoint and capture the exact command in PR descriptions.
- Keep fixtures under 10 seconds of audio to keep CI quick if automated tests are added later.

## Commit & Pull Request Guidelines
- Keep commits scoped to one directory whenever possible (e.g., “auto: add runpod client CLI”). If a change crosses boundaries, document each affected workflow in the commit body.
- Always sync the root `uv.lock` after changing dependencies so both the Demucs CLI and client stay reproducible.
- PR descriptions should list: the Demucs command (local), Docker build tag (worker), or CLI invocation (client) you tested, plus the resulting output location or URL.
- Continue using short auto-style commit subjects. Link issues when relevant and include screenshots/logs only if they add clarity (e.g., RunPod deployment screenshot, CLI output).
