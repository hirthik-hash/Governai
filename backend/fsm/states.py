from enum import Enum


class FSMState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    ERROR = "error"
