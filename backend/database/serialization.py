# backend/database/serialization.py

"""
JSON-safe rendering of arbitrary Python values (Day 73).

The decision log stores each FSM context snapshot in a JSON column, but
real snapshots are not plain JSON: the escalation flow puts a
NotificationRecord dataclass into the context. to_json_safe() converts
whatever it is given into something json.dumps() accepts.

It NEVER raises. A value it does not recognize is stored as its repr()
rather than failing - a logging problem must not be able to change or
block an access decision once persistence is wired into the pipeline.
The conversion is one-way and lossy by design: a dataclass comes back
as a dict, not as the dataclass.
"""

import dataclasses
from datetime import date, datetime
from enum import Enum
from typing import Any


def to_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return to_json_safe(value.value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_json_safe(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_safe(v) for v in value]
    if isinstance(value, (set, frozenset)):
        # Sets have no order; sort so the stored form is deterministic.
        return sorted((to_json_safe(v) for v in value), key=repr)
    return repr(value)
