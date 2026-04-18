from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from .hardware import HardwareConfig
from .smart_functions import DetectionEvent


@dataclass
class ProSession:
    name: str
    config: dict[str, Any]
    watchlist: list[float] = field(default_factory=list)
    detections: list[dict[str, Any]] = field(default_factory=list)
    runtime_source: str = "local"

    @classmethod
    def from_runtime(
        cls,
        *,
        name: str,
        config: HardwareConfig,
        watchlist: list[float],
        detections: list[DetectionEvent],
        runtime_source: str = "local",
    ) -> "ProSession":
        return cls(
            name=name,
            config=asdict(config),
            watchlist=[float(freq) for freq in watchlist],
            detections=[asdict(det) for det in detections],
            runtime_source=("sidecar" if str(runtime_source).strip().lower() == "sidecar" else "local"),
        )


class SessionStore:
    def __init__(self, base_dir: Path | str = "sessions/pro_sessions") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def list_sessions(self) -> list[str]:
        return sorted(p.stem for p in self.base_dir.glob("*.json"))

    def save(self, session: ProSession) -> Path:
        path = self.base_dir / f"{session.name}.json"
        path.write_text(json.dumps(asdict(session), indent=2), encoding="utf-8")
        return path

    def load(self, name: str) -> ProSession:
        path = self.base_dir / f"{name}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ProSession(**payload)
