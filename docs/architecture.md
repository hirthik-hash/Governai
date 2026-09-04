# GovernAI — Architecture

## What this system is

GovernAI is an access-governance engine built on a simple principle:
**AI (or any external decision input) can reason about a request, but
a deterministic Finite State Machine (FSM) makes the actual decision.**
No component can talk its way past the FSM's rules.

The system is composed of two independent state machines:

1. **GovernanceFSM** — tracks a single access request's lifecycle
2. **RecoveryFSM** — tracks overall system health, independent of any request

Both share one generic engine (`BaseFSM`) and one consistent architecture:
a list of `Transition` rules, each pairing a `from_state` / `to_state`
with a small, named, independently-testable condition function.

---

## GovernanceFSM — Request Lifecycle

### States

| State | Meaning |
|---|---|
| `IDLE` | No request yet |
| `REQUEST_RECEIVED` | Request has entered the system |
| `PARSING_REQUEST` | Being interpreted/classified |
| `CLARIFICATION_REQUESTED` | Ambiguous — waiting on the requester |
| `VALIDATING_ACCESS` | Checking clearance and risk |
| `AUTHORIZED` | Passed validation, about to be granted |
| `ESCALATION_REQUIRED` | Needs human approval |
| `HARD_DENIED` | Critical risk or blacklist — no override possible |
| `MANAGER_REVIEW` | Awaiting human decision |
| `ACCESS_GRANTED` | Access has been granted |
| `DENIED_FINAL` | Terminal — access permanently denied |
| `AUDIT_LOGGING` | Recording the granted decision |
| `CLOSED` | Terminal — request lifecycle complete |

`CLOSED` and `DENIED_FINAL` are the only terminal states — once reached,
`BaseFSM` refuses any further transition (`TerminalStateError`).
### Diagram

IDLE -> REQUEST_RECEIVED -> PARSING_REQUEST
| ^
(ambiguous) | | (clarified)
v |
CLARIFICATION_REQUESTED
|
(clarified) -> VALIDATING_ACCESS
|
+-------------------------+-------------------------+
| | |
(hard denial trigger) (authorized) (needs escalation)
v v v
HARD_DENIED AUTHORIZED ESCALATION_REQUIRED
| | |
v v (escalation sent)
DENIED_FINAL ACCESS_GRANTED v
| MANAGER_REVIEW
v | |
AUDIT_LOGGING (approved) (rejected/
| | timeout)
v v v
CLOSED ACCESS_GRANTED DENIED_FINAL

### Key rules

- **Authorization** requires `clearance >= required_clearance AND risk_score < 40`
  AND must not simultaneously satisfy hard-denial conditions.
- **Escalation** triggers when clearance is insufficient OR risk is `>= 40`,
  as long as it isn't a hard-denial case.
- **Hard denial** triggers on `risk_score >= 85` OR a blacklist match —
  this cannot be overridden by any clearance level, including the maximum.
- **Escalation cannot be skipped**: `ESCALATION_REQUIRED -> MANAGER_REVIEW`
  requires `escalation_sent = True` — a request cannot proceed until
  someone/something has actually sent the notification.
- **Manager review** resolves via `approval_token_valid` (grants access) or
  `rejected` / `timed_out` (denies, both routing to the same terminal state
  but distinguishable in the audit history by description).

---

## RecoveryFSM — System Health (Safe Mode)

### States

| State | Meaning |
|---|---|
| `SYSTEM_NORMAL` | Healthy, full operation |
| `DEGRADED_WARNING` | A failure was detected, not yet critical |
| `SAFE_MODE_ACTIVE` | Critical failure — restricted operation |
| `RESTORING` | Recovery signal received, confirming stability |

No state here is terminal — the entire point of this FSM is that it recovers.

### Diagram
SYSTEM_NORMAL --(unhealthy)--> DEGRADED_WARNING
| |
(critical) | | (recovered before critical)
v v
SAFE_MODE_ACTIVE SYSTEM_NORMAL
|
(recovery signal)
v
RESTORING
/
(confirmed) (fails again)
v v
SYSTEM_NORMAL SAFE_MODE_ACTIVE

### Key rule: the safe-mode gate

`SystemAwareRequestProcessor` (in `core/orchestrator.py`) is the only
point where the two FSMs interact. It does **not** merge them — it
simply refuses to let a request move from `AUTHORIZED` into
`ACCESS_GRANTED` while `RecoveryFSM.is_safe_mode()` is `True`, unless
the request is flagged `is_public_readonly`. This is the concrete
implementation of "deny sensitive operations, allow read-only public
access" during a system-health degradation.

---

## Shared engine: `BaseFSM`

Both FSMs are thin subclasses of one generic `BaseFSM` (introduced Day 15,
refactored from two near-identical implementations). `BaseFSM` owns:

- Candidate transition lookup from the current state
- Ambiguous-match detection (`AmbiguousTransitionError`) — a rulebook
  safety net, not just a runtime error
- Transition execution and timestamped history recording
- `run_until_stuck()` for chaining unconditional transitions

Subclasses supply only: their rulebook, their initial state, and
(for `GovernanceFSM` only) which states are terminal.

---

## Real bugs this design caught during development

Worth keeping on record — this is direct evidence of the FSM's
ambiguity-detection acting as a genuine safety mechanism, not just a
theoretical one.

### Bug 1: `is_authorized` didn't exclude hard-denial conditions

**Symptom:** A blacklisted, low-risk, high-clearance test case raised
`AmbiguousTransitionError` instead of hard-denying.

**Root cause:** `is_authorized` only checked `clearance >= required AND
risk < 40` — it never checked for a blacklist match. A blacklisted user
with low risk and high clearance satisfied *both* the hard-denial rule
and the authorization rule at once.

**Fix:** `is_authorized` now also requires `not is_hard_denied(ctx)`.

**Why it matters:** this was a real, silent security gap — without the
ambiguity check, rule ordering could have let a blacklisted user reach
`AUTHORIZED` depending on which condition happened to be checked first.
The FSM's refusal to guess is what surfaced it.

### Bug 2: Miscounted transition steps in a test assertion

**Symptom:** A test expected `VALIDATING_ACCESS -> AUTHORIZED` after
three `transition()` calls, but the third call only reached
`VALIDATING_ACCESS` itself.

**Root cause:** `PARSING_REQUEST -> VALIDATING_ACCESS` is itself a full
transition step; a fourth call is needed to resolve `VALIDATING_ACCESS`.

**Fix:** Added the missing `transition()` call before the assertion.

**Why it matters:** a reminder that off-by-one errors in *tests* are as
real a risk as bugs in the system under test — the FSM's explicit,
enumerable states made this easy to diagnose precisely because every
step is a named, inspectable state rather than an implicit one.

---

## Current test coverage

As of Day 16: **60 passing tests** across:
- Happy-path authorization (7)
- Escalation triggers, notification gating, manager outcomes (12)
- Hard denial triggers, lifecycle, rulebook integrity (10)
- Safe Mode FSM: warning resolution, escalation, restoration (15)
- Ambiguity/clarification loop (5)
- Invalid transition edge cases (5)
- Escalation timeout tracker (6)

## Not yet built (upcoming phases)

- Agent layer (Request Understanding, Access Validation, Security &
  Risk, Escalation, Audit, Failure Recovery, Policy Intelligence)
- Database persistence (currently everything is in-memory)
- FastAPI backend / REST endpoints
- Frontend (Next.js dashboard)
- Real health-check logic driving `RecoveryFSM` (currently only reacts
  to manually-passed `system_healthy`/`critical_failure` flags)