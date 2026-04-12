# Fast Path / Slow Path SDR Architecture

Acest document descrie arhitectura recomandată pentru stabilitate la lățimi de bandă mari (BladeRF), cu separare clară între procesarea critică și managementul aplicației.

## Obiectiv

- **Fast Path (C++20):** achiziție I/Q, buffer lock-free, FFT/filtrare SIMD, decimare.
- **Slow Path (Python/FastAPI):** control config, sesiuni UI, WebSocket către browser.
- **Frontend (React):** randare GPU (WebGL) + transport binar pentru spectru.

## Flux de date recomandat

1. BladeRF DMA scrie I/Q în ring-buffer lock-free (C++).
2. Worker DSP în C++ face:
   - FFT + complex-to-magnitude (SIMD).
   - decimare/filtrare pentru rata destinată UI.
3. Rezultatul este publicat în **shared memory** (zero-copy).
4. FastAPI citește doar snapshot-ul cel mai recent și trimite în WebSocket binar.
5. Frontend afișează waterfall-ul și ignoră cadrele pierdute când browserul e lent.

## Control runtime (bandwidth slider)

- Valori discrete suportate: **1, 2, 5, 10, 20 MHz**.
- API recomandat: `PUT /api/config/bandwidth?value=<Hz>`.
- La schimbare de rată:
  1. configurezi sample-rate hardware;
  2. ajustezi LPF hardware;
  3. golești bufferele vechi pentru a evita „fantomele” în waterfall.

## Ce este implementat acum în proiect

- Endpoint dedicat pentru bandwidth cu validare pe valori discrete.
- Streaming binar `Float32` prin WebSocket (`/ws/spectrum/binary`) cu fallback JSON.
- Slider de sample-rate discret în UI pentru control stabil.

Acest pas pregătește migrarea incrementală către engine-ul C++ fără a rupe UX-ul existent.

---

## Roadmap recomandat (după fundație)

### 1) Zidurile — Motor de achiziție C++ (fast path hardware)

**Ce livrăm:**
- proces separat (`acquisition_engine`) scris în C++ care face exclusiv:
  - inițializare BladeRF;
  - citire continuă I/Q;
  - push în ring buffer lock-free.

**De ce:**
- evităm pauzele necontrolate din runtime-ul Python (GC, scheduling);
- izolăm partea cea mai sensibilă la latență într-un proces determinist.

**Criterii de acceptanță:**
- zero `dropped_samples` în regim stabil (20 MHz) pe o fereastră de test definită;
- metrici exportate (`rx_overruns`, `buffer_fill_ratio`, `engine_uptime_s`).

### 2) Planșeul — Shared memory + ring buffer zero-copy

**Ce livrăm:**
- zonă de memorie partajată între C++ (writer) și Python (reader);
- protocol de snapshot cu header fix (versiune, timestamp, bins, scale, seq).

**De ce:**
- eliminăm copii intermediare de date;
- Python rămâne orchestration/control plane, nu data plane.

**Criterii de acceptanță:**
- un singur write al datelor de spectru pe frame;
- citire non-blocantă din FastAPI și fallback curat la ultimul frame valid.

### 3) Acoperișul — Detectoare DSP în C++

**Ce livrăm:**
- pipeline DSP C++ pentru detectoare (OOK/FSK, praguri adaptive, estimare baud);
- ieșire compactă a evenimentelor (detecții), separată de stream-ul brut.

**De ce:**
- reducere semnalelor fantomă;
- estimări de baud/deviation mai robuste în timp real.

**Criterii de acceptanță:**
- reducere fals-pozitive pe setul de test intern;
- latență predictibilă pentru emiterea alertelor.

### 4) Finisaje — WebGL + compresie binară finală

**Ce livrăm:**
- renderer WebGL pentru waterfall/spectrum în frontend;
- format binar final pentru transport (de ex. `Float32` sau `Int16` scalat + header minimal).

**De ce:**
- stabilitate la FPS constant fără încărcare mare pe CPU browser;
- randare fluidă la rată de update ridicată.

**Criterii de acceptanță:**
- UI fluent la 30 FPS target;
- degradare controlată: drop frame, nu blocare UI.

### 5) Opțional (dar recomandat) — Watchdog 24/7

**Ce livrăm:**
- watchdog care verifică progresul secvenței/timestamp-ului din shared memory;
- la blocaj: restart controlat `acquisition_engine` (+ recovery USB dacă platforma permite).

**De ce:**
- auto-healing la erori firmware/USB;
- operare autonomă pe termen lung.

**Criterii de acceptanță:**
- detectare timeout și recovery automat fără intervenție manuală;
- jurnal clar al cauzei și al acțiunilor de remediere.

## Observație de implementare

Strategia optimă este migrarea incrementală în pași mici, fiecare cu metrici de performanță și regresie: mai întâi mutăm **acquisition + shared memory**, apoi **DSP detections**, iar la final optimizăm **rendering-ul frontend**.
