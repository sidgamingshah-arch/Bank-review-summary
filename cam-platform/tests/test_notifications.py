"""In-app notifications: a run's creator is notified when generation finishes,
notifications are strictly user-scoped, and read / read-all endpoints work."""
from __future__ import annotations

from fastapi.testclient import TestClient

import cam.services.orchestration.main as orch
from cam.services.orchestration import worker
from tests.conftest import make_user_headers

# registers the shared orchestration fakes as fixtures
from tests.test_orchestration import _create_run, wired  # noqa: F401


def test_run_completion_notifies_creator(wired, analyst_headers):
    with TestClient(orch.app) as c:
        run = _create_run(c, analyst_headers).json()
        worker.drain()
        res = c.get("/api/notifications", headers=analyst_headers).json()
        assert res["unread"] >= 1
        n = next(n for n in res["notifications"] if n["run_id"] == run["id"])
        assert n["kind"] == "run_complete" and n["read"] is False
        assert n["cam_id"] and "ready" in n["title"].lower()

        # marking it read drops it from the unread view and the unread count
        assert c.post(f"/api/notifications/{n['id']}/read",
                      headers=analyst_headers).status_code == 200
        after = c.get("/api/notifications?unread_only=true", headers=analyst_headers).json()
        assert all(x["id"] != n["id"] for x in after["notifications"])


def test_notifications_are_user_scoped(wired, analyst_headers):
    with TestClient(orch.app) as c:
        _create_run(c, analyst_headers)
        worker.drain()
        other = make_user_headers("analyst2", ["analyst"])
        res = c.get("/api/notifications", headers=other).json()
        assert res["unread"] == 0 and res["notifications"] == []


def test_mark_all_read(wired, analyst_headers):
    with TestClient(orch.app) as c:
        _create_run(c, analyst_headers)
        _create_run(c, analyst_headers)
        worker.drain()
        r = c.post("/api/notifications/read-all", headers=analyst_headers).json()
        assert r["marked"] >= 1
        assert c.get("/api/notifications", headers=analyst_headers).json()["unread"] == 0


def test_cannot_mark_others_notification_read(wired, analyst_headers):
    with TestClient(orch.app) as c:
        _create_run(c, analyst_headers)
        worker.drain()
        mine = c.get("/api/notifications", headers=analyst_headers).json()["notifications"]
        assert mine, "creator should have at least one notification"
        other = make_user_headers("analyst2", ["analyst"])
        r = c.post(f"/api/notifications/{mine[0]['id']}/read", headers=other)
        assert r.status_code == 404  # not visible to another user
