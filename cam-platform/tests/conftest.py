"""Shared pytest fixtures.

Environment is pinned BEFORE any cam import: every service resolves its SQLite
DB and blob dirs under a session-scoped temp directory, and audit emission is
captured in-memory instead of hitting the network.
"""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="cam-tests-")
os.environ["CAM_DATA_DIR"] = _TMP
os.environ.setdefault("CAM_JWT_SECRET", "test-secret")
# unit tests drive the orchestration worker synchronously (worker.drain())
os.environ.setdefault("CAM_WORKER_ENABLED", "false")

import pytest  # noqa: E402

from cam.common import audit as audit_client  # noqa: E402
from cam.common.config import get_settings  # noqa: E402
from cam.common.security import create_service_token, create_user_token  # noqa: E402


@pytest.fixture(autouse=True)
def captured_audit(monkeypatch):
    """No network in unit tests: capture audit events in a list."""
    events: list[dict] = []

    def fake_emit(settings, *, action, entity_type, entity_id, principal=None,
                  case_id=None, run_id=None, cam_id=None, detail=None):
        events.append({"action": action, "entity_type": entity_type, "entity_id": entity_id,
                       "case_id": case_id, "run_id": run_id, "cam_id": cam_id,
                       "actor": principal.username if principal else "svc",
                       "detail": detail or {}})

    monkeypatch.setattr(audit_client, "emit", fake_emit)
    # services import `from cam.common import audit` and call audit.emit -> patched above
    yield events


def make_user_headers(username: str, roles: list[str], sub: str | None = None) -> dict:
    settings = get_settings("tests")
    token, _ = create_user_token(settings, sub=sub or f"uid-{username}", username=username,
                                 display_name=username, roles=roles)
    return {"Authorization": f"Bearer {token}"}


def make_service_headers(svc: str = "tests") -> dict:
    settings = get_settings("tests")
    return {"Authorization": f"Bearer {create_service_token(settings, svc)}"}


@pytest.fixture
def analyst_headers():
    return make_user_headers("analyst1", ["analyst"])


@pytest.fixture
def admin_headers():
    return make_user_headers("admin1", ["business_admin"])


@pytest.fixture
def admin2_headers():
    return make_user_headers("admin2", ["business_admin"])


@pytest.fixture
def reviewer_headers():
    return make_user_headers("reviewer1", ["reviewer"])


@pytest.fixture
def auditor_headers():
    return make_user_headers("auditor1", ["auditor"])


@pytest.fixture
def service_headers():
    return make_service_headers()
