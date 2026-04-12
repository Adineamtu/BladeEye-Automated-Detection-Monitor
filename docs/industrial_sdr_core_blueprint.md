# Industrial SDR Core Blueprint (C++ + Python Bridge)

Acest document fixează implementarea recomandată pentru operare stabilă la 20–40 MHz.

## 1) Zidurile — Backend C++ multi-thread

Executabilul `sdr_core` trebuie să ruleze separat de FastAPI și să izoleze trei fluxuri:

1. **Acquisition thread (producer)**
   - folosește interfața async a driverului SDR (ex: `bladerf_submit_stream` + callback);
   - nu face DSP; doar scrie blocuri I/Q într-un ring buffer SPSC pre-alocat;
   - rulează la prioritate ridicată.

2. **DSP worker thread**
   - citește blocurile din ring;
   - aplică fereastră Hann/Hamming;
   - execută FFT (`fftw3f`) + conversie magnitudine în dB;
   - aplică mediere pe mai multe cadre (5–10) pentru reducerea zgomotului alb;
   - rulează detecția de vârfuri peste prag.

3. **IPC thread**
   - publică snapshot-ul final în shared memory;
   - trimite doar metadatele de alertă (frecvență + putere) prin Unix Domain Socket.

## 2) Planșeul — Shared memory zero-copy

Map-ul de memorie partajată (POSIX) conține:

- `frame_id` incremental;
- `sample_rate`, `center_freq` pentru sincronizare;
- `spectrum_data[2048]` (float, magnitudine dB);
- `peak_count` + listă de peak events.

Sincronizarea producer/consumer se face cu un indicator atomic simplu:

- `0 = taken` (cititorul a consumat frame-ul),
- `1 = ready` (scriitorul a publicat un frame nou).

## 3) Acoperișul — logică de detecție în C++

Python nu procesează toate bin-urile FFT pentru detecție, ci primește deja semnale extrase:

- peak detection în C++;
- averaging per bin;
- alerte compacte via Unix socket (`JSON` minim sau payload binar).

## 4) Instalația electrică — control + auto-healing

- `sdr_core` are command listener (Unix socket separat) pentru `SET_GAIN`, `SET_FREQ`, `SET_RATE`, `SET_THRESHOLD` fără restart.
- la erori de backend USB/driver: închidere handle SDR, pauză 1s, reinițializare automată.

## 5) Contractul cu FastAPI

- FastAPI devine strict **control plane + WebSocket relay**;
- datele de spectru se citesc din `/dev/shm` și se trimit binar către browser;
- API-ul de control forward-ează comenzi către socket-ul `sdr_core`.

## Status în repository

În `cpp/sdr_core/` există un bootstrap compilabil care implementează:

- ring buffer SPSC;
- pipeline pe 3 thread-uri;
- FFT + smoothing + peak detection;
- publicare shared memory;
- command socket și alert socket.

Integrarea directă cu callback-urile async bladeRF este următorul pas incremental.
