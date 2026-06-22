"""Pluggable Claude provider selection.

The rest of the codebase only needs a client that exposes ``messages.parse`` and
a ``Capabilities`` flag set so it can degrade gracefully across providers. We use
base64 ``document`` blocks + explicit ``cache_control`` everywhere (supported on
direct / Bedrock / Vertex), so we avoid the Files API (not on Bedrock/Vertex) in
v1 and keep one portable code path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from asm_review.config import Settings


@dataclass(frozen=True)
class Capabilities:
    files_api: bool
    auto_prompt_caching: bool
    explicit_cache_control: bool


def get_client_and_caps(settings: Settings):
    """Return ``(client, Capabilities)`` for the configured provider."""
    provider = (settings.llm_provider or "anthropic").lower()

    if provider == "anthropic":
        from anthropic import Anthropic

        return Anthropic(), Capabilities(
            files_api=True, auto_prompt_caching=True, explicit_cache_control=True
        )

    if provider == "bedrock":
        from anthropic import AnthropicBedrockMantle

        region = settings.aws_region or os.getenv("AWS_REGION") or "us-east-1"
        return AnthropicBedrockMantle(aws_region=region), Capabilities(
            files_api=False, auto_prompt_caching=False, explicit_cache_control=True
        )

    if provider == "vertex":
        from anthropic import AnthropicVertex

        if not settings.vertex_project_id:
            raise ValueError("VERTEX_PROJECT_ID is required when LLM_PROVIDER=vertex")
        return AnthropicVertex(
            project_id=settings.vertex_project_id, region=settings.vertex_region
        ), Capabilities(
            files_api=False, auto_prompt_caching=False, explicit_cache_control=True
        )

    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")


def model_id(settings: Settings) -> str:
    """Resolve the model id for the provider (Bedrock needs an ``anthropic.`` prefix)."""
    model = settings.model
    if (settings.llm_provider or "").lower() == "bedrock" and not model.startswith("anthropic."):
        return f"anthropic.{model}"
    return model
