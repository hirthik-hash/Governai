# backend/agents/security_agent.py

"""
Security & Risk Intelligence Agent (GovernAI Agent 3 of 7).

Computes a weighted risk score from behavioral risk factors, per the
formula: risk_score = sum(triggered factor weights) / max_possible * 100.

All 6 factors are implemented, in this fixed check order (also the
guaranteed order they appear in risk_triggers, per Day 40):

  1. classification_jump (weight 30) - stateless. Clearance shortfall
     of >= 2 levels. Excess clearance (negative shortfall) never
     triggers this, regardless of magnitude.
  2. department_mismatch (weight 15) - stateless. Reuses
     RequestUnderstandingAgent's cross_department_request flag.
  3. unusual_hour (weight 20) - stateless. Reuses
     AccessValidationAgent's after_hours_access flag.
  4. repeated_failures (weight 10) - stateful. Read from an injected
     RequestHistoryTracker (default: 3+ denials in 10 minutes).
  5. rapid_succession (weight 12) - stateful. Same tracker (default:
     5+ requests in 30 seconds).
  6. geographic_anomaly (weight 25) - stateful. Read from an injected
     GeoAnomalyDetector. A user's first-ever request is never
     anomalous - there's no baseline yet to compare against.

max_possible = sum of all 6 weights = 112, fixed since Day 36 so the
score's scale never shifted as factors were added incrementally.
Maxing out all 6 factors yields risk_score = 100 (confirmed Day 38);
5 factors alone tops out at ~78, below the hard-denial threshold -
geographic_anomaly is genuinely necessary to reach CRITICAL via
behavioral factors alone (Day 37/38 finding).

risk_level and recommendation (Day 39) are derived from risk_score
using thresholds imported from core.config.settings
(escalation_risk_threshold=40, hard_denial_risk_threshold=85) - the
SAME values fsm/transitions.py uses independently, so this agent's
description of risk can never contradict what the FSM actually does
with the same score (verified directly against fsm.transitions in
tests/test_security_agent.py).

This agent does not itself deny or escalate anything - it produces
risk_score for the FSM's existing thresholds to act on. It never
raises for expected input issues (missing clearance fields, missing
user_id/location) - those degrade gracefully to skipped checks or a
_failure() result, never an exception.

Full three-agent chain integration (RequestUnderstandingAgent ->
AccessValidationAgent -> SecurityRiskAgent -> GovernanceFSM), covering
every FSM outcome including risk-score-driven hard denial without
relying on blacklist_match, is in
tests/test_security_agent_fsm_integration.py (Day 41).
"""
from agents.base_agent import BaseAgent, AgentResult
from core.request_history_tracker import RequestHistoryTracker
from core.geo_anomaly_detector import GeoAnomalyDetector
from core.config import settings

FACTOR_WEIGHTS = {
    "repeated_failures": 10,
    "unusual_hour": 20,
    "department_mismatch": 15,
    "rapid_succession": 12,
    "geographic_anomaly": 25,
    "classification_jump": 30,
}

MAX_POSSIBLE_RISK_WEIGHT = sum(FACTOR_WEIGHTS.values())  # 112

CLASSIFICATION_JUMP_THRESHOLD = 2


def compute_risk_level_and_recommendation(risk_score: int) -> tuple[str, str]:
    """
    Derives a human-readable risk_level and recommendation from a
    numeric risk_score, using the exact same thresholds the FSM
    itself uses (core.config.settings), so this description never
    contradicts the FSM's actual behavior for the same score.
    """
    if risk_score >= settings.hard_denial_risk_threshold:
        return "CRITICAL", "HARD_DENY"

    if risk_score >= settings.escalation_risk_threshold:
        return "HIGH", "ESCALATE"

    if risk_score >= settings.escalation_risk_threshold // 2:
        return "MEDIUM", "MONITOR"

    return "LOW", "ALLOW"


class SecurityRiskAgent(BaseAgent):
    """
    Computes risk_score, risk_level, and recommendation from
    available request data plus two injected history dependencies.
    """

    def __init__(
        self,
        history_tracker: RequestHistoryTracker = None,
        geo_detector: GeoAnomalyDetector = None,
    ):
        super().__init__()
        self._history = history_tracker or RequestHistoryTracker()
        self._geo = geo_detector or GeoAnomalyDetector()

    @property
    def agent_name(self) -> str:
        return "security_risk"

    def process(self, input_data: dict) -> AgentResult:
        clearance = input_data.get("clearance")
        required_clearance = input_data.get("required_clearance")
        user_id = input_data.get("user_id")
        location = input_data.get("location")

        if clearance is None or required_clearance is None:
            return self._failure(
                "Missing clearance information",
                errors=["clearance and required_clearance are required"],
            )

        triggered = {}

        shortfall = required_clearance - clearance
        if shortfall >= CLASSIFICATION_JUMP_THRESHOLD:
            triggered["classification_jump"] = FACTOR_WEIGHTS["classification_jump"]

        if input_data.get("cross_department_request") is True:
            triggered["department_mismatch"] = FACTOR_WEIGHTS["department_mismatch"]

        if input_data.get("after_hours_access") is True:
            triggered["unusual_hour"] = FACTOR_WEIGHTS["unusual_hour"]

        if user_id:
            if self._history.has_repeated_failures(user_id):
                triggered["repeated_failures"] = FACTOR_WEIGHTS["repeated_failures"]
            if self._history.has_rapid_succession(user_id):
                triggered["rapid_succession"] = FACTOR_WEIGHTS["rapid_succession"]

        if user_id and location:
            if self._geo.is_anomalous(user_id, location):
                triggered["geographic_anomaly"] = FACTOR_WEIGHTS["geographic_anomaly"]

        raw_score = sum(triggered.values())
        risk_score = round((raw_score / MAX_POSSIBLE_RISK_WEIGHT) * 100)
        triggered_names = list(triggered.keys())

        risk_level, recommendation = compute_risk_level_and_recommendation(risk_score)

        if triggered_names:
            reasoning = (
                f"Risk score {risk_score} ({risk_level}) from triggered factors: "
                f"{', '.join(triggered_names)} "
                f"(raw weight {raw_score}/{MAX_POSSIBLE_RISK_WEIGHT}) - "
                f"recommendation: {recommendation}"
            )
        else:
            reasoning = (
                f"No risk factors triggered - risk score {risk_score} "
                f"({risk_level}) - recommendation: {recommendation}"
            )

        data = {
            "risk_score": risk_score,
            "risk_triggers": triggered_names,
            "risk_level": risk_level,
            "recommendation": recommendation,
        }

        return self._success(data, reasoning)