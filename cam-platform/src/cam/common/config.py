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
    llm_provider: str = "mock"  # mock | anthropic | openai
    genai_model: str = "claude-opus-4-8"
    genai_max_tokens: int = 2000
    # User-supplied / OpenAI-compatible endpoint (llm_provider="openai"): vLLM,
    # LiteLLM, Azure OpenAI, Ollama, a bank-hosted gateway, etc. The base URL
    # should include the version path prefix (e.g. https://llm.internal/v1);
    # "/chat/completions" is appended. The API key itself is NEVER stored on
    # Settings — only the NAME of the env var that holds it; the value is read
    # from os.environ at provider construction and never logged (NFR-06).
    genai_base_url: str = ""
    genai_api_key_env: str = "CAM_GENAI_API_KEY"
    genai_auth_scheme: str = "Bearer"  # Authorization: "<scheme> <key>"; "" -> raw key
    genai_temperature: float = 0.0
    genai_timeout_seconds: float = 120.0

    # Embedding egress (for large-document retrieval / RAG). Kept independent of
    # the chat provider because Anthropic has no embeddings API — so chat can run
    # on anthropic while embeddings run on an OpenAI-compatible endpoint.
    #   mock   -> deterministic offline hashing embedder (dev/tests/demo)
    #   openai -> POST <base_url>/embeddings (OpenAI-compatible: OpenAI, Azure,
    #             vLLM, LiteLLM, a bank-hosted embedder, ...)
    # Base URL falls back to genai_base_url when empty; the API key is read from
    # the named env var at construction and never stored/logged (NFR-06).
    genai_embed_provider: str = "mock"  # mock | openai
    genai_embed_model: str = ""  # e.g. text-embedding-3-small (required for openai)
    genai_embed_base_url: str = ""  # empty -> reuse genai_base_url
    genai_embed_api_key_env: str = "CAM_GENAI_API_KEY"  # env var NAME only, never the value
    genai_embed_dim: int = 256  # mock vector dimension (live provider reports its own)

    # Large-document retrieval (RAG). Off by default so runs behave exactly as
    # before until an admin enables it. When on, document text is chunked and
    # embedded at intake and each section is grounded on the top-K most relevant
    # passages rather than the first MAX_DOC_CHARS of full text.
    rag_enabled: bool = False
    rag_top_k: int = 6
    rag_chunk_size: int = 1200  # characters per chunk
    rag_chunk_overlap: int = 200  # character overlap between adjacent chunks
    rag_max_chunks: int = 4000  # per-document safety cap on chunk count

    # External grounding connectors (client-provided, integrated). The endpoint
    # URL is deployment config (here); the on/off toggle is a master setting
    # (business-admin controlled). Empty URL + toggle on -> deterministic mock.
    connector_news_url: str = ""
    connector_search_url: str = ""
    connector_api_key_env: str = "CAM_CONNECTOR_API_KEY"
    connector_timeout_seconds: float = 8.0
    connector_max_items: int = 5

    # intake / generation guardrails
    max_upload_mb: int = 25
    # Extracted-text cap (on-disk). Large enough for a 300+ page annual report
    # (~900k chars) so retrieval can reach content deep in the document; the raw
    # binary is still bounded by max_upload_mb.
    max_extract_chars: int = 2_000_000
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
