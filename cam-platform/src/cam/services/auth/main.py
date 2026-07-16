"""auth-adapter — dev stand-in for bank IdP SSO (prod: OIDC/SAML, swap this
service only; NFR-05). Owns users/roles (IT-admin managed) and user output
preference profiles (FR-B01/B05).
"""
from __future__ import annotations

import hashlib
import os
import secrets
from typing import Literal

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import select

from cam.common import audit
from cam.common.app_factory import create_app
from cam.common.config import get_settings
from cam.common.db import make_engine, make_session_factory
from cam.common.errors import ApiError
from cam.common.security import Principal, create_user_token, make_auth_dependencies

from .models import Base, PreferenceProfile, User

settings = get_settings("auth")
engine = make_engine(settings.resolved_db_url())
SessionLocal = make_session_factory(engine)
current_principal, require, require_service = make_auth_dependencies(settings)

app = create_app(settings, "CAM auth-adapter")

DEMO_PASSWORD = os.environ.get("CAM_DEMO_PASSWORD", "Demo#2026")
SEED_USERS = [
    ("admin1", "Asha Iyer", ["business_admin"]),
    ("admin2", "Rohit Menon", ["business_admin"]),
    ("itadmin", "Priya Nair", ["it_admin"]),
    ("analyst1", "Kunal Verma", ["analyst"]),
    ("analyst2", "Sneha Rao", ["analyst"]),
    ("reviewer1", "Vikram Joshi", ["reviewer"]),
    ("auditor1", "Meera Krishnan", ["auditor"]),
]


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return secrets.compare_digest(hash_password(password, salt), stored)


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if not db.scalar(select(User).limit(1)):
            for username, display, roles in SEED_USERS:
                db.add(User(username=username, display_name=display,
                            email=f"{username}@bank.example", roles=roles,
                            password_hash=hash_password(DEMO_PASSWORD)))
            db.add(PreferenceProfile(user_id=None, updated_by="system"))  # org default
            db.commit()


class LoginRequest(BaseModel):
    username: str
    password: str


class PreferenceInput(BaseModel):
    tonality: Literal["crisp", "narrative"]
    structure_bias: Literal["bullets", "paragraphs"]
    table_usage: Literal["auto", "prefer", "avoid"]
    length: Literal["concise", "standard", "detailed"]


class UserCreate(BaseModel):
    username: str
    display_name: str = ""
    email: str = ""
    roles: list[str]
    password: str


class UserPatch(BaseModel):
    roles: list[str] | None = None
    active: bool | None = None


@app.post("/api/auth/token")
def login(body: LoginRequest):
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == body.username))
        if not user or not user.active or not verify_password(body.password, user.password_hash):
            raise ApiError.unauthorized("invalid credentials")
        token, ttl = create_user_token(settings, sub=user.id, username=user.username,
                                       display_name=user.display_name, roles=list(user.roles))
        audit.emit(settings, action="user.login", entity_type="user", entity_id=user.id,
                   principal=Principal(sub=user.id, username=user.username, roles=list(user.roles)))
        return {"access_token": token, "token_type": "bearer", "expires_in": ttl,
                "user": user.to_dict()}


@app.get("/api/auth/me")
def me(principal: Principal = Depends(current_principal)):
    with SessionLocal() as db:
        user = db.get(User, principal.sub)
        if not user:
            raise ApiError.not_found("user")
        return user.to_dict()


@app.get("/api/auth/users")
def list_users(principal: Principal = Depends(require("users:admin"))):
    with SessionLocal() as db:
        return [u.to_dict() for u in db.scalars(select(User).order_by(User.username)).all()]


@app.post("/api/auth/users", status_code=201)
def create_user(body: UserCreate, principal: Principal = Depends(require("users:admin"))):
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.username == body.username)):
            raise ApiError.conflict(f"username '{body.username}' already exists")
        unknown = set(body.roles) - {"business_admin", "it_admin", "analyst", "reviewer", "auditor"}
        if unknown:
            raise ApiError.validation(f"unknown roles: {sorted(unknown)}")
        user = User(username=body.username, display_name=body.display_name, email=body.email,
                    roles=body.roles, password_hash=hash_password(body.password))
        db.add(user)
        db.commit()
        audit.emit(settings, action="user.created", entity_type="user", entity_id=user.id,
                   principal=principal, detail={"username": user.username, "roles": body.roles})
        return user.to_dict()


@app.patch("/api/auth/users/{user_id}")
def patch_user(user_id: str, body: UserPatch,
               principal: Principal = Depends(require("users:admin"))):
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if not user:
            raise ApiError.not_found("user")
        before = {"roles": list(user.roles), "active": user.active}
        if body.roles is not None:
            user.roles = body.roles
        if body.active is not None:
            user.active = body.active
        db.commit()
        audit.emit(settings, action="user.updated", entity_type="user", entity_id=user.id,
                   principal=principal,
                   detail={"before": before, "after": {"roles": list(user.roles), "active": user.active}})
        return user.to_dict()


def _org_default(db) -> PreferenceProfile:
    profile = db.scalar(select(PreferenceProfile).where(PreferenceProfile.user_id.is_(None)))
    if not profile:
        profile = PreferenceProfile(user_id=None, updated_by="system")
        db.add(profile)
        db.commit()
    return profile


@app.get("/api/auth/preferences")
def get_preferences(principal: Principal = Depends(require("prefs:own"))):
    with SessionLocal() as db:
        own = db.scalar(select(PreferenceProfile).where(PreferenceProfile.user_id == principal.sub))
        return (own or _org_default(db)).to_dict()


@app.put("/api/auth/preferences")
def put_preferences(body: PreferenceInput, principal: Principal = Depends(require("prefs:own"))):
    with SessionLocal() as db:
        own = db.scalar(select(PreferenceProfile).where(PreferenceProfile.user_id == principal.sub))
        if not own:
            own = PreferenceProfile(user_id=principal.sub)
            db.add(own)
        own.tonality, own.structure_bias = body.tonality, body.structure_bias
        own.table_usage, own.length = body.table_usage, body.length
        own.updated_by = principal.username
        db.commit()
        audit.emit(settings, action="prefs.updated", entity_type="preference_profile",
                   entity_id=own.id, principal=principal, detail=body.model_dump())
        return own.to_dict()


@app.get("/api/auth/preferences/org-default")
def get_org_default(principal: Principal = Depends(current_principal)):
    with SessionLocal() as db:
        return _org_default(db).to_dict()


@app.put("/api/auth/preferences/org-default")
def put_org_default(body: PreferenceInput,
                    principal: Principal = Depends(require("org_defaults:set"))):
    with SessionLocal() as db:
        profile = _org_default(db)
        profile.tonality, profile.structure_bias = body.tonality, body.structure_bias
        profile.table_usage, profile.length = body.table_usage, body.length
        profile.updated_by = principal.username
        db.commit()
        audit.emit(settings, action="prefs.updated", entity_type="preference_profile",
                   entity_id=profile.id, principal=principal,
                   detail={"scope": "org_default", **body.model_dump()})
        return profile.to_dict()
