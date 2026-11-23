"""Estimate a beat grid and musical key from a mix or local Demucs stems."""

import argparse
import json
import math
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

SAMPLE_RATE = 22050
HOP = 256
MIN_SAMPLES = SAMPLE_RATE // 2
PITCHED_STEMS = ("bass", "other", "vocals", "guitar", "piano")
NOTES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
# Krumhansl–Schmuckler profiles, in semitones above the tonic.
PROFILES = {
    "major": (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88),
    "minor": (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17),
}


def _read_audio(path: Path) -> tuple[np.ndarray, tuple[int, int]]:
    try:
        samples, rate = sf.read(path, dtype="float32", always_2d=True)
    except sf.SoundFileError as exc:
        raise ValueError(f"Cannot read audio: {path} ({exc})") from exc
    if not np.all(np.isfinite(samples)):
        raise ValueError(f"Non-finite samples in {path}")
    mono = samples.mean(axis=1)
    if mono.size and rate != SAMPLE_RATE:
        mono = librosa.resample(mono, orig_sr=rate, target_sr=SAMPLE_RATE)
    return mono, (len(samples), rate)


def _load_inputs(path: Path) -> tuple[np.ndarray, np.ndarray | None, bool]:
    if path.is_file():
        mix, _ = _read_audio(path)
        return mix, mix, True
    if not path.is_dir():
        raise ValueError(f"Input does not exist: {path}")

    drums: np.ndarray | None = None
    pitched: np.ndarray | None = None
    layout: tuple[int, int] | None = None
    for name in ("drums", *PITCHED_STEMS):
        stem = path / f"{name}.wav"
        if not stem.exists():
            continue
        audio, stem_layout = _read_audio(stem)
        if layout is not None and layout != stem_layout:
            raise ValueError(f"Stem length or sample rate does not match: {stem}")
        layout = stem_layout
        if name == "drums":
            drums = audio
        elif pitched is None:
            pitched = audio
        else:
            pitched += audio
    rhythm = drums if drums is not None else pitched
    if rhythm is None:
        raise ValueError(f"No standard Demucs WAV stems found in {path}")
    return rhythm, pitched, False


def _has_signal(y: np.ndarray) -> bool:
    return y.size >= MIN_SAMPLES and float(np.max(np.abs(y))) > 1e-6


def _estimate_key(y: np.ndarray, *, isolate_harmonics: bool) -> tuple[str | None, float | None]:
    if not _has_signal(y):
        return None, None
    spectrum = np.abs(librosa.stft(y, n_fft=4096, hop_length=1024))
    if isolate_harmonics:
        spectrum, _ = librosa.decompose.hpss(spectrum)
    chroma = librosa.feature.chroma_stft(
        S=spectrum**2, sr=SAMPLE_RATE, n_fft=4096, tuning=0.0, norm=None
    ).mean(axis=1)
    centered = chroma - chroma.mean()
    norm = float(np.linalg.norm(centered))
    if norm <= 1e-8 or norm < 0.1 * float(np.linalg.norm(chroma)):
        return None, None
    scores = []
    for mode, profile in PROFILES.items():
        template = np.asarray(profile) - np.mean(profile)
        template /= np.linalg.norm(template)
        correlations = [float(centered @ np.roll(template, i)) / norm for i in range(12)]
        scores.extend((score, f"{note} {mode}") for score, note in zip(correlations, NOTES))
    best, second = sorted(scores, reverse=True)[:2]
    if best[0] < 0.45 or best[0] - second[0] < 0.025:
        return None, None
    return best[1], round(best[0], 3)


