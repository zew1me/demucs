# Repository Guidelines

## Project Structure & Module Organization
The repo now houses three distinct workflows that all rely on the root-level `pyproject.toml` / `uv.lock`:
- `local-run/` – documentation plus a scratch space where stems land (`separations/`). No Python deps live here anymore.
- `runpod-worker/` – Docker context for the GPU worker (`Dockerfile`, `handler.py`, `runpod.yaml`, `requirements.txt`). Nothing else should bleed into this folder.
- `client/` – Python module (`client/__init__.py`, `runpod_client.py`) that exposes the `runpod-demucs` CLI via the root project scripts table.
Top-level docs (`README.md`, `AGENTS.md`) describe how the pieces interact; avoid adding executable code directly in the repo root outside of the shared `pyproject`. UV’s cache is redirected to `.uv/cache` via `uv.toml`, and the entire `.uv/` directory stays untracked.
Use the `uv` CLI (`uv add`, `uv remove`, `uv lock`, etc.) to modify dependencies and lockfiles whenever possible rather than editing `pyproject.toml` or `uv.toml` by hand.

## Build, Test, and Development Commands
- `uv sync`: run once from anywhere in the repo to hydrate both the Demucs CLI and the RunPod client scripts into `.uv/`.
- `cd local-run && TORCHAUDIO_USE_SOUND_FILE=1 uv run demucs --name htdemucs ~/Downloads/export.wav`: validates the workstation workflow; outputs land under `local-run/separations/`.
- `cd runpod-worker && docker build -t demucs-worker .`: ensure Docker contexts build cleanly; RunPod performs the same operation server-side.
- `docker run --rm -e RUNPOD_TEST=1 demucs-worker`: lightweight smoke test that the handler imports (the actual event loop only triggers inside RunPod, but this surfaces import/runtime errors early).
- `uv sync && RUNPOD_API_KEY=... RUNPOD_ENDPOINT_ID=... uv run runpod-demucs --input-file ~/Downloads/song.wav`: confirms the CLI can talk to a deployed endpoint and decode stems locally.
- `uv run pre-commit install && uv run pre-commit run --all-files`: hydrate git hooks + mirror the CI lint/type-check step (trailing whitespace, EOF fixes, Ruff, Pyrefly). Required before opening a PR; GitHub gate named **pre-commit** must pass before merging to `master`.

### Pre-commit workflow expectations

- Hooks live at the repo root in `.pre-commit-config.yaml` and run Ruff plus Pyrefly (type checking the entire repo). GitHub Actions runs `uv run pre-commit run --all-files` on every push/PR and is required for merges.
- When authoring code, install hooks once via `uv run pre-commit install` so `git commit` enforces them locally; use `uv run pre-commit run --all-files` prior to opening a PR to catch failures earlier. For quick spot-checks, `uv run pre-commit run --files path/to/file.py` targets only the files you just edited.
- If Pyrefly blocks your commit, address the typing issue (do not skip the hook). When editing worker-only files that import `runpod`, guard imports as in `runpod-worker/handler.py` so Pyrefly can type-check without that dependency installed locally.
- Workflow-specific notes:
  - `client/`: expect Pyrefly to enforce Typer/Click signatures—prefer raising `typer.BadParameter` with `param_hint` instead of unsupported `param_name`.
  - `runpod-worker/`: the hook runs without GPU deps; keep imports optional and prefer pure-stdlib logic in the handler core to keep linting green.

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
- Use Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, etc.). The `auto:` prefix is acceptable when paired with a valid type (e.g., `auto: chore: …`), but vanilla “auto:” only should be avoided going forward.
- Keep commits scoped to one directory whenever possible. If a change crosses boundaries, document each affected workflow in the commit body.
- Always sync the root `uv.lock` after changing dependencies so both the Demucs CLI and client stay reproducible.
- PR descriptions should list: the Demucs command (local), Docker build tag (worker), or CLI invocation (client) you tested, plus the resulting output location or URL.
- Continue using short auto-style commit subjects. Link issues when relevant and include screenshots/logs only if they add clarity (e.g., RunPod deployment screenshot, CLI output).
