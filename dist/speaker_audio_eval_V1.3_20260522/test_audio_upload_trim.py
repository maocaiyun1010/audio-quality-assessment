# -*- coding: utf-8 -*-
"""上传前音频截断（audio_llm_normalize + dify_upload_max_audio_seconds）单元测试。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from audio_llm_normalize import TARGET_SAMPLE_RATE, write_normalized_wav_for_upload
from difyclient import dify_upload_max_audio_seconds


class TestAudioUploadTrim(unittest.TestCase):
    def test_trim_when_longer_than_cap(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "long.wav"
            sr = 44100
            n = int(sr * 90)
            sf.write(str(src), np.zeros(n, dtype=np.float32), sr)
            dst = Path(td) / "out.wav"
            meta = write_normalized_wav_for_upload(src, dst, max_duration_sec=60.0)
            self.assertTrue(meta["trimmed"])
            self.assertGreater(meta["duration_in_sec"], 60.0)
            self.assertAlmostEqual(meta["duration_out_sec"], 60.0, delta=0.15)
            data, out_sr = sf.read(str(dst))
            self.assertEqual(out_sr, TARGET_SAMPLE_RATE)
            self.assertAlmostEqual(len(data) / TARGET_SAMPLE_RATE, 60.0, delta=0.15)

    def test_no_trim_when_shorter_than_cap(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "short.wav"
            sr = 48000
            n = int(sr * 25)
            sf.write(str(src), np.zeros(n, dtype=np.float32), sr)
            dst = Path(td) / "out.wav"
            meta = write_normalized_wav_for_upload(src, dst, max_duration_sec=60.0)
            self.assertFalse(meta["trimmed"])
            self.assertAlmostEqual(meta["duration_in_sec"], 25.0, delta=0.05)
            self.assertAlmostEqual(meta["duration_out_sec"], 25.0, delta=0.05)

    def test_no_cap_uploads_full(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "long.wav"
            sr = 22050
            n = int(sr * 75)
            sf.write(str(src), np.zeros(n, dtype=np.float32), sr)
            dst = Path(td) / "out.wav"
            meta = write_normalized_wav_for_upload(src, dst, max_duration_sec=None)
            self.assertFalse(meta["trimmed"])
            self.assertAlmostEqual(meta["duration_out_sec"], 75.0, delta=0.2)

    def test_dify_upload_max_audio_seconds_env(self) -> None:
        old = os.environ.get("DIFY_UPLOAD_MAX_AUDIO_SECONDS")
        try:
            os.environ.pop("DIFY_UPLOAD_MAX_AUDIO_SECONDS", None)
            self.assertEqual(dify_upload_max_audio_seconds(), 60.0)
            os.environ["DIFY_UPLOAD_MAX_AUDIO_SECONDS"] = "0"
            self.assertIsNone(dify_upload_max_audio_seconds())
            os.environ["DIFY_UPLOAD_MAX_AUDIO_SECONDS"] = "45"
            self.assertEqual(dify_upload_max_audio_seconds(), 45.0)
        finally:
            if old is None:
                os.environ.pop("DIFY_UPLOAD_MAX_AUDIO_SECONDS", None)
            else:
                os.environ["DIFY_UPLOAD_MAX_AUDIO_SECONDS"] = old


if __name__ == "__main__":
    unittest.main()
