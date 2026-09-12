# backend/core/geo_anomaly_detector.py

"""
Tracks per-user location history to detect geographic anomalies -
the last of SecurityRiskAgent's 6 risk factors (Day 38).

A request is only flagged anomalous if the user has an established
history AND the new location isn't part of it. A user's first-ever
recorded request is never anomalous - there is no baseline yet, and
flagging "no data" as suspicious would be a false-positive trap, not
a real detection.

In-memory only, same as RequestHistoryTracker (Day 37) - Phase 3
will back this with real persistence.

Consumed by agents.security_agent.SecurityRiskAgent as an injected
dependency (geographic_anomaly factor).
"""


class GeoAnomalyDetector:
    """
    Per-user set of previously-seen locations (e.g. country codes).
    Call record_location() after every request (regardless of outcome)
    so future requests have a baseline to compare against.
    """

    def __init__(self):
        self._known_locations: dict[str, set[str]] = {}

    def record_location(self, user_id: str, location: str) -> None:
        self._known_locations.setdefault(user_id, set()).add(location)

    def is_anomalous(self, user_id: str, location: str) -> bool:
        known = self._known_locations.get(user_id)

        if not known:
            # No history yet - nothing to compare against, so this
            # first request establishes the baseline rather than
            # being flagged.
            return False

        return location not in known

    def clear(self, user_id: str) -> None:
        self._known_locations.pop(user_id, None)