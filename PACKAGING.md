# Packaging (Standalone App)

This project can be shipped as a standalone desktop app (single-click), without manual terminal startup.

## Recommended project layout

- `cpp/sdr_core`: C++ acquisition engine (`sdr_core`)
- `frontend`: React UI source
- `app_wrapper`: native launcher + PyInstaller config

## What `app_wrapper` handles

- **Process manager**: starts `sdr_core` + FastAPI in the background
- **Window wrapper**: opens the UI in a native window with `PySide6 + Qt WebEngine`
- **Lifecycle controller**: sends `terminate()` to child processes on close
- **Resource discovery**: resolves runtime paths, including inside PyInstaller bundles
- **Dynamic port**: automatically chooses a free port when not explicitly set

## Local build

```bash
python app_wrapper/build_standalone.py
```

Build script flow:
1. `npm ci` + `npm run build` in `frontend/`
2. C++ build in `cpp/sdr_core/build`
3. PyInstaller build from `app_wrapper/reactive_jam.spec`
4. Archive output as `reactive_jam_standalone.tar.gz` (Linux/macOS) or `.zip` (Windows)

## Final bundle content

- Native launcher (`reactive_jam` / `reactive_jam.exe`)
- `frontend/dist`
- `sdr_core` binary (if present at build time)
- Python dependencies collected by PyInstaller

## Runtime system libraries (must be present on target machine)

Some native libraries are difficult or unsafe to fully statically bundle. Keep these available on the target OS.

### Required shared libraries

- `libbladeRF` (runtime package, e.g. `libbladerf2`)
- `libusb-1.0`
- `libfftw3f` (from FFTW3)

### Version policy for releases

For each published release, record and publish the **exact versions** detected on the build machine:

```bash
pkg-config --modversion libbladeRF
pkg-config --modversion libusb-1.0
pkg-config --modversion fftw3f
```

Add those exact values to release notes and installer docs so users can match ABI-compatible packages on their systems.

### Linux verification command

```bash
ldd ./reactive_jam | grep -E 'bladeRF|libusb|fftw'
```

If any dependency is missing, the standalone launcher may start but the SDR backend can fail at runtime.
