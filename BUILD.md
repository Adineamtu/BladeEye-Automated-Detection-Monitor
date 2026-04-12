# Building the Cython Extension

The monitoring scripts can optionally use a small Cython helper for faster FSK
demodulation. This helper is defined in `HackRF/_fsk_cython.pyx` and is compiled
into a Python extension module.

## Prerequisites
- Python 3 with `pip`
- [`cython`](https://pypi.org/project/Cython/)
- [`numpy`](https://pypi.org/project/numpy/)

Install the packages with:

```bash
pip install cython numpy
```

## Building
Run the following command from the repository root to build the extension in place:

```bash
python HackRF/setup.py build_ext --inplace
```

The build step produces a compiled `_fsk_cython` module inside the `HackRF` directory. The `passive_monitor.py` script will automatically import this module if it exists. If the extension is missing or fails to compile, the script falls back to a pure Python implementation, so building the extension is optional.