def _downbeat_index(
    beats: np.ndarray, broadband: np.ndarray, low: np.ndarray, beats_per_bar: int
) -> int | None:
    """Return the phase within the tracked beats, or None for weak evidence."""
    complete_bars = len(beats) // beats_per_bar
    if complete_bars < 2:
        return None

    # Look near each tracked beat for the attack; kicks can be weaker than snares
    # in the broadband envelope, so give the low band more weight.
    radius = round(0.07 * SAMPLE_RATE / HOP)
    bars = np.zeros((complete_bars, beats_per_bar))
    for envelope, weight in ((broadband, 0.25), (low, 0.75)):
        peaks = np.array(
            [
                envelope[max(0, b - radius) : b + radius + 1].max(initial=0)
                for b in beats[: complete_bars * beats_per_bar]
            ]
        ).reshape(bars.shape)
        # Use the strongest typical phase as the scale. A global percentile
        # can clip a genuine once-per-bar accent in meters longer than 10.
        scale = float(np.max(np.median(peaks, axis=0)))
        if scale > 0:
            bars += weight * np.clip(peaks / scale, 0, 1)

    phase_scores = np.median(bars, axis=0)
    runner_up, best = np.argsort(phase_scores)[-2:]
    if phase_scores[best] - phase_scores[runner_up] < 0.10:
        return None
    leads = bars[:, best] - np.max(np.delete(bars, best, axis=1), axis=1)
    return int(best) if np.count_nonzero(leads >= 0.05) > complete_bars / 2 else None


def _estimate_rhythm(
    y: np.ndarray, beats_per_bar: int, bpm: float | None
) -> tuple[float | None, list[float], float | None]:
    if not _has_signal(y):
        return None, [], None
    mel = librosa.feature.melspectrogram(y=y, sr=SAMPLE_RATE, hop_length=HOP)
    db = librosa.power_to_db(mel, ref=np.max)
    broadband = librosa.onset.onset_strength(S=db, sr=SAMPLE_RATE, hop_length=HOP)
    low_bins = librosa.mel_frequencies(n_mels=130, fmax=SAMPLE_RATE / 2)[1:-1] < 180
    low = librosa.onset.onset_strength(S=db[low_bins], sr=SAMPLE_RATE, hop_length=HOP)
    _, beats = librosa.beat.beat_track(
        onset_envelope=broadband, sr=SAMPLE_RATE, hop_length=HOP, bpm=bpm
    )
    if len(beats) < 2:
        return None, [], None
    # Average over several beats to reduce the analysis frame's quantization error.
    # Take a median so isolated skipped beats or fills affect the estimate less.
    span = min(4, len(beats) - 1)
    frames_per_beat = float(np.median(beats[span:] - beats[:-span])) / span
    tempo_bpm = bpm if bpm is not None else 60 * SAMPLE_RATE / (HOP * frames_per_beat)
    times = [
        round(float(t), 6) for t in librosa.frames_to_time(beats, sr=SAMPLE_RATE, hop_length=HOP)
    ]
    phase = _downbeat_index(beats, broadband, low, beats_per_bar)
    return round(tempo_bpm, 3), times, times[phase] if phase is not None else None


def analyze(path: Path, *, beats_per_bar: int = 4, bpm: float | None = None) -> dict:
    """Analyze the file or one song's stem directory without writing any audio."""
    if not 2 <= beats_per_bar <= 12:
        raise ValueError("Beats per bar must be between 2 and 12")
    if bpm is not None and not (math.isfinite(bpm) and 30 <= bpm <= 300):
        raise ValueError("BPM must be between 30 and 300")
    rhythm, pitched, mix = _load_inputs(path)
    tempo, times, downbeat = _estimate_rhythm(rhythm, beats_per_bar, bpm)
    key, correlation = (
        (None, None) if pitched is None else _estimate_key(pitched, isolate_harmonics=mix)
    )
    return {
        "tempo_bpm": tempo,
        "key": key,
        "key_correlation": correlation,
        "beats_per_bar": beats_per_bar,
        "beat_times_seconds": times,
        "estimated_downbeat_seconds": downbeat,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input", type=Path, help="Audio file or directory containing one song's Demucs WAV stems"
    )
    parser.add_argument("--beats-per-bar", type=int, default=4, help="Fixed meter (2–12; default: 4)")
    parser.add_argument("--bpm", type=float, help="Track beats at this known tempo (30–300 BPM)")
    args = parser.parse_args()
    try:
        result = analyze(args.input.expanduser(), beats_per_bar=args.beats_per_bar, bpm=args.bpm)
    except ValueError as exc:
        parser.error(str(exc))
        return
    print(json.dumps(result, indent=2, allow_nan=False))
