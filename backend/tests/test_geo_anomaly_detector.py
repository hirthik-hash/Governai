# backend/tests/test_geo_anomaly_detector.py

from core.geo_anomaly_detector import GeoAnomalyDetector


class TestGeoAnomalyDetector:

    def test_first_ever_request_is_never_anomalous(self):
        detector = GeoAnomalyDetector()
        assert detector.is_anomalous("user-001", "US") is False

    def test_same_location_as_history_not_anomalous(self):
        detector = GeoAnomalyDetector()
        detector.record_location("user-001", "US")

        assert detector.is_anomalous("user-001", "US") is False

    def test_new_location_after_established_history_is_anomalous(self):
        detector = GeoAnomalyDetector()
        detector.record_location("user-001", "US")

        assert detector.is_anomalous("user-001", "RU") is True

    def test_multiple_known_locations_all_count_as_normal(self):
        detector = GeoAnomalyDetector()
        detector.record_location("user-001", "US")
        detector.record_location("user-001", "IN")

        assert detector.is_anomalous("user-001", "US") is False
        assert detector.is_anomalous("user-001", "IN") is False
        assert detector.is_anomalous("user-001", "FR") is True

    def test_different_users_tracked_independently(self):
        detector = GeoAnomalyDetector()
        detector.record_location("user-001", "US")

        assert detector.is_anomalous("user-002", "US") is False  # no history for user-002

    def test_recording_a_new_location_expands_the_known_set(self):
        detector = GeoAnomalyDetector()
        detector.record_location("user-001", "US")
        assert detector.is_anomalous("user-001", "RU") is True

        detector.record_location("user-001", "RU")
        assert detector.is_anomalous("user-001", "RU") is False

    def test_clear_resets_a_users_history(self):
        detector = GeoAnomalyDetector()
        detector.record_location("user-001", "US")
        detector.clear("user-001")

        assert detector.is_anomalous("user-001", "US") is False  # back to no-baseline