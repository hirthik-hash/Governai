# backend/agents/security_agent.py

"""
Security & Risk Intelligence Agent (GovernAI Agent 3 of 7).

Computes a weighted risk score from behavioral risk factors, per the
formula: risk_score = sum(triggered factor weights) / max_possible * 100.

All 6 factors are now implemented (Days 36-38):
  - Stateless (Day 36): classification_jump, department_mismatch,
    unusual_hour - computable from a single request's data.
  - Stateful (Day 37): repeated_failures, rapid_succession - read
    from an injected RequestHistoryTracker.
  - Stateful (Day 38): geographic_anomaly - read from an injected
    GeoAnomalyDetector. Only flagged if the user has prior location
    history AND the new location isn't part of it; a first-ever
    request is never anomalous.

max_possible = sum of all 6 weights = 112, fixed since Day 36 so the
score's scale never shifted as factors were added incrementally.

This agent does not itself deny or escalate anything - it produces
risk_score for the FSM's existing thresholds (escalation at 40,
hard denial at 85, both defined in fsm/transitions.py) to act on.
Note: this agent does NOT call record_request()/record_location() -
that's the caller's responsibility, after the FSM's decision is
known, so history reflects actual outcomes, not just attempts.
"""

from agents.base_agent import BaseAgent, AgentResult
from core.request_history_tracker import RequestHistoryTracker
from core.geo_anomaly_detector import GeoAnomalyDetector

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


class SecurityRiskAgent(BaseAgent):
    """
    Computes risk_score from available request data plus two
    injected history dependencies. All 6 factors are live as of
    Day 38.
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

        if triggered_names:
            reasoning = (
                f"Risk score {risk_score} from triggered factors: "
                f"{', '.join(triggered_names)} "
                f"(raw weight {raw_score}/{MAX_POSSIBLE_RISK_WEIGHT})"
            )
        else:
            reasoning = f"No risk factors triggered - risk score {risk_score}"

        data = {
            "risk_score": risk_score,
            "risk_triggers": triggered_names,
        }

        return self._success(data, reasoning)