# BladeEye Automated Detection Monitor

BladeEye is a **desktop-first RF detection and analysis platform** focused on real-time monitoring and post-capture investigation.

The current product is the **BladeEye Pro desktop runtime** (PySide6), which includes two operational workspaces:

- **Monitor**: live RF spectrum/waterfall, scan control, watchlist tracking, detections, session management, and reporting.
- **Lab**: indexed raw IQ capture analysis, burst extraction, modulation/baud estimation, signature matching, and rolling-code inspection.

---

## Current Architecture (April 2026)

The repository contains multiple components from earlier phases. The actively maintained user-facing runtime is:

- `main.py` → starts the native BladeEye desktop app.
- `bladeeye_pro/` → production desktop runtime and signal-processing pipeline.

Additional folders (`api.py`, `backend/`, `frontend/`, `app_wrapper/`, `cpp/`) are still useful for compatibility, packaging, experiments, and tests, but they are not the primary day-to-day launch path for operators.

---

## Core Capabilities

### 1. Monitor Workspace (Live Operations)

- Real-time waterfall + spectrum visualization.
- Zoom and pan controls for frequency inspection.
- SDR runtime status indicators (scan state, latency, dropped chunks, errors).
- RF control panel (presets, center frequency, sample rate, gain, alert threshold, hopping).
- Live detections table (frequency, modulation, baud, protocol hint, power, duration, timestamp).
- Watchlist management.
- Session save/load.
- Report export (HTML + PDF).
- 30-second circular IQ buffering and per-detection IQ export.

### 2. Lab Workspace (Post-Capture Analysis)

- High-speed asynchronous raw IQ recording.
- Power-trigger indexing with pre-trigger metadata.
- Event-based extraction of IQ windows.
- Offline low-pass cleanup for focused analysis.
- Baud-rate and modulation estimation.
- Signature matching against local signature database.
- Rolling-code behavior inspection for related captures.

---

## Installation

## Prerequisites

- Python **3.10+** (3.11 recommended).
- SDR stack (for hardware capture): GNU Radio + `gr-osmosdr` + BladeRF runtime/driver packages.
- Linux/macOS/Windows (Linux has the most documented SDR package flow).

## 1) Clone and create environment

```bash
git clone <your-repo-url> BladeEye-Automated-Detection-Monitor
cd BladeEye-Automated-Detection-Monitor
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
```

## 2) Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## 3) Optional performance build (Cython helper)

```bash
python backend/setup.py build_ext --inplace
```

---

## Usage

## Start BladeEye Pro (desktop)

```bash
python main.py --desktop-pro --center-freq 868000000 --sample-rate 5000000 --gain 32
```

### Runtime arguments

- `--desktop-pro` (enabled by default)
- `--center-freq` (Hz)
- `--sample-rate` (samples/second)
- `--gain` (dB)

### Typical safe startup

```bash
python main.py
```

This uses safe defaults from `main.py`:

- center frequency: `433920000`
- sample rate: `1000000`
- gain: `20`

---

## Monitor Workflow (Quick Start)

1. Launch the app.
2. Select a preset or manually set center frequency/sample rate/gain.
3. Start scanning.
4. Observe detections and spectrum behavior.
5. Add frequencies of interest to watchlist.
6. Save session and export report when needed.

## Lab Workflow (Quick Start)

1. Open **LAB** from the top action bar.
2. Record a raw IQ session with power-indexing enabled.
3. Stop capture and load indexed events.
4. Jump through events and inspect extracted windows.
5. Run bitrate/modulation/signature analysis.
6. Use results to refine watchlist/rules in Monitor.

---

## Testing

Run the Python test suite:

```bash
pytest -q
```

Run a focused subset:

```bash
pytest -q tests/test_bladeeye_pro_core.py tests/test_capture_lab.py
```

Frontend tests (if working on React side):

```bash
cd frontend
npm ci
npm test
```

---

## Documentation Map

- `SETUP.md` – system dependencies and SDR verification.
- `API.md` – REST/WebSocket API contract for backend mode.
- `docs/features.md` – functional feature matrix (Monitor + Lab).
- `docs/user_interface.md` – UI layout and operator guidance.
- `docs/protocols.md` – protocol/signature definitions and extension flow.
- `PACKAGING.md` – standalone packaging notes.

---

## Language Policy

Project-facing materials are maintained in **English**:

- code comments/docstrings,
- user documentation,
- README and docs.

This helps keep operator, developer, and integrator workflows consistent across teams.
