# BladeEye Evolution — Plan Structural

Acest document formalizează viziunea BladeEye Evolution într-un plan tehnic implementabil, pe piloni, cu priorități și criterii de acceptare.

## 1) Pilonul Hardware & Control (Fundația)

### Obiectiv
Să asigure conectare robustă cu SDR-ul, control în timp real al parametrilor RF și compatibilitate multi-device.

### Capacități
- **Conexiune hardware agnostică**
  - Detectare universală SDR pe bază de VID/PID.
  - Prioritizare BladeRF implicit, cu fallback către alte SDR-uri detectate.
  - Selector explicit de device în UI.
- **Control în timp real (fără restart stream)**
  - `sample_rate` dinamic.
  - control de gain pe etaje (`LNA`, `VGA`, unde hardware-ul permite).
  - `center_frequency` cu reacordare instant.
- **Presets regionale**
  - profil „Europe” și „Global”, extensibile prin config.
- **Hopping engine**
  - listă de frecvențe + dwell time + politică de salt (sequential/random).
  - sincronizare între achiziție și analiză pentru evitare de frame mismatch.

### Criterii de acceptare
- Device detectat în `< 2s` de la startup.
- Schimbare `sample_rate/gain/frequency` aplicată live, fără cădere API/UI.
- Hopping stabil pe sesiuni de minim `30 min` fără drift semnificativ.

---

## 2) Pilonul de Analiză Inteligentă (Creierul)

### Obiectiv
Transformarea fluxului I/Q brut în informații acționabile (tip semnal, scop probabil, protocol).

### Capacități
- **Identificare automată**
  - detectare tip modulație: `AM/FM/ASK/FSK/PSK` (+ extensibil).
  - deducere automată baud/symbol rate.
  - clasificator „likely purpose” (telecomandă auto, meteo, pager, satelit etc.).
- **Interpretare și etichetare**
  - identificare protocol prin matching cu bibliotecă de protocoale.
  - etichetare semnale + jurnalizare în baza istorică.
  - măsurători pe eveniment: RSSI (dBm), durată (ms), bandwidth estimat.

### Criterii de acceptare
- Pipeline asincron stabil sub încărcare continuă.
- Rate de identificare reproducibile pe seturi de test controlate.
- Persistență corectă a metadatelor în sesiuni multiple.

---

## 3) Pilonul de Vizualizare și Interfață (Oglinda)

### Obiectiv
UI fluent care oferă situația RF „dintr-o privire” + diagnostic intern.

### Capacități
- **Waterfall engine**
  - rezoluție înaltă, palete configurabile, zoom/pan fluid.
- **Signals & Patterns view**
  - vizualizare time-domain per eveniment capturat.
  - overlay pattern pentru comparații manuale.
- **Sanity monitor**
  - buffer load în timp real.
  - dropped packets counter.
  - consolă unificată erori (hardware/API/system), cu mesaje inteligibile.

### Criterii de acceptare
- UI rămâne responsiv la flux continuu (latency vizuală minimă).
- Indicatorii de sănătate se actualizează în timp real și corect.

---

## 4) Pilonul de Alertare și Automatizare

### Obiectiv
Declanșarea automată de acțiuni la evenimente RF relevante.

### Capacități
- **Alert threshold (squelch inteligent)**
  - prag pe putere + filtre pe bandă/protocol.
- **Rule engine (IF/THEN)**
  - exemplu: „dacă Protocol X pe Frecvența Y cu putere > Z dBm, atunci record + alertă”.
- **Signal fingerprint database**
  - stocare pattern-uri pentru recunoaștere ulterioară.

### Criterii de acceptare
- Reguli executate determinist și auditate în log.
- Rată redusă de false trigger pe scenarii de test.

---

## 5) Viziunea de Implementare (Runtime Architecture)

- **True standalone**: distribuție self-contained, fără dependențe globale fragile.
- **OS țintă**: Linux-first (Ubuntu/Dragon/Kali), cu bridge local pentru integrarea UI ↔ backend ↔ hardware.
- **Backbone de comunicație**: bus de mesaje high-throughput (ex: ZeroMQ) între:
  1. achiziție,
  2. analiză,
  3. API/UI.
- **Rezistență la blocaje UI**: analiză și capture izolate în procese/thread-uri separate.

### Startup handshake în 3 pași
1. Detect hardware (`GREEN/RED`).
2. Verificare permisiuni USB (cu autofix ghidat unde este posibil).
3. Inițializare motor analiză (`READY`).

---

## 6) Roadmap recomandat (incremental)

### Faza A — Stabilizare fundație
- device manager multi-SDR + live controls complete.
- handshake startup + sanity indicators minimali.

### Faza B — Inteligență v1
- detector modulație + estimator baud + clasificator scop probabil.
- logging extins per eveniment.

### Faza C — UX operațional
- waterfall avansat + patterns view + consolă unificată de erori.

### Faza D — Automatizare v1
- rule engine IF/THEN + alertare + fingerprint DB.

### Faza E — Hardening standalone
- pachet final self-contained, validat pe distribuții Linux țintă.

---

## 7) KPI-uri de succes

- Timp startup până la „READY”.
- Stabilitate stream (dropped packets / oră).
- Acuratețe identificare modulație/protocol pe benchmark intern.
- Timp de reacție alertă după detecție.
- Stabilitate sesiune lungă (ex: 8h monitorizare continuă).

---

## Concluzie

BladeEye Evolution urmărește tranziția de la un simplu „spectrum viewer” la un sistem complet de **detecție, interpretare și automatizare RF**, robust în producție și ușor de pornit într-un singur click.
