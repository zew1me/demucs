# Repository Guidelines

## Project Structure & Module Organization
The repository is intentionally lean. `pyproject.toml` and `uv.lock` pin Demucs and its runtime stack for `uv`. `README.md` documents the workflow, and this `AGENTS.md` adds contributor guidance. Generated metadata (e.g., `uv_demucs_runner.egg-info/`) should be ignored in commits. Audio separations automatically land in `separations/<timestamp>/htdemucs/<track>/`—never edit stems in place; copy them elsewhere if you need to modify outputs.

## Build, Test, and Development Commands
- `uv sync`: resolve and install dependencies into `.uv/`; run before modifying `pyproject.toml`.
- `TORCHAUDIO_USE_SOUND_FILE=1 uv run demucs --name htdemucs ~/Downloads/export.wav`: primary invocation during development; replace the path or add Demucs flags as needed.
- `TORCHAUDIO_USE_SOUND_FILE=1 uvx demucs --name htdemucs <file>`: one-off execution without touching the local environment.
- `uv lock --locked`: regenerate the lockfile if dependencies change; commit both `pyproject.toml` and `uv.lock`.

## Coding Style & Naming Conventions
No Python packages live in this repo today. If you add scripts, keep them under `src/` with module-friendly names (snake_case files, PascalCase classes, snake_case functions). Default to 4-space indentation, black-compatible formatting, and type hints for new code. CLI wrappers should expose descriptive flags rather than hard-coded paths.

## Testing Guidelines
Demucs itself has upstream coverage, so local validation currently means running the CLI against a known short clip and confirming four stems appear under `separations/`. If you add glue scripts, accompany them with pytest units in `tests/` (mirror module paths, name files `test_<module>.py`). Prefer deterministic fixtures or 5–10 second WAVs to keep runs fast.

## Commit & Pull Request Guidelines
History uses short, descriptive auto-generated messages; continue that pattern (e.g., “auto: document demucs workflow”). Scope each commit to a single concern, ensure `uv.lock` stays in sync, and include any manual run logs in the PR description. Pull requests should describe the scenario exercised (`uv run demucs ...` command, source WAV location, output folder) and note whether `TORCHAUDIO_USE_SOUND_FILE` or other env vars were required. Link issues when relevant and add screenshots only if you introduce user-facing docs.
