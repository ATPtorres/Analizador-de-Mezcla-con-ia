import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy.io.wavfile import write

from mixai.dsp import AudioError, analyze, measure_loudness


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "Requires FFmpeg")
class DspTests(unittest.TestCase):
    rate = 48000

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "source.wav"

    def tone(self, seconds=4, frequency=1000, peak_db=-23):
        time = np.arange(round(self.rate * seconds)) / self.rate
        return (10 ** (peak_db / 20) * np.sin(2 * np.pi * frequency * time)).astype("float32")

    def test_ebu_stereo_tone_and_midband(self):
        x = self.tone(seconds=20)
        write(self.path, self.rate, np.column_stack((x, x)))
        result = analyze(self.path)
        self.assertAlmostEqual(result["loudness"]["integrated_lufs"], -23, delta=.1)
        self.assertAlmostEqual(result["loudness"]["true_peak_dbtp"], -23, delta=.15)
        self.assertAlmostEqual(result["loudness"]["short_term"][0]["lufs"], -23, delta=.1)
        self.assertAlmostEqual(result["loudness"]["lra_lu"], 0, delta=.1)
        self.assertAlmostEqual(result["crest_db"], 3.01, places=2)
        self.assertEqual(result["bands_pct"]["medio"], 100)
        self.assertEqual(result["stereo"]["correlation"], 1)
        self.assertEqual(result["stereo"]["width_pct"], 0)
        self.assertEqual(result["stereo"]["mono_delta_db"], 0)
        json.dumps(result, allow_nan=False)

    def test_antiphase_retains_spectral_energy(self):
        x = self.tone(frequency=80)
        write(self.path, self.rate, np.column_stack((x, -x)))
        result = analyze(self.path)
        self.assertEqual(result["stereo"]["correlation"], -1)
        self.assertEqual(result["stereo"]["width_pct"], 100)
        self.assertTrue(result["stereo"]["mono_cancellation"])
        self.assertIsNone(result["stereo"]["mono_delta_db"])
        self.assertGreater(result["bands_pct"]["grave"], 99)

    def test_silence_mono(self):
        write(self.path, self.rate, np.zeros(self.rate, dtype="float32"))
        result = analyze(self.path)
        self.assertIsNone(result["loudness"]["integrated_lufs"])
        self.assertIsNone(result["loudness"]["true_peak_dbtp"])
        self.assertIsNone(result["rms_dbfs"])
        self.assertIsNone(result["stereo"]["correlation"])
        self.assertEqual(result["loudness"]["short_term"], [])

    def test_quiet_signal_true_peak(self):
        write(self.path, self.rate, self.tone(peak_db=-80))
        self.assertAlmostEqual(analyze(self.path)["loudness"]["true_peak_dbtp"], -80, delta=.2)

    def test_final_partial_frame_peak(self):
        x = np.zeros(self.rate + 100, dtype="float32")
        x[-20] = .5
        pcm = self.path.with_suffix(".raw")
        x.tofile(pcm)
        result = measure_loudness(pcm, self.rate, 1, len(x) / self.rate, .5)
        self.assertAlmostEqual(result["true_peak_dbtp"], -6, delta=.25)

    def test_mp3_and_flac_decode(self):
        write(self.path, self.rate, self.tone())
        for suffix in ("mp3", "flac"):
            with self.subTest(suffix=suffix):
                encoded = self.path.with_suffix("." + suffix)
                subprocess.run(["ffmpeg", "-v", "error", "-i", str(self.path), str(encoded)], check=True)
                result = analyze(encoded)
                self.assertEqual(result["format"], suffix)
                self.assertAlmostEqual(result["duration_s"], 4, delta=.05)

    def test_invalid_audio_and_too_short(self):
        self.path.write_text("not audio")
        with self.assertRaises(AudioError):
            analyze(self.path)
        write(self.path, self.rate, self.tone(seconds=.2))
        with self.assertRaises(AudioError):
            analyze(self.path)


if __name__ == "__main__":
    unittest.main()
