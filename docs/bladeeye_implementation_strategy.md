# BladeEye Evolution — Varianta Recomandată de Implementare

## TL;DR
**Cea mai bună variantă** este o arhitectură **modulară pe procese**, Linux-first, cu **backbone ZeroMQ**, orchestrată printr-un **supervisor local**, implementată incremental în 5 faze (Foundation → Intelligence → UX → Automation → Hardening).

Această variantă minimizează riscul tehnic, păstrează latența mică și permite scalare fără a bloca UI-ul.

---

## 1) De ce această variantă este optimă

1. **Separă clar responsabilitățile** (capture, analiză, API/UI, alertare).
2. **Izolează fail-urile**: dacă UI cade, capture+analiză continuă.
3. **Permite tuning independent** pentru performanță (CPU affinity, buffer sizing, rate limits).
4. **Simplifică packaging-ul standalone** (un singur bundle cu procese lansate controlat).
5. **Este compatibilă cu viziunea existentă** (handshake startup, sanity monitor, reguli IF/THEN).

---

## 2) Arhitectura recomandată (practic)

### Procese principale
- **`sdr-gateway`**
  - detectare hardware (VID/PID), init driver, setări live (`freq/rate/gain`), hopping.
- **`signal-engine`**
  - FFT + detector evenimente + extragere feature-uri + clasificare modulație/protocol.
- **`rule-engine`**
  - evaluare reguli IF/THEN, squelch inteligent, trigger record/alert.
- **`api-service`**
  - REST/WebSocket, expunere stare, configurare runtime.
- **`ui-app`**
  - waterfall, view patterns, sanity monitor, jurnal consolidat.

### Backbone de mesaje (ZeroMQ)
- `PUB/SUB` pentru telemetrie (spectrum frames, health metrics).
- `REQ/REP` pentru comenzi de control critice.
- `PUSH/PULL` pentru workload intern (batch-uri de feature extraction).

### Contract de mesaje (obligatoriu)
- folosiți schemă versionată (`message_version`), cu validare strictă.
- includeți timestamp monotonic + source-id în fiecare mesaj.

---

## 3) Modelul de date minim (v1)

### `detections`
- `id`, `ts_start`, `ts_end`, `center_freq_hz`, `bandwidth_hz`, `rssi_dbm`
- `modulation`, `baud_rate`, `protocol_guess`, `purpose_guess`, `confidence`

### `fingerprints`
- `id`, `name`, `feature_vector`, `created_at`, `last_seen_at`, `tags`

### `alerts`
- `id`, `rule_id`, `detection_id`, `trigger_ts`, `action_type`, `status`, `payload`

### `rules`
- `id`, `enabled`, `conditions_json`, `actions_json`, `cooldown_ms`, `priority`

---

## 4) Roadmap de implementare recomandat

## Faza 1 — Foundation (2–3 săptămâni)
- Device manager multi-SDR + BladeRF prioritar.
- Live controls (`sample_rate/gain/frequency`) fără restart stream.
- Startup handshake 3 pași + health endpoints.

**Exit criteria**
- schimbările live sunt stabile 30 minute;
- dropped packets în limite definite pe profil standard.

## Faza 2 — Intelligence v1 (2–4 săptămâni)
- Detector modulație + estimator baud + scor de încredere.
- Pipeline asincron I/Q → features → classification.

**Exit criteria**
- acuratețe minimă pe set intern de benchmark;
- latență de detecție sub pragul operațional stabilit.

## Faza 3 — UX operațional (2 săptămâni)
- Waterfall performant + patterns view.
- Sanity panel (buffer, dropped packets, error bus).

**Exit criteria**
- UI responsiv în sesiune continuă de minim 1h.

## Faza 4 — Alertare & Automatizare (2 săptămâni)
- Rule engine IF/THEN.
- Alert channels (log, webhook, fișier local) + recording trigger.

**Exit criteria**
- reguli deterministe și auditabile end-to-end.

## Faza 5 — Hardening Standalone (1–2 săptămâni)
- Bundle self-contained pentru Linux target.
- Smoke tests automate la pornire + recovery scripts.

**Exit criteria**
- instalare și rulare one-click pe distribuțiile țintă.

---

## 5) Decizii tehnice cheie (recomandate)

- **Orchestrare locală**: un launcher-supervisor care pornește procesele și monitorizează restarturi controlate.
- **Persistență**: SQLite pentru v1 (simplu, robust), migrabil ulterior.
- **Observabilitate**: metrici uniforme (`buffer_fill`, `dropped_packets`, `proc_latency_ms`, `rule_triggers`).
- **Safety la RF control**: validare strictă pe intervale hardware înainte de aplicarea comenzilor.
- **Config management**: profiluri YAML/JSON versionate (Europe/Global/custom).

---

## 6) Riscuri + mitigare

1. **USB instability / throughput bottlenecks**
   - mitigare: buffer tuning, pinned CPU cores, warning-uri proactive în UI.
2. **False positives la clasificare**
   - mitigare: confidence thresholds + feedback loop din etichetare manuală.
3. **Blocaje în UI la volum mare de date**
   - mitigare: downsampling pentru render, streaming binar, backpressure.
4. **Drift în hopping/analysis sync**
   - mitigare: clock source unificat + marker per hop în pipeline.

---

## 7) Ce implementăm prima dată (pragmatic)

Dacă trebuie ales un singur traseu „best first”:  
**Foundation + Handshake + Sanity Monitor**.

Motiv: fără stabilitatea acestui strat, funcțiile de AI/alertare vor produce rezultate inconsistente și greu de operat în teren.

---

## Concluzie

Varianta optimă este **modulară, proces-separată, orientată pe mesaje (ZeroMQ)** și livrată incremental. Astfel obțineți rapid valoare operațională, apoi adăugați inteligență și automatizare fără a sacrifica stabilitatea.
