"""Runtime configuration + credentials, loaded from a single config file.

You put your API keys in one file (``backend/.env`` by default — copy it from
``.env.example``). On import we load that file into the process environment so
**every** provider SDK can see the keys (the Anthropic client, the AWS credential
chain for Bedrock, Google ADC for Vertex), and pydantic reads it too.

Resolution order for the config file (first that exists wins):
  1. ``$ASM_ENV_FILE`` (explicit path override)
  2. ``backend/.env``
  3. ``backend/secrets.env``
  4. ``<repo root>/.env``
Real environment variables always take precedence over the file.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[1]  # .../backend
_REPO_ROOT = _BACKEND_DIR.parent


def _resolve_env_file() -> str | None:
    candidates: list[str] = []
    override = os.getenv("ASM_ENV_FILE")
    if override:
        candidates.append(override)
    candidates += [
        str(_BACKEND_DIR / ".env"),
        str(_BACKEND_DIR / "secrets.env"),
        str(_REPO_ROOT / ".env"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


ENV_FILE = _resolve_env_file()


def _load_env_into_environment() -> None:
    """Load the config file into os.environ (existing real env vars win)."""
    if not ENV_FILE:
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(ENV_FILE, override=False)
    except Exception:
        # python-dotenv is optional at import time; pydantic still reads the file.
        pass


_load_env_into_environment()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        extra="ignore",
        case_sensitive=False,
        # `model` would otherwise clash with pydantic's protected "model_" namespace.
        protected_namespaces=(),
    )

    # --- Provider selection ---
    llm_provider: str = "anthropic"  # anthropic | bedrock | vertex
    model: str = "claude-opus-4-8"

    # --- Credentials / endpoints (set these in your config file) ---
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None
    aws_region: str | None = None
    vertex_project_id: str | None = None
    vertex_region: str = "global"

    # --- App behaviour ---
    placeholder_text: str = "[To be entered by L1]"
    max_pdf_mb: int = 28
    max_tokens: int = 16000
    data_dir: str = "./data"


def get_settings() -> Settings:
    return Settings()
