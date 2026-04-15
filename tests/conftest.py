import types
import sys
import numpy as np
import pytest


@pytest.fixture(autouse=True)
def dummy_gnuradio_modules(monkeypatch):
    """Provide minimal gnuradio-like modules so imports succeed."""
    gnuradio = types.ModuleType("gnuradio")
    gr = types.ModuleType("gr")
    class DummyTopBlock:
        pass
    gr.top_block = DummyTopBlock
    class DummySyncBlock:
        pass
    gr.sync_block = DummySyncBlock
    gr.basic_block = DummySyncBlock
    gnuradio.gr = gr
    gnuradio.blocks = types.SimpleNamespace()
    fft_mod = types.SimpleNamespace(window=types.SimpleNamespace())
    gnuradio.fft = fft_mod
    monkeypatch.setitem(sys.modules, "gnuradio", gnuradio)
    monkeypatch.setitem(sys.modules, "gnuradio.gr", gr)
    monkeypatch.setitem(sys.modules, "gnuradio.blocks", gnuradio.blocks)
    monkeypatch.setitem(sys.modules, "gnuradio.fft", fft_mod)
    monkeypatch.setitem(sys.modules, "gnuradio.fft.window", fft_mod.window)

    # Provide placeholders for other optional modules
    monkeypatch.setitem(sys.modules, "pmt", types.ModuleType("pmt"))
    monkeypatch.setitem(sys.modules, "osmosdr", types.ModuleType("osmosdr"))

    # Ensure project root is on sys.path for ``import backend.passive_monitor``
    import os
    monkeypatch.syspath_prepend(os.getcwd())
