# Foundation Phase Delivery (Faza 1)

Această livrare implementează primele elemente critice din traseul de consolidare:

## Ce s-a implementat acum

1. **Pre-Flight Check API**
   - detectare BladeRF (best-effort, `lsusb` + VID `2cf0`)
   - verificare permisiuni USB
   - fallback automat în `demo` dacă hardware-ul sau permisiunile nu sunt valide
   - endpoint nou: `GET /api/preflight`

2. **Runtime mode robust (hardware/demo)**
   - `config_state` include `runtime_mode`, `hardware_detected`, `usb_access_ok`
   - `GET /api/health` nu mai cade în demo mode; returnează health payload sigur

3. **Data bridge pregătit pentru ZeroMQ**
   - modul nou `backend/zmq_bridge.py`
   - activare prin env:
     - `BLADEEYE_DATA_BRIDGE=zmq`
     - `BLADEEYE_ZMQ_ENDPOINT=tcp://127.0.0.1:5557`
   - endpoint-urile websocket de spectrum încearcă mai întâi ZeroMQ, apoi fallback pe shared-memory, apoi monitor/demo

## Ce urmează (următor sprint)

- Definirea contractului binar de frame (header + payload) pentru fluxul C++ → Python via ZeroMQ.
- Migrarea producer-ului C++ la Push real de frame-uri I/Q/spectrum.
- Metrici reale de pipeline (`buffer_load`, `dropped_packets`) din bridge-ul ZeroMQ.
