# BladeEye Option D - Feature Matrix

Această versiune documentează **singura variantă suportată**: Option D (desktop unificat).

## 1) Vizualizare în timp real
- Waterfall painter (FFT -> colormap).
- Spectrum overlay.
- Zoom & pan pe axa de frecvență.
- Theme dark desktop (QSS).

## 2) Inteligență RF
- Clasificare modulație (OOK/ASK/FSK).
- Estimare baud rate.
- Protocol hinting (ex: `OOK-Remote`, `FSK-Telemetry`).
- Purpose inference pe baza semnăturilor locale.
- Hopping manager cu interval configurabil.

## 3) Date și memorie
- Circular buffer 30 secunde IQ.
- Instant record al ultimelor 30 secunde în fișier `.iq`.
- SQLite detection logging pentru istoric operațional.
- Export I/Q per detecție din tabel.

## 4) Control operațional
- Session save/load.
- Export report HTML.
- Export PDF.
- Start/Stop scan.
- Watchlist live (add/remove).

## 5) Offline analyzer
- Drag & drop `.iq` / `.complex`.
- Afișare metadate: samples, modulație, SNR, baud.
