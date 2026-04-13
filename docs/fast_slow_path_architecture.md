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
- canalul de date în `/dev/shm` (ex: `/dev/shm/bladeeye_buffer`) ca "autostradă" comună pentru procese.
- sincronizare strictă writer/reader cu semafoare (sau mecanism echivalent de semnalizare) pentru a evita citiri concurente.
- buffer circular cu dimensiune fixă (ex. 128 MB) pentru consum de RAM predictibil.

**De ce:**
- eliminăm copii intermediare de date;
- Python rămâne orchestration/control plane, nu data plane.
- înlocuim transportul WebSocket pentru payload-ul mare cu acces direct în RAM, reducând latența de capăt.

**Criterii de acceptanță:**
- un singur write al datelor de spectru pe frame;
- citire non-blocantă din FastAPI și fallback curat la ultimul frame valid.
- fără `segfault`/corupție la stres concurent (writer și reader la 100% CPU);
- fără creștere nelimitată de memorie în regim continuu 24/7.

#### Contract de sincronizare recomandat (fără cod)

1. Writer-ul rezervă slotul următor din ring.
2. Writer-ul marchează slotul ca "în scriere" (semafor/stare intermediară).
3. Writer-ul scrie payload + metadata (timestamp, seq, lungime, versiune).
4. Writer-ul face commit atomic al stării slotului ("valid pentru citire").
5. Reader-ul consumă numai sloturi marcate "valid", niciodată sloturi în scriere.

Acest contract previne condițiile de cursă și citirile de adrese invalide, principalul risc real în zero-copy.

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

### 6) Igienă operațională obligatorie — Cleanup + logging

**Cleanup pentru "orfani" din shared memory:**
- launcher-ul execută la fiecare pornire o fază de "preflight cleanup";
- dacă găsește segmentul vechi (`/dev/shm/bladeeye_buffer`), validează dacă există procese active;
- în absența unui owner valid, șterge segmentul și recreează structurile curate.

**De ce:**
- previne blocaje după crash-uri;
- elimină stări "zombie" care pot da erori false la pornire.

**Log-to-file (diagnostic minim obligatoriu):**
- două fișiere separate în directorul aplicației:
  - `logs/engine_error.log` (motor C++ / SDR);
  - `logs/api_error.log` (API / orchestrare / WebSocket control plane).
- rotație simplă pe dimensiune/timp pentru a evita umplerea discului.
- fiecare eroare majoră include timestamp, componentă, cod de eroare, context (ex. port ocupat, access denied, timeout USB).

**Criterii de acceptanță:**
- orice incident critic are trasabilitate în fișier în < 1s de la apariție;
- startup-ul raportează explicit acțiunile de cleanup efectuate.

## Observație de implementare

Strategia optimă este migrarea incrementală în pași mici, fiecare cu metrici de performanță și regresie: mai întâi mutăm **acquisition + shared memory**, apoi **DSP detections**, iar la final optimizăm **rendering-ul frontend**.

În modelul final, WebSocket rămâne strict **control plane** (Start/Stop/Set Frequency/health), iar planul de date de volum mare rămâne în shared memory.
