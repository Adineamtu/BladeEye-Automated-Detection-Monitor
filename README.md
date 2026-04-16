# BladeEye Option D (Unified)

BladeEye rulează acum ca **o singură variantă operațională: Option D (desktop nativ PySide6)**.
Acest flux unifică UI-ul, achiziția SDR, clasificarea în timp real, logging-ul SIGINT, managementul sesiunii și exportul de rapoarte.

## Ce include varianta unificată

- Waterfall + spectrum renderer cu colormap, zoom (scroll) și pan (drag).
- Header de status cu:
  - starea SDR core,
  - latență flux,
  - dropped chunks,
  - scan status.
- Session management complet: load/save sesiune.
- Export center: HTML report + PDF export.
- RF controls live:
  - presets (433/868/915),
  - center frequency,
  - sample-rate slider,
  - gain slider,
  - alert threshold,
  - active frequency display,
  - hopping enable.
- Offline IQ analyzer (drag & drop + browse) cu file info (samples, modulație, SNR, baud).
- Watchlist live (add/remove frecvențe).
- Detected Signals table (coloane operaționale):
  - Center Frequency,
  - Modulation Type,
  - Baud Rate,
  - Detection / Likely Purpose,
  - Label / Protocol,
  - Signal Strength,
  - Duration (s),
  - Time,
  - Actions (Export I/Q per detection).
- Circular buffer 30s și instant record `.iq`.
- SQLite logger pentru istoricul detecțiilor (`sessions/bladeeye_pro_sigint.db`).

## Rulare

```bash
python main.py --desktop-pro --center-freq 868000000 --sample-rate 5000000 --gain 32
```

## Structură relevantă

- `bladeeye_pro/app.py` – UI desktop și orchestration runtime.
- `bladeeye_pro/dsp.py` – FFT, trigger, clasificare/decodare rapidă.
- `bladeeye_pro/smart_functions.py` – detector modulație + clasificator + hopping logic.
- `bladeeye_pro/circular_buffer.py` – buffer circular thread-safe.
- `bladeeye_pro/session.py` – save/load sesiuni.
- `bladeeye_pro/sigint_logger.py` – persistare detecții în SQLite.

## Testare

```bash
pytest -q tests/test_bladeeye_pro_core.py
```
