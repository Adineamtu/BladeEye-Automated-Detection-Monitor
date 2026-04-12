from __future__ import annotations

"""Definitions for protocol headers and helpers including user additions."""

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional
import json


@dataclass(frozen=True)
class ProtocolDefinition:
    """Description of a binary protocol header and its fields."""

    name: str
    header: str
    fields: Dict[str, tuple[int, int]]


# A small collection of example protocol definitions.  These serve both as
# built-in decoders and as documentation for how to add new protocols.
PROTOCOLS: List[ProtocolDefinition] = [
    ProtocolDefinition(
        name="ExampleProto",
        header="1010",
        fields={"payload": (4, 8)},
    ),
]


# ---------------------------------------------------------------------------
# User-defined protocol support

SESSIONS_DIR = Path("sessions")
USER_PROTO_FILE = SESSIONS_DIR / "user_protocols.json"


@dataclass
class UserProtocol:
    """Schema for a user-defined protocol.

    ``data_field_structure`` maps field names to ``(start, length)`` tuples and
    mirrors ``ProtocolDefinition.fields`` for built-in protocols.
    """

    protocol_name: str
    modulation_type: str
    baud_rate: float
    header_pattern: str
    data_field_structure: Dict[str, List[int]]


def load_user_protocols() -> List[UserProtocol]:
    """Return all persisted user protocol definitions."""

    if not USER_PROTO_FILE.exists():
        return []
    with open(USER_PROTO_FILE, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return [UserProtocol(**item) for item in data]


def save_user_protocol(proto: UserProtocol) -> None:
    """Append *proto* to the JSON store on disk."""

    SESSIONS_DIR.mkdir(exist_ok=True)
    protocols = load_user_protocols()
    protocols.append(proto)
    with open(USER_PROTO_FILE, "w", encoding="utf-8") as fh:
        json.dump([asdict(p) for p in protocols], fh, indent=2)


def identify_protocol(bits: str) -> Optional[dict]:
    """Return protocol metadata for *bits* if a known header matches.

    User-defined protocols are loaded from ``USER_PROTO_FILE`` on each call so
    newly added entries take effect immediately.
    """

    # Check built-in definitions first
    for proto in PROTOCOLS:
        if bits.startswith(proto.header):
            extracted: Dict[str, str] = {}
            for field, (start, length) in proto.fields.items():
                end = start + length
                if end <= len(bits):
                    extracted[field] = bits[start:end]
            return {"name": proto.name, "fields": extracted}

    # Then check user-defined protocols
    for proto in load_user_protocols():
        if bits.startswith(proto.header_pattern):
            extracted: Dict[str, str] = {}
            for field, (start, length) in proto.data_field_structure.items():
                end = start + length
                if end <= len(bits):
                    extracted[field] = bits[start:end]
            return {
                "name": proto.protocol_name,
                "fields": extracted,
                "modulation_type": proto.modulation_type,
                "baud_rate": proto.baud_rate,
            }
    return None
