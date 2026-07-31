"""The gateway's optional SPA-serving block: serves the built UI at one origin,
does not shadow the /api proxy, and is guarded against path traversal."""
from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

import cam.gateway.main as gw


def test_spa_serving_and_traversal_guard(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.ico").write_text("ICON", encoding="utf-8")

    monkeypatch.setenv("CAM_FRONTEND_DIST", str(dist))
    reloaded = importlib.reload(gw)
    try:
        with TestClient(reloaded.app) as c:
            # gateway's own routes still win
            assert c.get("/healthz").json()["service"] == "gateway"
            # index for the root and for any client-side route
            assert "<div id=root>" in c.get("/").text
            assert "<div id=root>" in c.get("/cases/123").text
            # a real static file is served as-is
            assert c.get("/favicon.ico").text == "ICON"
            # /api is proxied (auth guard fires) — NOT served the SPA index
            r_api = c.get("/api/runs")
            assert r_api.status_code == 401
            assert "<div id=root>" not in r_api.text
            # traversal cannot escape dist — falls back to index, never leaks a file
            leak = c.get("/../../../../etc/passwd")
            assert "root:" not in leak.text and "<div id=root>" in leak.text
    finally:
        monkeypatch.delenv("CAM_FRONTEND_DIST", raising=False)
        importlib.reload(gw)  # restore the module to its default state
