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
    genai_embed_provider: str = "mock"  # mock | openai | azure
    genai_embed_model: str = ""  # embedding model id; for azure, the embed DEPLOYMENT name
    genai_embed_base_url: str = ""  # empty -> reuse genai_base_url
    genai_embed_api_key_env: str = "CAM_GENAI_API_KEY"  # env var NAME only, never the value
    genai_embed_dim: int = 256  # mock vector dimension (live provider reports its own)

    # Large-document retrieval (RAG). Off by default so runs behave exactly as
    # before until an admin enables it. Retrieval mode is three-way:
    #   off       -> full document text (no retrieval)
    #   keyword   -> lexical/BM25 retrieval, NO embedding model needed
    #   embedding -> semantic (vector / hybrid) retrieval via the embedder
    # rag_enabled is kept for back-compat: True is read as "embedding" when
    # rag_mode is unset.
    rag_mode: str = "off"  # off | keyword | embedding
    rag_enabled: bool = False
    rag_top_k: int = 6
    rag_chunk_size: int = 1200  # characters per chunk
    rag_chunk_overlap: int = 200  # character overlap between adjacent chunks
    rag_max_chunks: int = 4000  # per-document safety cap on chunk count

    # Retrieval index backend: local (DocumentChunk table + in-Python ranking) or
    # a managed Azure AI Search index. Deployment-level, not a per-run setting.
    retrieval_backend: str = "local"  # local | azure_search

    # ---- Azure OpenAI (chat + embeddings + reasoning), used when
    # llm_provider / genai_embed_provider == "azure". Deployment names reuse
    # genai_model (chat) and genai_embed_model (embeddings). The key is read from
    # the env var NAMED below; its value is never stored on Settings (NFR-06).
    azure_openai_endpoint: str = ""  # e.g. https://my-res.openai.azure.com
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_api_key_env: str = "AZURE_OPENAI_API_KEY"  # env var NAME only
    azure_openai_reasoning: bool = False  # chat deployment is an o-series reasoning model

    # ---- Azure AI Search (retrieval_backend == "azure_search")
    azure_search_endpoint: str = ""  # e.g. https://my-res.search.windows.net
    azure_search_api_version: str = "2024-07-01"
    azure_search_api_key_env: str = "AZURE_SEARCH_API_KEY"  # env var NAME only
    azure_search_index: str = "cam-chunks"

    # ---- Azure Blob Storage (blob_backend == "azure"): binaries + text extracts
    blob_backend: str = "local"  # local | azure
    azure_blob_connection_env: str = "AZURE_BLOB_CONNECTION_STRING"  # env var NAME
    azure_blob_account_url: str = ""  # alt to connection string (AAD/managed identity)
    azure_blob_container_blobs: str = "cam-blobs"
    azure_blob_container_extracts: str = "cam-extracts"

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
    # Generation worker pool. worker_pool_size is the number of worker tasks
    # spawned at startup — the HARD CEILING on concurrent sections (infra sizing,
    # changing it needs a restart). worker_concurrency is the DEFAULT active
    # concurrency; the live value is an admin master setting ("worker_concurrency")
    # clamped to [1, pool_size] and applied without a restart (idle workers above
    # the active count). Back-compat: CAM_WORKER_CONCURRENCY still seeds both.
    worker_concurrency: int = 2
    worker_pool_size: int = 8
    # Run-level admission queue: bursts of run requests are all ACCEPTED (queued),
    # and at most max_concurrent_runs generate at once (FIFO, admin-tunable master
    # setting); the rest wait and start automatically as slots free.
    # max_active_runs_per_user is a per-user fairness cap on RUNNING runs so one
    # user's burst cannot occupy every slot (submission is never rejected).
    max_concurrent_runs: int = 4
    max_active_runs_per_user: int = 2

    # Email (SMTP) notifications. If smtp_host is empty the mailer LOGS the message
    # instead of sending (dev/no-op) so the feature works with zero configuration.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    # Only the NAME of the env var holding the SMTP password is stored (NFR-06); the
    # value is read from os.environ at send time and never placed on Settings/logged.
    smtp_password_env: str = "CAM_SMTP_PASSWORD"
    smtp_from: str = "CAM Studio <cam-studio@bank.example>"
    smtp_starttls: bool = True  # STARTTLS on a plain connection (port 587)
    smtp_ssl: bool = False      # implicit TLS (SMTPS, port 465) — mutually exclusive
    smtp_timeout_seconds: int = 15
    # Base URL the SPA is served from (the single-origin gateway) — used to build
    # deep links back to a run in notification emails.
    app_base_url: str = "http://localhost:8080"
    # Fallback for the runtime 'email_notifications' master toggle. The shipped
    # default is ON via master DEFAULT_SETTINGS; this env-layer fallback is
    # intentionally OFF so a master-config outage fails safe (no surprise mail when
    # the toggle can't be read) rather than fail-open.
    email_notifications: bool = False

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
