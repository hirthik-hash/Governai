# backend/tests/test_database_serialization.py

"""Day 73: to_json_safe() - JSON rendering of arbitrary snapshot values."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from agents.escalation_agent import NotificationRecord
from database.serialization import to_json_safe
from data.seed_data import Sensitivity


@dataclass
class _Inner:
    name: str
    level: int


@dataclass
class _Outer:
    inner: _Inner
    tags: list


class _Unknown:
    def __repr__(self):
        return "<Unknown thing>"


class TestPassThrough:

    def test_plain_json_values_are_returned_unchanged(self):
        value = {"a": 1, "b": [1.5, True, None, "x"], "c": {"d": "e"}}

        assert to_json_safe(value) == value


class TestConversions:

    def test_dataclass_becomes_a_dict_recursively(self):
        assert to_json_safe(_Outer(inner=_Inner("n", 2), tags=["a"])) == {
            "inner": {"name": "n", "level": 2},
            "tags": ["a"],
        }

    def test_enum_becomes_its_value(self):
        assert to_json_safe(Sensitivity.TOP_SECRET) == "top_secret"

    def test_datetime_becomes_iso_string(self):
        moment = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)

        assert to_json_safe(moment) == "2026-09-21T10:00:00+00:00"

    def test_tuple_becomes_list(self):
        assert to_json_safe((1, (2, 3))) == [1, [2, 3]]

    def test_set_becomes_a_sorted_list(self):
        assert to_json_safe({"b", "a", "c"}) == ["a", "b", "c"]

    def test_non_string_dict_keys_become_strings(self):
        assert to_json_safe({1: "a", 2: {3: "b"}}) == {"1": "a", "2": {"3": "b"}}

    def test_unknown_object_becomes_its_repr_and_never_raises(self):
        assert to_json_safe({"x": _Unknown()}) == {"x": "<Unknown thing>"}

    def test_a_dataclass_class_object_is_not_mistaken_for_an_instance(self):
        assert to_json_safe(_Inner) == repr(_Inner)


class TestRealSnapshotValues:

    def test_a_real_notification_record_becomes_json_serializable(self):
        # The exact object that made raw context snapshots un-storable.
        notification = NotificationRecord(
            request_id="req-1", approver_user_id="user-004", approver_name="Sofia Ricci",
            requester_user_id="user-003", requester_name="Test User",
            resource_summary="resource-003 (restricted)",
            sent_at="2026-09-21T10:00:00+00:00", timeout_seconds=3600,
        )

        rendered = to_json_safe({"notification": notification, "risk_score": 45})

        assert json.loads(json.dumps(rendered)) == rendered
        assert rendered["notification"]["approver_user_id"] == "user-004"
        assert rendered["notification"]["timeout_seconds"] == 3600
