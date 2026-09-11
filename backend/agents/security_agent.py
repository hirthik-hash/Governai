# backend/agents/security_agent.py

"""
Security & Risk Intelligence Agent (GovernAI Agent 3 of 7).

Computes a weighted risk score (0-100) from six behavioral/contextual
factors, following the original design's risk formula:

    risk_score = sum(weight for factor present) / sum(all weights) * 100

All six factor weights are defined from Day 1 of this agent's build
(today) so the scoring scale never changes - only detection logic
for individual factors gets added over Days 36-39. As of today,
three factors have real detection logic (stateless - computable from
Agent 1/2 output alone); the other three are defined but always
evaluate to False until their detection logic is built:

    IMPLEMENTED (Day 36):
      - department_mismatch   (from cross_department_request)
      - unusual_hour          (from after_hours_access)
      - classification_jump   (required_clearance - clearance >= 2)

    NOT YET IMPLEMENTED (always False until their day arrives):
      - repeated_failures     (Day 37 - needs a history tracker)
      - rapid_succession      (Day 38 - needs request-timing history)
      - geographic_anomaly    (Day 39 - needs a location stand-in)

risk_level is a human-readable label only, for reasoning/display -
the FSM itself acts on the raw risk_score, using the same thresholds
already defined in core.config.settings (escalation_risk_threshold=40,
hard_denial_risk_threshold=85), mirrored here for consistent labeling.
"""

from agents.base_agent import BaseAgent, AgentResult

# Factor weights - stable from today, per the original design.
FACTOR_WEIGHTS = {
    "repeated_failures": 10,
    "unusual_hour": 20,
    "department_mismatch": 15,
    "rapid_succession": 12,
    "geographic_anomaly": 25,
    "classification_jump": 30,
}

MAX_POSSIBLE_SCORE = sum(FACTOR_WEIGHTS.values())

# Mirrors core.config.settings thresholds - kept as local constants
# here since this agent shouldn't depend on config wiring not yet
# connected to the FSM itself (see Day 21 notes).
RISK_LEVEL_LOW_MAX = 39
RISK_LEVEL_MEDIUM_MAX = 84

# A clearance gap this large or more counts as a "classification jump" -
# attempting to access a resource far above one's own clearance level,
# not just modestly insufficient.
CLASSIFICATION_JUMP_THRESHOLD = 2


def classify_risk_level(risk_score: int) -> str:
    if risk_score <= RISK_LEVEL_LOW_MAX:
        return "LOW"
    if risk_score <= RISK_LEVEL_MEDIUM_MAX:
        return "MEDIUM"
    return "HIGH"


class SecurityRiskIntelligenceAgent(BaseAgent):
    """
    Computes a weighted risk score from behavioral/contextual factors.
    Takes the combined output of RequestUnderstandingAgent and
    AccessValidationAgent as input. Does not itself deny or escalate
    anything - produces risk_score for the FSM to act on, exactly as
    designed since Day 3's transition rulebook was written to expect
    a risk_score field.
    """

    @property
    def agent_name(self) -> str:
        return "security_risk_intelligence"

    def process(self, input_data: dict) -> AgentResult:
        clearance = input_data.get("clearance")
        required_clearance = input_data.get("required_clearance")

        if clearance is None or required_clearance is None:
            return self._failure(
                "Missing clearance information",
                errors=["clearance and required_clearance are required"],
            )

        factors_present = {
            "department_mismatch": bool(input_data.get("cross_department_request", False)),
            "unusual_hour": bool(input_data.get("after_hours_access", False)),
            "classification_jump": (required_clearance - clearance) >= CLASSIFICATION_JUMP_THRESHOLD,

            # Not yet implemented - see module docstring for the day
            # each will be built. Always False until then.
            "repeated_failures": False,
            "rapid_succession": False,
            "geographic_anomaly": False,
        }

        triggered_weight = sum(
            weight for factor, weight in FACTOR_WEIGHTS.items()
            if factors_present[factor]
        )
        risk_score = round((triggered_weight / MAX_POSSIBLE_SCORE) * 100)
        risk_level = classify_risk_level(risk_score)

        triggered_factors = [f for f, present in factors_present.items() if present]

        data = {
            "risk_score": risk_score,
            "risk_level": risk_level,
            "triggered_factors": triggered_factors,
        }

        if triggered_factors:
            reasoning = (
                f"Risk score {risk_score} ({risk_level}) - triggered factors: "
                f"{', '.join(triggered_factors)}"
            )
        else:
            reasoning = f"Risk score {risk_score} ({risk_level}) - no risk factors triggered"

        return self._success(data, reasoning)