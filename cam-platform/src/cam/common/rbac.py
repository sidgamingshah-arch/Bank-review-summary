"""Role → capability matrix, transcribed from BRD §4 (see docs/contracts.md).

This is deliberately data, not code: auditors and MRM can review the exact
authorisation model, and services all enforce the same matrix.
"""
from __future__ import annotations

ROLE_CAPABILITIES: dict[str, set[str]] = {
    "business_admin": {
        "masters:read", "masters:draft", "masters:submit", "masters:approve",
        "masters:settings", "org_defaults:set", "audit:read", "prefs:own",
    },
    "it_admin": {"users:admin", "env:config", "prefs:own"},
    "analyst": {
        "masters:read", "case:create", "case:read", "docs:manage",
        "generate:run", "cam:edit", "cam:converse", "cam:finalise",
        "cam:download", "audit:read", "prefs:own",
    },
    "reviewer": {
        "masters:read", "case:read", "generate:run", "cam:edit",
        "cam:converse", "cam:finalise", "cam:download", "audit:read", "prefs:own",
    },
    "auditor": {"masters:read", "case:read", "audit:read", "audit:export", "prefs:own"},
    # Internal service-to-service identity (typ=service tokens only).
    "service": {"masters:read", "case:read", "internal:write", "genai:call", "audit:read"},
}

# Roles whose case/audit visibility is restricted to their own records.
OWN_SCOPED_ROLES = {"analyst"}


def capabilities_for(roles: list[str]) -> set[str]:
    caps: set[str] = set()
    for r in roles:
        caps |= ROLE_CAPABILITIES.get(r, set())
    return caps


def has_capability(roles: list[str], capability: str) -> bool:
    return capability in capabilities_for(roles)


def is_own_scoped(roles: list[str]) -> bool:
    """True when the principal only sees records they own (analyst)."""
    return bool(set(roles) & OWN_SCOPED_ROLES) and not (set(roles) - OWN_SCOPED_ROLES)
