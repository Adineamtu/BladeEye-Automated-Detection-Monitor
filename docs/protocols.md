# Adding Built-in Protocol Definitions

The backend can recognise simple protocols by matching fixed binary headers.
Protocol definitions live in [`backend/protocols.py`](../backend/protocols.py).
Each definition provides:

- `name`: Human readable protocol name.
- `header`: Bit string that must match the start of a decoded transmission.
- `fields`: Mapping of field names to `(start, length)` tuples indicating
  the bit slice to extract when the header matches.

```python
ProtocolDefinition(
    name="ExampleProto",
    header="1010",          # expected header bits
    fields={"payload": (4, 8)},  # field name -> (start, length)
)
```

To add a new protocol:

1. Append a new `ProtocolDefinition` to the `PROTOCOLS` list in
   `backend/protocols.py` with the appropriate header and fields.
2. Ensure any additional metadata required by the protocol is represented as
   fields so it can be returned by the `identify_protocol()` helper.
3. Run the test suite (`pytest` and `npm test` in `frontend/`) to verify the
   new definition works as expected.

The API's `/api/signals/{frequency}/decode` endpoint automatically uses
`identify_protocol` on decoded bitstrings and exposes the protocol name and
fields in both the response JSON and the in-memory `Signal` object.


For a complete inventory of integrated RF devices and protocol definitions, see `docs/integrated_devices_and_protocols.md`.
