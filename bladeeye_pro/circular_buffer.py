from __future__ import annotations

from collections.abc import Iterable
import threading

import numpy as np


class IQCircularBuffer:
    """Thread-safe circular IQ buffer storing only the latest samples."""

    def __init__(self, capacity_samples: int) -> None:
        if capacity_samples <= 0:
            raise ValueError("capacity_samples must be > 0")
        self._capacity = int(capacity_samples)
        self._data = np.zeros(self._capacity, dtype=np.complex64)
        self._write_idx = 0
        self._size = 0
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def size(self) -> int:
        return self._size

    def extend(self, samples: Iterable[complex] | np.ndarray) -> None:
        arr = np.asarray(samples, dtype=np.complex64).ravel()
        if arr.size == 0:
            return
        with self._lock:
            if arr.size >= self._capacity:
                self._data[:] = arr[-self._capacity :]
                self._write_idx = 0
                self._size = self._capacity
                return

            end = self._write_idx + arr.size
            if end <= self._capacity:
                self._data[self._write_idx : end] = arr
            else:
                first = self._capacity - self._write_idx
                self._data[self._write_idx :] = arr[:first]
                self._data[: arr.size - first] = arr[first:]
            self._write_idx = end % self._capacity
            self._size = min(self._capacity, self._size + arr.size)

    def latest(self, count: int) -> np.ndarray:
        if count <= 0:
            return np.zeros(0, dtype=np.complex64)
        with self._lock:
            n = min(int(count), self._size)
            if n == 0:
                return np.zeros(0, dtype=np.complex64)
            start = (self._write_idx - n) % self._capacity
            if start < self._write_idx:
                out = self._data[start : self._write_idx]
            else:
                out = np.concatenate((self._data[start:], self._data[: self._write_idx]))
            return out.copy()

    def snapshot(self) -> np.ndarray:
        return self.latest(self._size)
