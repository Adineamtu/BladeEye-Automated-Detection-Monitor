# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

block_cipher = None

# Detectăm calea proiectului
work_dir = os.getcwd()

a = Analysis(
    ['launcher.py'],
    pathex=[work_dir],
    datas=[
        ('../frontend/dist', 'frontend/dist'),
    ] + collect_data_files('PySide6'),
    hiddenimports=collect_submodules('PySide6'),
    binaries=[
        ('../cpp/sdr_core/build/sdr_core', '.'),
    ] + collect_dynamic_libs('PySide6'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='BladeEye',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['../assets/icon.ico'],
)
