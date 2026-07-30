"""Runtime-tunable generation concurrency: the worker's active-concurrency gate
reads the live 'worker_concurrency' master setting, clamps it to the pool ceiling,
and fails open to the environment default when master-config is unreachable."""
from __future__ import annotations

from types import SimpleNamespace

from cam.services.orchestration import worker


def _reset_cache(monkeypatch):
    # disable the short TTL so every call re-reads (no stale value between asserts)
    monkeypatch.setattr(worker, "_ACTIVE_TTL_SECONDS", -1.0)
    worker._active_cache["value"] = None
    worker._active_cache["at"] = 0.0


def test_active_concurrency_reads_setting(monkeypatch):
    _reset_cache(monkeypatch)
    monkeypatch.setattr(worker, "settings", SimpleNamespace(worker_concurrency=2))
    monkeypatch.setattr(worker, "_pool_size", 8)
    monkeypatch.setattr(worker.resolver, "fetch_settings", lambda: {"worker_concurrency": 5})
    assert worker._active_concurrency() == 5


def test_active_concurrency_clamped_to_pool_ceiling(monkeypatch):
    _reset_cache(monkeypatch)
    monkeypatch.setattr(worker, "settings", SimpleNamespace(worker_concurrency=2))
    monkeypatch.setattr(worker, "_pool_size", 4)
    monkeypatch.setattr(worker.resolver, "fetch_settings", lambda: {"worker_concurrency": 100})
    assert worker._active_concurrency() == 4  # never exceeds the spawned pool


def test_active_concurrency_floor_is_one(monkeypatch):
    _reset_cache(monkeypatch)
    monkeypatch.setattr(worker, "settings", SimpleNamespace(worker_concurrency=2))
    monkeypatch.setattr(worker, "_pool_size", 8)
    monkeypatch.setattr(worker.resolver, "fetch_settings", lambda: {"worker_concurrency": 0})
    assert worker._active_concurrency() == 1  # at least one worker always runs


def test_active_concurrency_defaults_when_setting_absent(monkeypatch):
    _reset_cache(monkeypatch)
    monkeypatch.setattr(worker, "settings", SimpleNamespace(worker_concurrency=3))
    monkeypatch.setattr(worker, "_pool_size", 8)
    monkeypatch.setattr(worker.resolver, "fetch_settings", lambda: {})
    assert worker._active_concurrency() == 3  # env default


def test_active_concurrency_failopen(monkeypatch):
    _reset_cache(monkeypatch)
    monkeypatch.setattr(worker, "settings", SimpleNamespace(worker_concurrency=2))
    monkeypatch.setattr(worker, "_pool_size", 8)

    def boom():
        raise RuntimeError("master-config down")

    monkeypatch.setattr(worker.resolver, "fetch_settings", boom)
    # fetch_settings itself is fail-open in resolver, but guard here too
    assert worker._active_concurrency() == 2


def test_active_concurrency_caches_within_ttl(monkeypatch):
    # with the real (positive) TTL, a second call must not re-read the setting
    monkeypatch.setattr(worker, "_ACTIVE_TTL_SECONDS", 5.0)
    worker._active_cache["value"] = None
    worker._active_cache["at"] = 0.0
    monkeypatch.setattr(worker, "settings", SimpleNamespace(worker_concurrency=2))
    monkeypatch.setattr(worker, "_pool_size", 8)
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return {"worker_concurrency": 6}

    monkeypatch.setattr(worker.resolver, "fetch_settings", counting)
    assert worker._active_concurrency() == 6
    assert worker._active_concurrency() == 6
    assert calls["n"] == 1  # second call served from cache
