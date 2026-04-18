# Protocol and Signature Definitions

BladeEye uses two complementary mechanisms for signal interpretation:

1. **Protocol definitions** (structured bit/header matching).
2. **RF signature matching** (feature-based identification from captured IQ behavior).

---

## 1) Built-in Protocol Definitions

Protocol definitions are maintained in `backend/protocols.py`.

Each protocol entry provides:

- `name`: human-readable protocol name.
- `header`: expected bit prefix.
- `fields`: mapping of decoded field names to `(start, length)` slices.

Example:

```python
ProtocolDefinition(
    name="ExampleProto",
    header="1010",
    fields={"payload": (4, 8)},
)
```

### Adding a new protocol definition

1. Add a new `ProtocolDefinition` to `PROTOCOLS` in `backend/protocols.py`.
2. Define all required decoded fields in `fields`.
3. Validate through tests (`pytest`) and relevant frontend checks (`npm test` in `frontend/`).

---

## 2) Signature-Based Identification

Signature logic and data are provided through the backend intelligence modules (for example `backend/signatures_data.py` and related engine code). Typical outcomes include:

- protocol hints,
- likely-purpose annotations,
- confidence-aware classification context.

Signature matching is especially useful when a strict protocol header match is unavailable.

---

## 3) Monitor vs Lab Usage

- **Monitor** applies fast classification/intelligence for live operational awareness.
- **Lab** enables deeper post-capture analysis and validation of signal behavior, including rolling-code related investigations.

Use both in sequence for highest confidence:

1. detect live in Monitor,
2. validate and refine in Lab,
3. feed findings back into signatures/protocol definitions.

---

## 4) Validation Checklist for New Definitions

When adding protocols or signature rules, verify:

- correct bit-header alignment,
- field extraction boundaries,
- no regressions in existing detections,
- expected API payload shape,
- report output correctness.

Minimum recommended checks:

```bash
pytest -q
cd frontend && npm test
```
