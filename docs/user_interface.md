# BladeEye Pro User Interface Guide

This guide covers the current desktop UI and its two main tabs: **Monitor** and **Lab**.

## 1) Global Header

The header exposes runtime telemetry and health indicators:

- **SDR Core Health**: running/idle status.
- **Scan Status**: active or stopped acquisition state.
- **Dropped**: dropped chunk counter under load.
- **Errors**: latest error summary.
- **Latency**: recent processing latency indicator.

These indicators should be checked first before interpreting detections.

---

## 2) Session and Action Bar

Top actions available from the main window:

- **Session selector**: choose existing saved session.
- **Load Session**: restore detections/watchlist/session state.
- **Save Session**: persist current operation state.
- **Download Report**: export HTML report.
- **Export as PDF**: generate PDF report.
- **START / STOP PREVIEW** and **STOP PREVIEW**: monitor controls.
- **Open LAB**: switch/focus Lab workflow.
- **Error Log**: inspect runtime errors.

---

## 3) Monitor Tab

### RF Control Panel

- Preset profile selector (433 / 868 / 915 ranges).
- Center frequency input.
- Sample-rate input/slider.
- Gain control.
- Alert threshold control.
- Active frequency and hopping controls.

### Visualization Area

- Waterfall history view.
- Spectrum curve overlay.
- Mouse wheel zoom.
- Click-drag pan.

### Detection and Context Panels

- Live detections table with frequency, modulation, baud, label/protocol, strength, duration, and timestamp.
- Watchlist list management (add/remove).
- Offline IQ file drop zone (drag-and-drop + browse).

---

## 4) Lab Tab

The Lab tab is optimized for post-capture signal intelligence workflows:

- Raw IQ capture control with power indexing.
- Indexed event browsing.
- Extracted event-window inspection.
- Modulation/baud estimation summary.
- Signature/rolling-code oriented analysis flow.

Use Lab when you need to explain **why** a detection happened, not just confirm that it happened.

---

## 5) Recommended Operator Routine

1. Verify header health.
2. Configure RF controls.
3. Start monitor scan.
4. Track detections and maintain watchlist.
5. Save session snapshots during important windows.
6. Export report artifacts.
7. Move to Lab for deeper forensic analysis when needed.
