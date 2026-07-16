"""JWT auth + RBAC dependencies.

Dev stand-in for the bank IdP: HS256 with a shared secret (vault-provided in
production). Tokens are short-lived (NFR-06). ``typ`` distinguishes user tokens
(issued by the auth-adapter after login/SSO) from service tokens (minted by
services for gateway-mediated service-to-service calls).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Request

from .config import Settings
from .errors import ApiError
from .rbac import has_capability

ALGORITHM = "HS256"


@dataclass
class Principal:
    sub: str
    username: str
    display_name: str = ""
    roles: list[str] = field(default_factory=list)
    typ: str = "user"  # user | service
    svc: str = ""

    @property
    def is_service(self) -> bool:
        return self.typ == "service"

    def can(self, capability: str) -> bool:
        return has_capability(self.roles, capability)


def create_user_token(settings: Settings, *, sub: str, username: str, display_name: str,
                      roles: list[str]) -> tuple[str, int]:
    ttl = settings.jwt_ttl_minutes * 60
    now = datetime.now(timezone.utc)
    claims = {
        "sub": sub, "username": username, "display_name": display_name,
        "roles": roles, "typ": "user",
        "iat": now, "exp": now + timedelta(seconds=ttl),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM), ttl


def create_service_token(settings: Settings, svc: str | None = None) -> str:
    now = datetime.now(timezone.utc)
    name = svc or settings.service_name
    claims = {
        "sub": f"svc:{name}", "username": f"svc:{name}", "display_name": name,
        "roles": ["service"], "typ": "service", "svc": name,
        "iat": now, "exp": now + timedelta(minutes=10),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(settings: Settings, token: str) -> Principal:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise ApiError.unauthorized("token expired")
    except jwt.PyJWTError:
        raise ApiError.unauthorized("invalid token")
    return Principal(
        sub=claims.get("sub", ""), username=claims.get("username", ""),
        display_name=claims.get("display_name", ""), roles=list(claims.get("roles", [])),
        typ=claims.get("typ", "user"), svc=claims.get("svc", ""),
    )


def bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise ApiError.unauthorized()
    return auth[len("Bearer "):]


def make_auth_dependencies(settings: Settings):
    """Per-service factory so each service binds its own Settings instance."""

    def current_principal(request: Request) -> Principal:
        return decode_token(settings, bearer_token(request))

    def require(*capabilities: str):
        def dep(principal: Principal = Depends(current_principal)) -> Principal:
            for cap in capabilities:
                if not principal.can(cap):
                    raise ApiError.forbidden(f"requires capability '{cap}'")
            return principal
        return dep

    def require_service(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.is_service:
            raise ApiError.forbidden("service-to-service endpoint (NFR-10)")
        return principal

    return current_principal, require, require_service
