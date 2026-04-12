from setuptools import setup
from Cython.Build import cythonize
import numpy as np

setup(
    name='fsk_cython',
    ext_modules=cythonize('_fsk_cython.pyx', language_level=3),
    include_dirs=[np.get_include()],
)
