# backend/cli.py

import argparse
from fsm.governance_fsm import GovernanceFSM
from fsm.recovery_fsm import RecoveryFSM
from core.orchestrator import SystemAwareRequestProcessor, AccessBlockedBySafeModeError
from core.decision_logger import DecisionLogger


PRESETS = {
    "authorize": {"clearance": 3, "required_clearance": 2, "risk_score": 10, "blacklist_match": False},
    "escalate": {"clearance": 1, "required_clearance": 3, "risk_score": 10, "blacklist_match": False},
    "hard_deny": {"clearance": 5, "required_clearance": 1, "risk_score": 95, "blacklist_match": False},
}


def build_context(args) -> dict:
    if args.preset:
        context = dict(PRESETS[args.preset])
    else:
        context = {
            "clearance": args.clearance,
            "required_clearance": args.required_clearance,
            "risk_score": args.risk_score,
            "blacklist_match": args.blacklist,
        }
    context["ambiguity_flag"] = False
    context["is_public_readonly"] = args.public_readonly
    return context


def main():
    parser = argparse.ArgumentParser(
        description="GovernAI CLI - manually drive the FSM for a single simulated request."
    )
    parser.add_argument("--preset", choices=PRESETS.keys(), help="Use a named scenario preset.")
    parser.add_argument("--clearance", type=int, default=3, help="Requester's clearance level.")
    parser.add_argument("--required-clearance", type=int, default=2, help="Clearance required for the resource.")
    parser.add_argument("--risk-score", type=int, default=10, help="Computed risk score (0-100).")
    parser.add_argument("--blacklist", action="store_true", help="Flag the requester as blacklisted.")
    parser.add_argument("--force-safe-mode", action="store_true", help="Force the system into SAFE_MODE_ACTIVE before processing.")
    parser.add_argument("--public-readonly", action="store_true", help="Flag this request as public/read-only.")
    parser.add_argument("--request-id", default="cli-request", help="Identifier for this request.")

    args = parser.parse_args()

    recovery = RecoveryFSM()
    if args.force_safe_mode:
        recovery.transition({"system_healthy": False})
        recovery.transition({"critical_failure": True})

    logger = DecisionLogger()
    processor = SystemAwareRequestProcessor(recovery, logger)

    request_fsm = GovernanceFSM(request_id=args.request_id)
    context = build_context(args)

    print(f"System state: {recovery.state.value}")
    print(f"Request context: {context}")
    print()

    try:
        final_state = processor.process_until_stuck(request_fsm, context)
        print(f"Final state: {final_state.value}")
    except AccessBlockedBySafeModeError as e:
        print(f"BLOCKED: {e}")

    print()
    logger.print_all()


if __name__ == "__main__":
    main()