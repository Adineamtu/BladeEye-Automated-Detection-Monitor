"""Execution board domain model and persistence helpers."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
import json

TaskStatus = Literal["todo", "in_progress", "blocked", "done"]


@dataclass
class ExecutionTask:
    """Single actionable task in the BladeEye delivery board."""

    id: str
    phase: str
    title: str
    description: str
    owner: str | None = None
    status: TaskStatus = "todo"
    acceptance_criteria: str = ""
    notes: str = ""


@dataclass
class ExecutionBoard:
    """Top-level board metadata and task list."""

    version: int
    board_name: str
    updated_at: str
    tasks: list[ExecutionTask]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_execution_board() -> ExecutionBoard:
    """Return the default execution board aligned to the critical path roadmap."""
    return ExecutionBoard(
        version=2,
        board_name="BladeEye Final Execution Board",
        updated_at=_now_iso(),
        tasks=[
            ExecutionTask(
                id="P1-T1",
                phase="Faza 1 · The Plumbing",
                title="Migrarea completă a spectrului pe ZeroMQ",
                description="Activare implicită BLADEEYE_DATA_BRIDGE=zmq și eliminare cod Shared Memory (SHM) din pipeline-ul live.",
                owner="rf-core",
                status="in_progress",
                acceptance_criteria="Toate fluxurile spectrum rulează exclusiv pe ZeroMQ, fără fallback SHM în runtime normal.",
                notes="Task critic pornit: stabilizarea legăturii C++/Python înaintea modulelor de inteligență.",
            ),
            ExecutionTask(
                id="P1-T2",
                phase="Faza 1 · The Plumbing",
                title="Sincronizare Preflight -> UI",
                description="Legare runtime_mode în UI și afișare banner discret în demo mode.",
                acceptance_criteria='În demo mode apare bannerul "Running in Simulation Mode - No Hardware Detected" fără regresii UI.',
            ),
            ExecutionTask(
                id="P2-T1",
                phase="Faza 2 · The Brain",
                title="Thread de clasificare a semnalului",
                description="Procesor asincron pentru clasificare modulation type (AM/FM/ASK/FSK) și calcul signal strength (RSSI).",
                acceptance_criteria="Clasificare live cu scor de încredere și RSSI expuse în API/UI.",
            ),
            ExecutionTask(
                id="P2-T2",
                phase="Faza 2 · The Brain",
                title="Motor de deducție baud rate",
                description="Estimare automată a vitezei de simbol pentru semnale digitale detectate.",
                acceptance_criteria="Estimări stabile și reproductibile pe capturi de referință digitale.",
            ),
            ExecutionTask(
                id="P2-T3",
                phase="Faza 2 · The Brain",
                title="Bază de date protocoale și labeling",
                description="Fingerprinting pe pattern-uri cunoscute pentru Likely Purpose (Pager, Car Key, Weather Station).",
                acceptance_criteria="Fiecare semnal compatibil primește etichetă probabilistică și referință fingerprint.",
            ),
            ExecutionTask(
                id="P3-T1",
                phase="Faza 3 · The Dashboard",
                title="Optimizare waterfall pe GPU",
                description="Mutare randare cascadă pe Canvas/WebGL pentru reducerea încărcării CPU.",
                acceptance_criteria="Randare fluentă la load ridicat fără degradarea latenței de analiză.",
            ),
            ExecutionTask(
                id="P3-T2",
                phase="Faza 3 · The Dashboard",
                title="Dashboard de telemetrie în timp real",
                description="Afișare Buffer Load, rată transfer ZMQ și fereastră consolidată Error Logs (sdr_core + preflight).",
                acceptance_criteria="Metrici și log-uri consolidate vizibile live direct din UI.",
            ),
            ExecutionTask(
                id="P4-T1",
                phase="Faza 4 · Mission Ready",
                title="Hopping Engine & Actions",
                description="Logică de Frequency Hopping pe presets și acțiuni automate (ex: trigger înregistrare I/Q).",
                acceptance_criteria="Reguli IF/THEN executate determinist la detecția protocolului țintă.",
            ),
            ExecutionTask(
                id="P4-T2",
                phase="Faza 4 · Mission Ready",
                title="Bundling standalone",
                description="Pachet self-contained cu Python runtime, librării C++ statice și drivere USB fără apt/sudo.",
                acceptance_criteria="Instalare și rulare pe sistem țintă fără dependențe externe suplimentare.",
            ),
            ExecutionTask(
                id="P4-T3",
                phase="Faza 4 · Mission Ready",
                title="Verificare finală pe Execution Board",
                description="Închiderea tuturor task-urilor și rularea suitei complete de stress testing long-run.",
                acceptance_criteria="Toate task-urile marcate done și stabilitate confirmată pe test de durată.",
            ),
        ],
    )


def _board_to_dict(board: ExecutionBoard) -> dict:
    return {
        "version": board.version,
        "board_name": board.board_name,
        "updated_at": board.updated_at,
        "tasks": [asdict(task) for task in board.tasks],
    }


def save_execution_board(path: Path, board: ExecutionBoard) -> None:
    """Persist execution board to JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    board.updated_at = _now_iso()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_board_to_dict(board), fh, indent=2, ensure_ascii=False)


def load_execution_board(path: Path) -> ExecutionBoard:
    """Load execution board from disk or return defaults if missing/invalid."""
    if not path.exists():
        board = default_execution_board()
        save_execution_board(path, board)
        return board

    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        tasks = [ExecutionTask(**item) for item in payload.get("tasks", [])]
        if not tasks:
            raise ValueError("Empty board payload")
        return ExecutionBoard(
            version=int(payload.get("version", 1)),
            board_name=str(payload.get("board_name", "BladeEye Evolution Execution Board")),
            updated_at=str(payload.get("updated_at", _now_iso())),
            tasks=tasks,
        )
    except Exception:
        board = default_execution_board()
        save_execution_board(path, board)
        return board


def update_task(
    board: ExecutionBoard,
    task_id: str,
    *,
    status: TaskStatus | None = None,
    owner: str | None = None,
    notes: str | None = None,
) -> ExecutionTask:
    """Update mutable task fields and return updated task."""
    for task in board.tasks:
        if task.id == task_id:
            if status is not None:
                task.status = status
            if owner is not None:
                task.owner = owner
            if notes is not None:
                task.notes = notes
            board.updated_at = _now_iso()
            return task
    raise KeyError(task_id)
