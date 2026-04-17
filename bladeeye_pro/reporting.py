from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import html

from .smart_functions import DetectionEvent

URBAN_NOISE_HINTS = (
    "ant+",
    "ant and ant+",
    "weather",
    "meteo",
    "tpms",
    "tire pressure",
)


def is_urban_noise_label(label: str) -> bool:
    text = label.lower()
    return any(hint in text for hint in URBAN_NOISE_HINTS)


@dataclass
class ReportEventGroup:
    events: list[DetectionEvent] = field(default_factory=list)
    freqs_hz: list[float] = field(default_factory=list)

    def add(self, event: DetectionEvent) -> None:
        self.events.append(event)
        if event.center_freq not in self.freqs_hz:
            self.freqs_hz.append(event.center_freq)

    @property
    def head(self) -> DetectionEvent:
        return self.events[0]

    @property
    def is_fhss(self) -> bool:
        return len(self.freqs_hz) >= 3


def _same_signal_signature(a: DetectionEvent, b: DetectionEvent) -> bool:
    if a.modulation != b.modulation:
        return False
    if (a.label or "").strip() != (b.label or "").strip():
        return False
    a_baud = max(1.0, a.baud_rate)
    return abs(a.baud_rate - b.baud_rate) <= a_baud * 0.20


def group_detection_events(events: list[DetectionEvent], correlation_window_ms: float = 250.0) -> list[ReportEventGroup]:
    groups: list[ReportEventGroup] = []
    window_s = max(0.01, correlation_window_ms / 1000.0)
    for event in events:
        matched = None
        for group in groups:
            head = group.head
            if abs(head.timestamp - event.timestamp) > window_s:
                continue
            if not _same_signal_signature(head, event):
                continue
            matched = group
            break
        if matched is None:
            matched = ReportEventGroup()
            groups.append(matched)
        matched.add(event)
    return groups


def build_full_intelligence_report_html(
    *,
    detections: list[DetectionEvent],
    watchlist: list[float],
    raw_hex_max_chars: int = 64,
    hide_urban_noise: bool = False,
) -> str:
    filtered = [evt for evt in detections if not (hide_urban_noise and is_urban_noise_label(evt.label or ""))]
    groups = group_detection_events(filtered)
    rows = []
    for group in groups:
        evt = group.head
        label = evt.label or "Unknown / Raw Signal"
        safe_label = html.escape(label)
        if safe_label.startswith("Unknown"):
            safe_label = f"{safe_label} (User Tag: n/a)"
        if group.is_fhss:
            freqs = ", ".join(f"{f / 1e6:.6f}" for f in sorted(group.freqs_hz))
            freq_cell = f"FHSS Sequence ({len(group.freqs_hz)}): {freqs} MHz"
        else:
            freq_cell = f"{evt.center_freq / 1e6:.6f} MHz"
        rows.append(
            "<tr>"
            f"<td>{datetime.fromtimestamp(evt.timestamp).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}</td>"
            f"<td>{freq_cell}</td>"
            f"<td>{html.escape(evt.modulation)}</td>"
            f"<td>{evt.baud_rate:.1f}</td>"
            f"<td>{safe_label}</td>"
            f"<td>{evt.signal_strength:.5f}</td>"
            f"<td class='raw-hex'>{html.escape((evt.raw_hex or '')[:raw_hex_max_chars])}</td>"
            "</tr>"
        )
    return (
        "<html><head><meta charset='utf-8'><style>"
        "@page { size: A4 portrait; margin: 12mm; }"
        "body { font-family: Arial, sans-serif; font-size: 12px; color: #111; }"
        "h1 { margin: 0 0 8px 0; font-size: 42px; }"
        "p { margin: 3px 0; }"
        "table { border-collapse: collapse; width: 100%; table-layout: fixed; font-size: 11px; }"
        "th, td { border: 1px solid #777; padding: 4px; vertical-align: top; }"
        "th { background: #eceff3; }"
        "td.raw-hex { word-break: break-all; font-family: monospace; font-size: 10px; }"
        "tr { page-break-inside: avoid; }"
        "</style></head><body>"
        "<h1>BladeEye Full Intelligence Report</h1>"
        f"<p>Generated: {datetime.now(timezone.utc).isoformat()}</p>"
        f"<p>Detections: {len(filtered)} (from {len(detections)} total)</p>"
        f"<p>Watchlist: {', '.join(f'{f:.0f}' for f in watchlist) or 'none'}</p>"
        f"<p>Urban noise filter: {'ON' if hide_urban_noise else 'OFF'}</p>"
        "<table>"
        "<thead><tr><th style='width:16%'>Time</th><th style='width:20%'>Freq</th><th style='width:7%'>Mod</th>"
        "<th style='width:8%'>Baud</th><th style='width:18%'>Label</th><th style='width:9%'>Signal Strength</th>"
        "<th style='width:22%'>Raw Hex</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table></body></html>"
    )
