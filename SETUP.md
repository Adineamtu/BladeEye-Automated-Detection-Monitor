# Setup Guide

This guide describes installation requirements and hardware verification for the current BladeEye desktop runtime.

## 1) System Requirements

- Python 3.10+
- `pip` and virtual environment support
- SDR dependencies for live capture workflows:
  - GNU Radio (3.8+)
  - `gr-osmosdr`
  - BladeRF runtime/driver packages (`bladerf`, `libbladerf`)

> You can still run parts of the application without SDR hardware (for UI and development workflows), but live RF capture requires a compatible SDR stack.

---

## 2) Python Environment

From repository root:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 3) SDR Packages (Linux Examples)

### Debian/Ubuntu

```bash
sudo apt update
sudo apt install gnuradio gr-osmosdr bladerf libbladerf2 libbladerf-dev python3-matplotlib
```

### Fedora

```bash
sudo dnf install gnuradio gr-osmosdr bladerf bladerf-devel python3-matplotlib
```

### Arch Linux

```bash
sudo pacman -Syu gnuradio gr-osmosdr bladerf libbladerf python-matplotlib
```

---

## 4) Hardware Verification

1. Connect the BladeRF via USB.
2. Probe the device:

```bash
bladeRF-cli --probe
```

If the device is not accessible:

- run with elevated privileges for a quick test, or
- install/configure appropriate udev permissions for permanent non-root access.

---

## 5) Launch BladeEye Desktop

```bash
python main.py
```

Optional explicit configuration:

```bash
python main.py --desktop-pro --center-freq 868000000 --sample-rate 5000000 --gain 32
```

---

## 6) Optional Build Optimizations

Build Cython helper for optional demodulation speedups:

```bash
python backend/setup.py build_ext --inplace
```

---

## 7) Post-Install Validation

Recommended checks:

```bash
pytest -q tests/test_bladeeye_pro_core.py tests/test_capture_lab.py
```

If frontend/API integration is part of your workflow:

```bash
cd frontend
npm ci
npm test
```
