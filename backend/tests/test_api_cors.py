# backend/tests/test_api_cors.py

"""
Day 101: CORS, so a browser page on the Next.js dev server can call the API.

Preflight requests (OPTIONS) are what the browser sends before a call with
an Authorization header; the API must answer them for listed origins only.
"""

import pytest

from api.app import create_app
from core.config import Settings
from fastapi.testclient import TestClient
from tests.api_harness import ApiHarness

ALLOWED = "http://localhost:3000"
OTHER = "http://evil.example.com"
PREFLIGHT = {
    "Access-Control-Request-Method": "POST",
    "Access-Control-Request-Headers": "authorization,content-type",
}


def client_for(cors_origins):
    harness = ApiHarness()
    app = create_app(pipeline=harness.pipeline, auth_service=harness.auth, cors_origins=cors_origins)
    return TestClient(app)


def preflight(client, origin):
    return client.options("/requests", headers={"Origin": origin, **PREFLIGHT})


class TestCors:

    def test_no_cors_headers_when_no_origins_are_configured(self):
        response = preflight(client_for(None), ALLOWED)

        assert "access-control-allow-origin" not in response.headers

    def test_listed_origin_passes_preflight_with_the_headers_the_api_reads(self):
        response = preflight(client_for([ALLOWED]), ALLOWED)

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == ALLOWED
        allowed_headers = response.headers["access-control-allow-headers"].lower()
        assert "authorization" in allowed_headers and "content-type" in allowed_headers
        allowed_methods = response.headers["access-control-allow-methods"]
        assert "POST" in allowed_methods and "DELETE" in allowed_methods

    def test_unlisted_origin_is_refused_at_preflight(self):
        response = preflight(client_for([ALLOWED]), OTHER)

        assert response.status_code == 400
        assert "access-control-allow-origin" not in response.headers

    def test_a_normal_response_carries_the_header_for_a_listed_origin(self):
        response = client_for([ALLOWED]).get("/healthz", headers={"Origin": ALLOWED})

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == ALLOWED

    def test_credentials_are_not_allowed(self):
        response = preflight(client_for([ALLOWED]), ALLOWED)

        assert "access-control-allow-credentials" not in response.headers

    def test_wildcard_origin_is_refused_at_startup(self):
        with pytest.raises(ValueError):
            client_for(["*"])

    def test_default_setting_allows_only_the_local_next_dev_server(self):
        assert Settings(_env_file=None).cors_allowed_origins == ["http://localhost:3000"]