# backend/database/pending_store.py

"""
Database-backed durability for pending escalations (Day 79).

Unlike the audit ledger and decision log (Day 78, append-only), this
store is mutable working state: save() upserts, and delete() removes a
row once a request reaches a final outcome. Each call opens a short
session and commits before returning - the same short-session-per-
operation pattern used throughout (DatabaseDirectory,
DatabaseCredentialStore, DatabaseAuditStore).
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from agents.base_agent import AgentResult
from database.models import PendingEscalationModel
from database.serialization import to_json_safe
from database.session import session_scope


@dataclass(frozen=True)
class RestoredPendingEscalation:
    request_id: str
    fsm_state: str
    context: dict
    agent_results: list[AgentResult]
    timeout_sent_at: datetime
    timeout_seconds: "int | None"


def _serialize_agent_result(result: AgentResult) -> dict:
    return to_json_safe({
        "success": result.success, "data": result.data, "reasoning": result.reasoning,
        "errors": result.errors, "timestamp": result.timestamp,
    })


def _deserialize_agent_result(data: dict) -> AgentResult:
    return AgentResult(
        success=data["success"], data=data["data"], reasoning=data["reasoning"],
        errors=data["errors"], timestamp=data["timestamp"],
    )


class DatabasePendingEscalationStore:

    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def save(
        self,
        request_id: str,
        fsm_state: str,
        context: dict,
        agent_results: list[AgentResult],
        timeout_sent_at: datetime,
        timeout_seconds: "int | None" = None,
    ) -> None:
        with session_scope(self._session_factory) as session:
            model = session.scalar(select(PendingEscalationModel).where(PendingEscalationModel.request_id == request_id))
            if model is None:
                model = PendingEscalationModel(request_id=request_id)
                session.add(model)
            model.fsm_state = fsm_state
            model.context = to_json_safe(context)
            model.agent_results = [_serialize_agent_result(r) for r in agent_results]
            model.timeout_sent_at = timeout_sent_at.isoformat()
            model.timeout_seconds = timeout_seconds

    def delete(self, request_id: str) -> None:
        with session_scope(self._session_factory) as session:
            model = session.scalar(select(PendingEscalationModel).where(PendingEscalationModel.request_id == request_id))
            if model is not None:
                session.delete(model)

    def load_all(self) -> list[RestoredPendingEscalation]:
        """Every pending escalation, in the order it was originally parked."""
        session = self._session_factory()
        try:
            rows = session.scalars(select(PendingEscalationModel).order_by(PendingEscalationModel.id)).all()
            return [
                RestoredPendingEscalation(
                    request_id=row.request_id,
                    fsm_state=row.fsm_state,
                    context=dict(row.context),
                    agent_results=[_deserialize_agent_result(r) for r in row.agent_results],
                    timeout_sent_at=datetime.fromisoformat(row.timeout_sent_at),
                    timeout_seconds=row.timeout_seconds,
                )
                for row in rows
            ]
        finally:
            session.close()
