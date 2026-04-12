import numpy as np
cimport numpy as np

# cython: boundscheck=False
# cython: wraparound=False
# cython: cdivision=True

def integrate_and_dump(np.ndarray[np.float64_t, ndim=1] freq, int sps):
    cdef Py_ssize_t n_symbols = freq.shape[0] // sps
    cdef np.ndarray[np.float64_t, ndim=1] result = np.empty(n_symbols, dtype=np.float64)
    cdef Py_ssize_t i, j
    cdef double acc
    for j in range(n_symbols):
        acc = 0.0
        for i in range(sps):
            acc += freq[j * sps + i]
        result[j] = acc / sps
    return result
