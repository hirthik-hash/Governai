# backend/core/decision_logger.py

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class LogEntryType(Enum):
    REQUEST_TRANSITION = "request_transition"
    SYSTEM_TRANSITION = "system_transition"
    SAFE_MODE_BLOCK = "safe_mode_block"


@dataclass
class LogEntry:
    entry_type: LogEntryType
    timestamp: str
    description: str
    from_state: str = ""
    to_state: str = ""
    request_id: str = ""
    context_snapshot: dict = field(default_factory=dict)


class DecisionLogger:
    """
    Central, append-only log for every decision made by either FSM
    or by the orchestration layer. In-memory for now - Phase 3 will
    add persistence to the database.
    """

    def __init__(self):
        self._entries: list[LogEntry] = []

    def log_request_transition(self, request_id: str, record) -> None:
        self._entries.append(LogEntry(
            entry_type=LogEntryType.REQUEST_TRANSITION,
            timestamp=record.timestamp,
            description=record.description,
            from_state=record.from_state.value,
            to_state=record.to_state.value,
            request_id=request_id,
            context_snapshot=record.context_snapshot,
        ))

    def log_system_transition(self, record) -> None:
        self._entries.append(LogEntry(
            entry_type=LogEntryType.SYSTEM_TRANSITION,
            timestamp=record.timestamp,
            description=record.description,
            from_state=record.from_state.value,
            to_state=record.to_state.value,
            context_snapshot=record.context_snapshot,
        ))

    def log_safe_mode_block(self, request_id: str, description: str, context: dict) -> None:
        self._entries.append(LogEntry(
            entry_type=LogEntryType.SAFE_MODE_BLOCK,
            timestamp=datetime.now(timezone.utc).isoformat(),
            description=description,
            request_id=request_id,
            context_snapshot=dict(context),
        ))

    def all_entries(self) -> list[LogEntry]:
        return list(self._entries)

    def entries_for_request(self, request_id: str) -> list[LogEntry]:
        return [e for e in self._entries if e.request_id == request_id]

    def entries_by_type(self, entry_type: LogEntryType) -> list[LogEntry]:
        return [e for e in self._entries if e.entry_type == entry_type]

    def print_all(self) -> None:
        for e in self._entries:
            tag = e.entry_type.value
            if e.from_state or e.to_state:
                print(f"[{e.timestamp}] [{tag}] {e.from_state} -> {e.to_state} | {e.description}")
            else:
                print(f"[{e.timestamp}] [{tag}] {e.description}")