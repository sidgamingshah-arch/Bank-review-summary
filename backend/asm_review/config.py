"""Runtime configuration, loaded from environment / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings.

    Provider credentials beyond the fields below (Anthropic API key, full AWS
    credential chain) are read by the SDK clients directly from the environment.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
        # `model` would otherwise clash with pydantic's protected "model_" namespace.
        protected_namespaces=(),
    )

    # LLM provider selection
    llm_provider: str = "anthropic"  # anthropic | bedrock | vertex
    model: str = "claude-opus-4-8"

    # Provider-specific (only the relevant ones are used)
    aws_region: str | None = None
    vertex_project_id: str | None = None
    vertex_region: str = "global"

    # App behaviour
    placeholder_text: str = "[To be entered by L1]"
    max_pdf_mb: int = 28
    max_tokens: int = 16000
    data_dir: str = "./data"


def get_settings() -> Settings:
    return Settings()
