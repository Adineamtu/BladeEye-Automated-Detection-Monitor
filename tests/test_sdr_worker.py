import time
import os
import sys

import numpy as np

sys.path.insert(0, os.getcwd())

from bladeeye_pro.hardware import SDRWorker


def test_sdr_worker_drops_oldest_pending_chunks_when_backlogged():
    processed = []

    def process(chunk: np.ndarray) -> int:
        value = int(chunk[0].real)
        processed.append(value)
        return value

    worker = SDRWorker(process, max_pending_chunks=2, max_ready_frames=4)
    try:
        worker.submit_chunk(np.array([1], dtype=np.complex64))
        worker.submit_chunk(np.array([2], dtype=np.complex64))
        did_drop = worker.submit_chunk(np.array([3], dtype=np.complex64))

        assert did_drop is True
        assert worker.dropped_input_chunks == 1

        worker.start()
        deadline = time.time() + 1.0
        while len(processed) < 2 and time.time() < deadline:
            time.sleep(0.01)

        assert processed == [2, 3]
    finally:
        worker.stop()


def test_sdr_worker_returns_latest_frame_and_discards_stale_ready_frames():
    worker = SDRWorker(lambda chunk: int(chunk[0].real), max_pending_chunks=8, max_ready_frames=8)
    try:
        worker.start()
        for value in (10, 20, 30):
            worker.submit_chunk(np.array([value], dtype=np.complex64))

        deadline = time.time() + 1.0
        while worker.pending_chunks() > 0 and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)

        latest = worker.pop_latest_frame()
        assert latest == 30
        assert worker.dropped_ready_frames >= 2
    finally:
        worker.stop()
