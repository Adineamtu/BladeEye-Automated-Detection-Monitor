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
    """Return the default execution board derived from project roadmap."""
    return ExecutionBoard(
        version=1,
        board_name="BladeEye Evolution Execution Board",
        updated_at=_now_iso(),
        tasks=[
            ExecutionTask(
                id="F1-T1",
                phase="Foundation",
                title="Multi-SDR device manager",
                description="Detectare hardware agnostică (VID/PID) cu prioritate BladeRF și selector explicit de device.",
                acceptance_criteria="Detectarea device-ului activ în <2 secunde la pornire.",
            ),
            ExecutionTask(
                id="F1-T2",
                phase="Foundation",
                title="Live controls fără restart",
                description="Aplicare dinamică pentru center_freq, sample_rate și gain.",
                acceptance_criteria="Nicio întrerupere de stream la modificări repetate timp de 30 minute.",
            ),
            ExecutionTask(
                id="F1-T3",
                phase="Foundation",
                title="Startup handshake în 3 pași",
                description="Status hardware detect, USB permissions și analysis engine ready.",
                acceptance_criteria="Toți pașii sunt expuși în API și afișați în UI.",
            ),
            ExecutionTask(
                id="F2-T1",
                phase="Intelligence v1",
                title="Detector modulație",
                description="Clasificare AM/FM/ASK/FSK/PSK cu scor de încredere.",
                acceptance_criteria="Acuratețe minimă pe benchmark intern stabilit.",
            ),
            ExecutionTask(
                id="F2-T2",
                phase="Intelligence v1",
                title="Estimator baud-rate",
                description="Deducere automată a vitezei de simbol pentru semnale digitale.",
                acceptance_criteria="Estimări stabile pe capturi de referință.",
            ),
            ExecutionTask(
                id="F3-T1",
                phase="UX",
                title="Sanity monitor",
                description="Panou buffer load, dropped packets și erori consolidate.",
                acceptance_criteria="Metricile se actualizează în timp real fără blocaj UI.",
            ),
            ExecutionTask(
                id="F4-T1",
                phase="Automation",
                title="Rule engine IF/THEN",
                description="Reguli pe protocol/frecvență/prag putere cu trigger record + alert.",
                acceptance_criteria="Reguli deterministe și auditate în log.",
            ),
            ExecutionTask(
                id="F5-T1",
                phase="Hardening",
                title="Standalone packaging",
                description="Bundle Linux self-contained cu verificări de startup și recovery.",
                acceptance_criteria="Pornire one-click validată pe distribuțiile țintă.",
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
