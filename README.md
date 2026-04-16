# 📡 BladeEye: Advanced ELINT & Signal Intelligence Suite

![Build: Passing](https://img.shields.io/badge/Build-Passing-brightgreen)
![Application Window Screenshot](docs/images/app-window.svg)

BladeEye is a professional RF monitoring and ELINT-oriented analysis platform for real-time spectrum surveillance, protocol triage, and post-capture investigation.
Unlike basic SDR viewers, BladeEye combines a Python intelligence engine, FastAPI services, a React dashboard, and a high-throughput C++ SDR core bridge to detect, classify, correlate, and persist signal activity.

---

## 🚀 Core Capabilities

### 🧠 Intelligence Engine

- **Automatic Modulation Classification (AMC)** for digital/analog hints (ASK, FSK, PSK, MSK, AM, FM where detectable).
- **Baud-rate estimation** for candidate digital transmissions.
- **SNR and noise-gating logic** to reduce false positives.
- **Cyclostationary and heuristic signal analysis** paths for hard-to-detect emitters.
- **Protocol identification and custom signature matching** via built-in + user-defined protocol databases.

### 🕵️ SIGINT & Surveillance Workflow

- **Live SIGINT event log (SQLite)** with frequency, RSSI/power-derived data, decode metadata, and timestamps.
- **Watchlist + target alerts** for frequencies/protocols/signatures of interest.
- **FHSS / hopping correlation** with session-level grouping, dwell-time, and hop-range analytics.
- **Session persistence + autosave/recovery** for operational continuity.

### 🏗️ Industrial Runtime Architecture

- **ZeroMQ data bridge** between C++ acquisition path and Python backend for low-latency ingest.
- **Preflight checks** for SDR availability, USB access, and runtime mode selection (hardware vs demo fallback).
- **Standalone packaging path** (PyInstaller + embedded UI) for one-click desktop deployments.
- **Execution Board + telemetry** for runtime diagnostics, throughput health, and troubleshooting snapshots.

---

## 🧾 Integrated Devices & Protocols

BladeEye now ships with a built-in RF signature catalog and protocol list.

- Full device+protocol inventory: `docs/integrated_devices_and_protocols.md`
- Runtime APIs:
  - `GET /api/signatures` → list all built-in + user-captured signatures
  - `POST /api/signatures/capture` → **Capture to Signature** (save unknown pulse profile as a new signature)

### Capture to Signature (Auto-Învățare)

When an unknown signal is detected, BladeEye displays:

- `Puls detectat: <short>/<long> | Unknown Signal (...)`

In the Detected Signals table you can click **Save as Signature**, enter a custom device name (for example `Barieră Garaj Vecin`), and BladeEye stores the new signature automatically in `sessions/signatures_user.json`.

## 🧱 Repository Architecture

- `main.py` — application entrypoint (injects monitor, starts API server).
- `api.py` — FastAPI backend, websocket streams, control/config/session endpoints, reporting.
- `backend/` — intelligence logic (decoder, identifiers, patterns, SIGINT storage, ZMQ consumer, preflight checks).
- `cpp/sdr_core/` — high-performance C++ SDR bridge/runtime components.
- `frontend/` — React + Vite operational interface (waterfall, logs, watchlist, intelligence panels).
- `backend/passive_monitor.py` — SDR passive monitoring core (+ optional Cython acceleration).
- `app_wrapper/` — standalone desktop launcher + packaging scripts.
- `tests/` — API, intelligence, hopping/session, preflight, telemetry, and UI-adjacent backend test coverage.

---

## 🛠️ Installation

## System Requirements

- **OS:** Linux recommended (Ubuntu 22.04+, Kali, DragonOS).
- **Python:** 3.9+
- **Node.js:** required for frontend build/dev.
- **Native tooling:** `cmake`, C/C++ build tools.
- **SDR hardware:** BladeRF (primary) or compatible SDR paths used by your runtime setup.

> Note: Some advanced SDR paths require GNU Radio + osmosdr + vendor drivers.

### 1) Clone repository

```bash
git clone https://github.com/<your-org-or-user>/BladeEye-Automated-Detection-Monitor.git
cd BladeEye-Automated-Detection-Monitor
```

### 2) Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3) Build C++ SDR core (ZeroMQ bridge/runtime)

```bash
cd cpp/sdr_core
mkdir -p build && cd build
cmake ..
make -j"$(nproc)"
cd ../../..
```

### 4) Install frontend dependencies

```bash
cd frontend
npm install
cd ..
```

### 5) Optional: build Cython accelerator

```bash
python backend/setup.py build_ext --inplace
```

---

## ▶️ Running BladeEye

### Option A — Standard backend startup

```bash
python main.py
```

Default API/UI bind assumptions use `127.0.0.1:8000` unless overridden with flags.

### Option B — Startup script

```bash
./start_system.sh
```

### Option C — Dev split (frontend + API)

1. Start backend:

```bash
python main.py --host 127.0.0.1 --port 8000
```

2. Build or run frontend (depending on your workflow):

```bash
cd frontend
npm run build
# or: npm run dev
```

### Option D — BladeEye Pro native desktop runtime

Launch the new single-window desktop pipeline (hardware/acquisition + DSP + smart detection + circular buffer) directly from Python:

```bash
python main.py --desktop-pro --center-freq 868000000 --sample-rate 5000000 --gain 32
```

Notes:
- Uses PySide6 for a native UI (no browser stack).
- If `libbladeRF` binding is unavailable, it falls back to a high-fidelity simulated IQ source (`BLADEEYE_PRO_SIM=1`).
- Click **Record last 30s** to dump the circular buffer into `sessions/pro_capture_<ts>.npy`.


---

## 🖥️ Operational Usage

1. **Boot + Preflight**
   On startup, BladeEye performs runtime checks (hardware/USB/runtime mode). If SDR hardware is unavailable, the system can operate in demo/simulated paths.

2. **Active Monitoring**
   - Use the **waterfall/spectrum** views for real-time RF activity.
   - Track detections in **live intelligence + SIGINT logs**.
   - Configure **watch targets** for immediate alerting.

3. **FHSS/Hopping Analysis**
   - Multi-frequency detections from the same emitter are grouped under session logic.
   - Inspect hop behavior, dwell timing, and frequency spread from the session/intelligence panels.

---

## 📊 Example Use Cases

| Objective | Action | Expected Result |
|---|---|---|
| ISM security audit | Watch-target 433.92 MHz / protocol signatures | Logs and classifies remote/sensor activity with decode metadata |
| FHSS analysis | Monitor 868 MHz band over time | Identifies industrial/IoT hopping behavior and correlates sessions |
| Offline research | Replay `.iq` workflows (demo-compatible paths) | Post-event modulation + baud/protocol investigation without live hardware |

---

## 🔧 Diagnostics & Maintenance

BladeEye includes telemetry and runtime diagnostics features exposed through API/dashboard paths:

- **ZMQ throughput/latency health indicators**
- **Runtime error buffer + rotating logs**
- **Session autosave/recovery**
- **SQLite-backed SIGINT persistence with WAL-oriented behavior for write-heavy workloads**

For environment and hardware package details, see:

- `SETUP.md`
- `BUILD.md`
- `PACKAGING.md`
- `API.md`

---

## 🧪 Testing

Run the backend/API regression suite:

```bash
pytest -q
```

Frontend tests (from `frontend/`):

```bash
npm test
```

---

## 📜 License & Responsible Use

This project is intended for educational, defensive research, and authorized RF analysis. You are responsible for complying with local laws and spectrum-monitoring regulations.

See `LICENSE` for legal terms.

---

## 🤝 Contribution

Issues and pull requests are welcome. Prefer focused PRs with reproducible steps and (when relevant) test updates.

---

**BladeEye — From raw RF samples to actionable intelligence.**
