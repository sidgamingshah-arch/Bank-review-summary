"""The service-only contact-lookup endpoint used by orchestration to email a
run's creator: shape, service-only authZ, and unknown-user handling."""
from __future__ import annotations

from fastapi.testclient import TestClient

import cam.services.auth.main as auth
from tests.conftest import make_service_headers, make_user_headers


def test_contact_lookup_returns_shape_for_service_token():
    with TestClient(auth.app) as c:
        r = c.get("/api/auth/users/by-username/analyst1/contact",
                  headers=make_service_headers())
        assert r.status_code == 200
        body = r.json()
        assert body["username"] == "analyst1"
        assert body["email"] == "analyst1@bank.example"
        assert "display_name" in body and body["active"] is True


def test_contact_lookup_is_service_only():
    with TestClient(auth.app) as c:
        r = c.get("/api/auth/users/by-username/analyst1/contact",
                  headers=make_user_headers("analyst1", ["analyst"]))
        assert r.status_code == 403  # NFR-10: not exposed to end users


def test_contact_lookup_unknown_user_404():
    with TestClient(auth.app) as c:
        r = c.get("/api/auth/users/by-username/nobody/contact",
                  headers=make_service_headers())
        assert r.status_code == 404
