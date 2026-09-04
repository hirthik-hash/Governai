# backend/fsm/transitions.py

from dataclasses import dataclass
from typing import Callable, Any
from fsm.states import RequestState


@dataclass
class Transition:
    from_state: RequestState
    to_state: RequestState
    condition: Callable[[dict], bool]
    description: str


# ---- Condition helper functions ----
# Small, named, testable in isolation — the Transition objects below
# just wire these together.

def is_ambiguous(ctx: dict) -> bool:
    return ctx.get("ambiguity_flag", False) is True


def is_clarified(ctx: dict) -> bool:
    return ctx.get("ambiguity_flag", False) is False


def is_authorized(ctx: dict) -> bool:
    clearance = ctx.get("clearance", 0)
    required = ctx.get("required_clearance", 0)
    risk = ctx.get("risk_score", 0)
    return not is_hard_denied(ctx) and clearance >= required and risk < 40


def needs_escalation(ctx: dict) -> bool:
    clearance = ctx.get("clearance", 0)
    required = ctx.get("required_clearance", 0)
    risk = ctx.get("risk_score", 0)
    hard_denied = is_hard_denied(ctx)
    return not hard_denied and (clearance < required or risk >= 40)


def is_hard_denied(ctx: dict) -> bool:
    risk = ctx.get("risk_score", 0)
    return risk >= 85 or ctx.get("blacklist_match", False) is True


def escalation_sent(ctx: dict) -> bool:
    return ctx.get("escalation_sent", False) is True


def manager_approved(ctx: dict) -> bool:
    return ctx.get("approval_token_valid", False) is True


def manager_rejected_or_timeout(ctx: dict) -> bool:
    return ctx.get("rejected", False) is True or ctx.get("timed_out", False) is True


def always(ctx: dict) -> bool:
    return True


# ---- The rulebook ----

TRANSITIONS: list[Transition] = [

    Transition(
        RequestState.IDLE, RequestState.REQUEST_RECEIVED,
        always, "New request received"
    ),

    Transition(
        RequestState.REQUEST_RECEIVED, RequestState.PARSING_REQUEST,
        always, "Request handed to parsing"
    ),

    Transition(
        RequestState.PARSING_REQUEST, RequestState.CLARIFICATION_REQUESTED,
        is_ambiguous, "Request is ambiguous, clarification needed"
    ),
    Transition(
        RequestState.CLARIFICATION_REQUESTED, RequestState.PARSING_REQUEST,
        is_clarified, "User clarified the request, re-parsing"
    ),
    Transition(
        RequestState.PARSING_REQUEST, RequestState.VALIDATING_ACCESS,
        is_clarified, "Request is clear, proceeding to validation"
    ),

    # From VALIDATING_ACCESS — order matters: hard denial checked first
    Transition(
        RequestState.VALIDATING_ACCESS, RequestState.HARD_DENIED,
        is_hard_denied, "Risk score critical or blacklist match"
    ),
    Transition(
        RequestState.VALIDATING_ACCESS, RequestState.AUTHORIZED,
        is_authorized, "Clearance sufficient and risk acceptable"
    ),
    Transition(
        RequestState.VALIDATING_ACCESS, RequestState.ESCALATION_REQUIRED,
        needs_escalation, "Clearance insufficient or risk elevated"
    ),

    Transition(
        RequestState.AUTHORIZED, RequestState.ACCESS_GRANTED,
        always, "Authorization confirmed, granting access"
    ),

    Transition(
        RequestState.ESCALATION_REQUIRED, RequestState.MANAGER_REVIEW,
        escalation_sent, "Escalation notification sent to approver"
    ),

    Transition(
        RequestState.MANAGER_REVIEW, RequestState.ACCESS_GRANTED,
        manager_approved, "Approver granted access"
    ),
    Transition(
        RequestState.MANAGER_REVIEW, RequestState.DENIED_FINAL,
        manager_rejected_or_timeout, "Approver rejected or approval timed out"
    ),

    Transition(
        RequestState.HARD_DENIED, RequestState.DENIED_FINAL,
        always, "Hard denial finalized"
    ),

    Transition(
        RequestState.ACCESS_GRANTED, RequestState.AUDIT_LOGGING,
        always, "Logging granted access decision"
    ),
    Transition(
        RequestState.AUDIT_LOGGING, RequestState.CLOSED,
        always, "Request lifecycle complete"
    ),
]