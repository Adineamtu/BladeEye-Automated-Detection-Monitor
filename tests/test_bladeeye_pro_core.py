import os
import sys

import numpy as np

sys.path.insert(0, os.getcwd())

from bladeeye_pro.circular_buffer import IQCircularBuffer
from bladeeye_pro.dsp import DSPEngine


def test_iq_circular_buffer_keeps_latest_samples():
    buf = IQCircularBuffer(capacity_samples=5)
    buf.extend(np.array([1, 2, 3], dtype=np.complex64))
    buf.extend(np.array([4, 5, 6, 7], dtype=np.complex64))

    out = buf.snapshot()
    assert out.tolist() == [3 + 0j, 4 + 0j, 5 + 0j, 6 + 0j, 7 + 0j]


def test_dsp_engine_produces_fft_and_detection_frame():
    dsp = DSPEngine(sample_rate=1_000_000, center_freq=868e6, fft_size=1024)
    t = np.arange(1024, dtype=np.float32) / 1_000_000
    iq = (2.2 * np.exp(1j * 2 * np.pi * 50_000 * t)).astype(np.complex64)

    frame = dsp.process(iq)

    assert frame.fft_db.shape == (1024,)
    assert frame.averaged_fft_db.shape == (1024,)
    assert frame.energy > 0
    assert frame.threshold > 0
