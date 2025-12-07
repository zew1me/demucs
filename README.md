# uv-demucs-runner

This repository is intentionally tiny—it only contains a `pyproject.toml` that declares a dependency on the official [Demucs](https://github.com/facebookresearch/demucs) CLI. Using [uv](https://github.com/astral-sh/uv) you can run Demucs locally without creating a traditional virtual environment.

## Prerequisites

1. Install `uv` (see the official install docs). No other tooling is required.
2. Place the source audio you want to separate somewhere reachable—e.g. `~/Downloads/export.wav`.

## Usage

Demucs already exposes a `demucs` entrypoint, so after syncing the environment with `uv` you can run the CLI as usual.

```bash
uv sync          # installs Demucs into .uv/
TORCHAUDIO_USE_SOUND_FILE=1 uv run demucs --name htdemucs ~/Downloads/export.wav
```

The `--name htdemucs` flag picks the 4-stem HT Demucs model. Replace `export.wav` with any other audio file path.

### Run without syncing first

Use `uvx` when you simply want to execute Demucs once without managing a local environment:

```bash
TORCHAUDIO_USE_SOUND_FILE=1 uvx demucs --name htdemucs ~/Downloads/export.wav
```

`uvx` downloads (and caches) Demucs on demand and executes the CLI directly.

## Notes

- No source code is included; this repo only exists so that `uv` can track the Demucs dependency in `pyproject.toml`.
- Set `TORCHAUDIO_USE_SOUND_FILE=1` (as above) so torchaudio writes audio with the pure-Python SoundFile backend—no system FFmpeg/TorchCodec binaries required.
- Feel free to edit `pyproject.toml` to pin a specific Demucs version or add additional CLI tools you rely on.
