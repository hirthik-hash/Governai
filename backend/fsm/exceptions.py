# backend/fsm/exceptions.py

class InvalidTransitionError(Exception):
    """Raised when no transition rule matches the current state + context."""
    pass


class AmbiguousTransitionError(Exception):
    """Raised when more than one transition rule matches - a rulebook bug."""
    pass


class TerminalStateError(Exception):
    """Raised when attempting to transition an FSM already in a terminal state."""
    pass