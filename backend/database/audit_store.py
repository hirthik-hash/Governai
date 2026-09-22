# backend/database/audit_store.py

"""
Database-backed durability for the audit ledger and decision log (Day 78).

Both classes open a short session per call and commit before returning -
the same short-session-per-operation pattern as DatabaseDirectory and
DatabaseCredentialStore (Day 75-76), because sessions are not
thread-safe and FastAPI serves requests from more than one thread.

RequestPipeline keeps its own in-memory audit_records list as the fast
read path for the current process (unchanged from Day 70); these classes
are the durability layer underneath it - what makes the ledger survive a
restart, and what has_final_record() checks to refuse a replayed
request_id even from a brand-new pipeline instance.
"""

from sqlalchemy.orm import sessionmaker

from agents.audit_agent import AuditRecord
from core.decision_logger import DecisionLogger, LogEntry
from database.models import AuditRecordModel, DecisionLogEntryModel
from database.repositories import AuditRecordRepository, DecisionLogRepository
from database.session import session_scope


class DatabaseAuditStore:

    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def add(self, record: AuditRecord) -> None:
        with session_scope(self._session_factory) as session:
            AuditRecordRepository(session).add(record)

    def all(self) -> list[AuditRecord]:
        session = self._session_factory()
        try:
            return AuditRecordRepository(session).all()
        finally:
            session.close()

    def has_final_record(self, request_id: str) -> bool:
        session = self._session_factory()
        try:
            return AuditRecordRepository(session).has_final_record(request_id)
        finally:
            session.close()


class PersistentDecisionLogger(DecisionLogger):
    """
    Drop-in replacement for DecisionLogger: SystemAwareRequestProcessor
    calls the same three log_*() methods and does not know persistence is
    happening. Each call still updates the in-memory lists the base class
    keeps (all_entries() etc. keep working unchanged, e.g. for tests) and
    additionally writes the entry to the database.
    """

    def __init__(self, session_factory: sessionmaker):
        super().__init__()
        self._session_factory = session_factory

    def _persist(self, entry: LogEntry) -> None:
        with session_scope(self._session_factory) as session:
            DecisionLogRepository(session).add(entry)

    def log_request_transition(self, request_id: str, record) -> None:
        super().log_request_transition(request_id, record)
        self._persist(self.all_entries()[-1])

    def log_system_transition(self, record) -> None:
        super().log_system_transition(record)
        self._persist(self.all_entries()[-1])

    def log_safe_mode_block(self, request_id: str, description: str, context: dict) -> None:
        super().log_safe_mode_block(request_id, description, context)
        self._persist(self.all_entries()[-1])
