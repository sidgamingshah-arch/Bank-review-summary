"""Opik prompt store — the system-of-record for SECTION-PROMPT content.

Per the design decision, section prompts (the prompt masters) live in Opik. The
master-config service keeps governance (maker-checker, version numbers, status,
audit) and a snapshot copy of the content, so the platform stays reproducible and
runnable offline; Opik holds the authoritative, versioned prompt text.

Backends:
  * ``opik``  — a real Opik deployment (self-hosted or Comet cloud), via the
    ``opik`` SDK (an optional dependency), used when ``CAM_OPIK_ENABLED`` is set
    and the SDK is importable.
  * ``local`` — an offline stand-in (no external dependency): :func:`publish`
    returns a deterministic content hash as the "commit" and :func:`fetch` returns
    ``None`` so callers fall back to the master-config snapshot. Keeps dev, tests
    and the demo fully offline.

Every call is **fail-open**: an Opik outage or a missing SDK never blocks a
publish or a generation run — the platform degrades to the snapshot copy.
"""
from __future__ import annotations

import hashlib
import logging
import os

from cam.common.config import get_settings

log = logging.getLogger("cam.masters.opik")
settings = get_settings("master-config")

_client = None  # cached opik.Opik instance


def enabled() -> bool:
    return bool(getattr(settings, "opik_enabled", False))


def backend() -> str:
    return "opik" if enabled() else "local"


def _name(key: str) -> str:
    prefix = getattr(settings, "opik_prompt_prefix", "") or ""
    return f"{prefix}{key}" if prefix else key


def _opik():
    """Lazily construct (and cache) the Opik client. Imported lazily so the SDK is
    a truly optional dependency — master-config runs without it installed."""
    global _client
    if _client is None:
        import opik  # optional dependency; only imported when the backend is used
        api_key = os.environ.get(getattr(settings, "opik_api_key_env", "OPIK_API_KEY"), "") or None
        _client = opik.Opik(
            project_name=getattr(settings, "opik_project", "cam-prompts") or None,
            workspace=getattr(settings, "opik_workspace", "") or None,
            host=getattr(settings, "opik_url", "") or None,
            api_key=api_key,
        )
    return _client


def _local_ref(text: str) -> dict:
    commit = "local-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return {"backend": "local", "name": None, "commit": commit}


def publish(key: str, text: str, version_no: int) -> dict:
    """Store the published section-prompt content and return a reference
    ``{backend, name, commit}`` to stamp on the master version. Fail-open: on any
    error (or when disabled), returns a deterministic local reference so the
    publish always succeeds and the snapshot remains the fallback."""
    name = _name(key)
    if enabled():
        try:
            prompt = _opik().create_prompt(
                name=name, prompt=text,
                metadata={"source": "cam-master-config", "master_version": version_no},
                change_description=f"master-config v{version_no}")
            commit = getattr(prompt, "commit", None) or getattr(prompt, "version", None)
            log.info("published prompt %s to Opik (commit=%s)", name, commit)
            return {"backend": "opik", "name": name, "commit": commit}
        except Exception:  # pragma: no cover - network/SDK failures are fail-open
            log.exception("Opik publish failed for prompt %s; storing a local ref", key)
    return _local_ref(text)


def fetch(key: str, ref: dict | None) -> str | None:
    """Return the authoritative content from Opik for a version reference, or
    ``None`` to signal the caller to use the master-config snapshot (local ref,
    backend disabled, or Opik unreachable)."""
    if not ref or ref.get("backend") != "opik" or not enabled():
        return None
    try:
        prompt = _opik().get_prompt(name=ref.get("name") or _name(key), commit=ref.get("commit"))
        return getattr(prompt, "prompt", None) if prompt is not None else None
    except Exception:  # pragma: no cover - fail-open to the snapshot
        log.warning("Opik fetch failed for prompt %s; using the master-config snapshot", key)
        return None


def status() -> dict:
    """Admin-visible health of the prompt store."""
    st = {"enabled": enabled(), "backend": backend(),
          "url": getattr(settings, "opik_url", "") or None,
          "project": getattr(settings, "opik_project", None) or None}
    if enabled():
        try:
            import opik  # noqa: F401
            st["sdk_installed"] = True
        except Exception:  # pragma: no cover
            st["sdk_installed"] = False
        try:
            _opik()  # constructing the client validates the configuration
            st["reachable"] = True
        except Exception as exc:  # pragma: no cover
            st["reachable"] = False
            st["error"] = type(exc).__name__
    return st
