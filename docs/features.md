# BladeEye Pro Feature Matrix

This document describes the current, supported BladeEye desktop runtime with two operational areas: **Monitor** and **Lab**.

## 1) Monitor (Live RF Operations)

### Visualization
- Real-time waterfall rendering.
- Live spectrum overlay.
- Frequency-axis zoom and pan for narrowband inspection.

### RF Intelligence
- Signal event detection from incoming IQ stream.
- Modulation inference (for example OOK/ASK/FSK depending on signal behavior).
- Baud-rate estimation.
- Protocol hint labeling and likely-purpose enrichment.
- Frequency hopping support through configurable hopping control.

### Operational Controls
- Start/stop scan controls.
- Presets for common ISM ranges (433/868/915 MHz).
- Manual center frequency, sample rate, gain, threshold controls.
- Live watchlist add/remove.

### Data & Persistence
- 30-second circular IQ buffer.
- Per-detection IQ snippet export.
- Session save/load.
- Detection logging for operational traceability.

### Reporting
- HTML report export.
- PDF report export.

---

## 2) Lab (Offline Capture Investigation)

### Capture
- Asynchronous raw IQ recording to reduce ingestion-path blocking.
- Power-threshold event indexing during capture.
- Pre-trigger context indexing for each event.

### Event Navigation
- Jump-to-event extraction from indexed capture files.
- Configurable pre/post event windows for focused review.

### Signal Analysis
- Optional low-pass cleanup in offline windows.
- Baud-rate and modulation estimation from extracted bursts.
- Signal power/RSSI and peak-power summaries.

### Signature & Pattern Analysis
- Signature matching against local signature definitions.
- Rolling-code behavior inspection across related bitstreams.

---

## 3) Reliability and Runtime Safety

- Worker threading and queueing to protect UI responsiveness.
- Dropped-chunk tracking for overload visibility.
- Runtime error tracking and diagnostics panel support.

---

## 4) Legacy/Compatibility Components

The repository also includes backend/frontend and packaging modules used for API integrations, compatibility testing, and distribution workflows. The primary operator runtime remains the BladeEye Pro desktop app.
