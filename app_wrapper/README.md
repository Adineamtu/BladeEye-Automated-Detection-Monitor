# app_wrapper

Acest folder conține stratul de orchestrare pentru rularea proiectului ca aplicație standalone.

## Ce face launcher-ul

- pornește `sdr_core` (C++) în background, fără consolă vizibilă;
- pornește API-ul FastAPI pe un port dinamic liber;
- deschide UI-ul React într-o fereastră nativă (`PySide6 + Qt WebEngine`), fără browser extern;
- oprește automat procesele copil la închiderea ferestrei;
- afișează mesaje de eroare native dacă lipsesc binarele sau frontend-ul build-uit.

## Build local (one-click binary)

```bash
python app_wrapper/build_standalone.py
```

Scriptul:
1. face build la frontend;
2. compilează `cpp/sdr_core`;
3. rulează PyInstaller cu `app_wrapper/reactive_jam.spec`;
4. produce arhiva finală (`reactive_jam_standalone.tar.gz` sau `.zip`).
