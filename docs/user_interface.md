# BladeEye Option D - User Interface

## Header (Status)
- **SDR Core Health**: healthy/idle + latență + dropped chunks.
- **Scan Status**: Running / Stopped.
- **WebSocket state**: N/A pentru runtime desktop (fără browser bridge).

## Session & Reporting
- Session selector (dropdown).
- **Load Session** / **Save Session**.
- **Download Report** (HTML).
- **Export as PDF**.
- **Start** / **Stop** control global.

## RF Control Panel
- Presets: 433 / 868 / 915 MHz.
- Center frequency control.
- Sample rate slider.
- Gain slider.
- Alert threshold numeric.
- Active frequency display.
- Enable hopping checkbox.

## Offline IQ Analyzer
- Drag & Drop + Browse pentru fișiere `.iq` / `.complex`.
- File info: nume, samples, modulation, SNR, baud.

## Watchlist
- Add frequency.
- Remove selected.
- Active watchlist list view.

## Detected Signals Table
- Center Frequency
- Modulation Type
- Baud Rate
- Detection / Likely Purpose
- Label / Protocol
- Signal Strength
- Duration (s)
- Time
- Actions (Export I/Q)
