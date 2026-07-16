"""Environment-driven settings shared by every service.

All values come from the environment (prefix ``CAM_``); each service passes its
own ``service_name`` so per-service defaults (DB path) derive automatically.
Secrets are env/vault-provided only — never hardcoded, never logged (NFR-06).
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-secret-do-not-use-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CAM_", extra="ignore")

    service_name: str = "cam"
    gateway_url: str = "http://localhost:8080"
    jwt_secret: str = DEV_JWT_SECRET
    jwt_ttl_minutes: int = 60
    data_dir: str = ".data"
    db_url: str = ""  # empty -> sqlite file under data_dir

    # genai
    llm_provider: str = "mock"  # mock | anthropic
    genai_model: str = "claude-opus-4-8"
    genai_max_tokens: int = 2000

    # intake / generation guardrails
    max_upload_mb: int = 25
    worker_concurrency: int = 2
    max_active_runs_per_user: int = 2

    def resolved_db_url(self) -> str:
        if self.db_url:
            return self.db_url
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.data_dir}/{self.service_name}.db"

    @property
    def blob_dir(self) -> Path:
        p = Path(self.data_dir) / "blobs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def extract_dir(self) -> Path:
        p = Path(self.data_dir) / "extracts"
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache(maxsize=None)
def get_settings(service_name: str) -> Settings:
    # Per-service DB override, e.g. CAM_DB_URL_MASTER_CONFIG
    override = os.environ.get(f"CAM_DB_URL_{service_name.upper().replace('-', '_')}", "")
    s = Settings(service_name=service_name)
    if override:
        s.db_url = override
    return s
