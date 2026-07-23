"""Validate the configured LLM endpoint before a live run — no stack required.

Constructs the provider exactly as the genai-gateway does (from CAM_* env) and
issues one tiny generation, printing the model id and token usage. Use it to
confirm CAM_GENAI_BASE_URL / CAM_GENAI_MODEL / the API key are correct.

    CAM_LLM_PROVIDER=openai CAM_GENAI_BASE_URL=https://llm.internal/v1 \
    CAM_GENAI_MODEL=your-model CAM_GENAI_API_KEY=... \
    python scripts/llm_smoke.py
"""
from __future__ import annotations

import sys

from cam.common.config import get_settings
from cam.common.errors import ApiError
from cam.services.genai import providers


def main() -> int:
    settings = get_settings("genai")
    print(f"provider={settings.llm_provider} model={settings.genai_model} "
          f"base_url={settings.genai_base_url or '(default)'}")
    try:
        provider = providers.make_provider(settings)
    except ApiError as exc:
        print(f"misconfigured: {exc.message}")
        return 2

    system = "You are a terse assistant. Reply with one short sentence."
    user = "In one sentence, confirm you can generate text for a credit memo."
    try:
        result = provider.generate({}, system, user)
    except ApiError as exc:
        print(f"endpoint error [{exc.code}]: {exc.message}")
        return 1
    except Exception as exc:  # pragma: no cover - defensive
        print(f"unexpected error: {type(exc).__name__}: {exc}")
        return 1

    print(f"OK  model={result.model}  usage={result.usage}")
    print(f"reply: {result.content[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
