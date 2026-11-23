import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from client.analyze import _downbeat_index, analyze

SR = 44100


def _drums(beats_per_bar: int, *, accent: bool = True) -> np.ndarray:
    """Synthetic 120 BPM kicks, with the loudest kick on beat 1 of each bar."""
    y = np.zeros(9 * SR)
    t = np.arange(4410) / SR
    kick = np.sin(2 * np.pi * 65 * t) * np.exp(-t * 45)
    for i in range(16):
        start = int((0.4 + i * 0.5) * SR)
        gain = 0.9 if accent and i % beats_per_bar == 0 else 0.18
        y[start : start + kick.size] = gain * kick
    return y


def _f_sharp_minor() -> np.ndarray:
    """Sustained F# minor triad with tonic emphasis, in tune at A440."""
    t = np.arange(9 * SR) / SR
    y = np.zeros_like(t)
    for midi, gain in ((54, 0.17), (57, 0.13), (61, 0.12)):
        frequency = 440 * 2 ** ((midi - 69) / 12)
        y += gain * np.sin(2 * np.pi * frequency * t)
    return y


class AnalyzeTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)

    def test_stem_directory_estimates_tempo_key_and_downbeat(self) -> None:
        drums = _drums(4)
        sf.write(self.path / "drums.wav", np.column_stack([drums, drums]), SR)
        sf.write(self.path / "other.wav", _f_sharp_minor(), SR)
        result = analyze(self.path)
        self.assertAlmostEqual(result["tempo_bpm"], 120, delta=1.5)
        self.assertEqual(result["key"], "F# minor")
        # Tracking can trim an edge beat, but the first estimated beat 1 must
        # still fall on a real bar boundary (0.4s plus a multiple of 2s).
        offset = result["estimated_downbeat_seconds"] - 0.4
        self.assertAlmostEqual(offset, round(offset / 2) * 2, delta=0.08)
        self.assertIn(result["estimated_downbeat_seconds"], result["beat_times_seconds"])

    def test_mix_cli_respects_meter_and_known_tempo(self) -> None:
        mix = self.path / "mix.wav"
        sf.write(mix, 0.5 * (_drums(3) + _f_sharp_minor()), SR)
        result = subprocess.run(
            [
                str(Path(sys.executable).with_name("demucs-analyze")),
                str(mix),
                "--beats-per-bar",
                "3",
                "--bpm",
                "120",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        self.assertEqual(data["beats_per_bar"], 3)
        self.assertEqual(data["tempo_bpm"], 120)
        self.assertEqual(data["key"], "F# minor")
        offset = data["estimated_downbeat_seconds"] - 0.4
        self.assertAlmostEqual(offset, round(offset / 1.5) * 1.5, delta=0.08)

    def test_unaccented_clicks_have_no_downbeat_or_key(self) -> None:
        sf.write(self.path / "drums.wav", _drums(4, accent=False), SR)
        result = analyze(self.path)
        self.assertAlmostEqual(result["tempo_bpm"], 120, delta=1.5)
        self.assertIsNone(result["estimated_downbeat_seconds"])
        self.assertIsNone(result["key"])

    def test_sparse_accents_are_retained_in_twelve_beat_bars(self) -> None:
        beats = np.arange(1, 37) * 40
        onset = np.zeros(1500)
        onset[beats] = 1
        onset[beats[3::12]] = 4
        self.assertEqual(_downbeat_index(beats, onset, onset, 12), 3)

    def test_silence_is_unknown_even_with_known_tempo(self) -> None:
        path = self.path / "silence.wav"
        sf.write(path, np.zeros(SR), SR)
        result = analyze(path, bpm=120)
        self.assertEqual(result["beat_times_seconds"], [])
        for field in ("tempo_bpm", "key", "key_correlation", "estimated_downbeat_seconds"):
            self.assertIsNone(result[field])

    def test_rejects_mismatched_stems(self) -> None:
        sf.write(self.path / "drums.wav", np.zeros(SR), SR)
        sf.write(self.path / "bass.wav", np.zeros(SR // 2), SR)
        with self.assertRaisesRegex(ValueError, "length or sample rate"):
            analyze(self.path)

    def test_rejects_empty_stem_directory_and_corrupt_audio(self) -> None:
        with self.assertRaisesRegex(ValueError, "No standard Demucs WAV stems"):
            analyze(self.path)
        invalid = self.path / "bad.wav"
        invalid.write_text("not audio")
        with self.assertRaisesRegex(ValueError, "Cannot read audio"):
            analyze(invalid)

    def test_rejects_invalid_meter_and_nonfinite_tempo(self) -> None:
        with self.assertRaisesRegex(ValueError, "Beats per bar"):
            analyze(self.path, beats_per_bar=1)
        with self.assertRaisesRegex(ValueError, "BPM"):
            analyze(self.path, bpm=float("nan"))
