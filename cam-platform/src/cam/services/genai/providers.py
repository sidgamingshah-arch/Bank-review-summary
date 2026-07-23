"""LLM providers behind the GenAI gateway.

``mock``     — deterministic, offline composer used for local dev, tests and
               demos. It only ever repeats figures found in the supplied
               grounding material, so the no-fabrication trace check stays
               meaningful end-to-end without network access.
``anthropic``— the bank-approved model endpoint via the official Anthropic SDK
               (swap-in point for Bedrock/Vertex per the bank's hosting choice).
``openai``   — any user-supplied, OpenAI-compatible chat-completions endpoint
               (vLLM, LiteLLM, Azure OpenAI, Ollama, a bank-hosted gateway).
               Configured entirely from env: base URL, model, and an API key
               read from a named env var at construction — never logged (NFR-06).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import httpx

from cam.common.config import Settings
from cam.common.errors import ApiError


@dataclass
class GenResult:
    content: str
    model: str
    usage: dict = field(default_factory=dict)
    rationale: str = ""


def _estimate_usage(system: str, user: str, content: str) -> dict:
    return {"input_tokens": (len(system) + len(user)) // 4,
            "output_tokens": len(content) // 4}


# --------------------------------------------------------------------- mock

_SENTENCE_SPLIT = re.compile(r"(?<=[.;])\s+|\n+")
_HAS_DIGIT = re.compile(r"\d")

_LENGTH_FACTS = {"concise": 3, "standard": 6, "detailed": 10}


def _fact_sentences(text: str, cap: int) -> list[str]:
    facts = []
    for raw in _SENTENCE_SPLIT.split(text or ""):
        s = " ".join(raw.split()).strip(" -|")
        if 15 <= len(s) <= 240 and _HAS_DIGIT.search(s):
            facts.append(s.rstrip(".") + ".")
        if len(facts) >= cap:
            break
    return facts


class MockProvider:
    name = "mock"

    def __init__(self, settings: Settings):
        self.model = "mock-cam-composer-v1"

    def generate(self, request: dict, system: str, user: str) -> GenResult:
        placeholders = request.get("placeholders") or {}
        docs = request.get("grounding_docs") or []
        prefs = (request.get("preferences") or {}) if not request.get("fixed_format") else {}
        borrower = placeholders.get("borrower_name", "the borrower")
        industry = placeholders.get("industry_name", "")

        cap = _LENGTH_FACTS.get(prefs.get("length", "standard"), 6)
        facts: list[tuple[str, str]] = []
        extracted = request.get("extracted_facts") or []
        if extracted:
            # agentic pipeline: the extraction agent's facts are the grounding
            for fact in extracted[:cap]:
                quote = str(fact.get("quote", "")).rstrip(".") + "."
                facts.append((str(fact.get("source", "source")), quote))
        for doc in docs:
            if extracted:
                break
            for fact in _fact_sentences(doc.get("text", ""), max(2, cap // max(len(docs), 1) + 1)):
                if len(facts) < cap:
                    facts.append((doc.get("label") or doc.get("doctype_code", "source"), fact))

        parts: list[str] = []
        intro = f"Assessment of {borrower}"
        if industry:
            intro += f" ({industry})"
        if docs:
            labels = ", ".join(sorted({d.get("label") or d.get("doctype_code", "?") for d in docs}))
            intro += f", grounded on the following sources: {labels}."
        else:
            intro += "."
        parts.append(intro)

        if not docs:
            parts.append("[data gap: no mapped source documents were available for this section]")
        elif not facts:
            parts.append("The mapped source documents contain no quantitative data points "
                         "usable for this section. [data gap: quantitative inputs missing "
                         "from mapped sources]")
        elif prefs.get("table_usage") == "prefer":
            rows = ["| Source | Observation |", "|---|---|"]
            rows += [f"| {label} | {fact} |" for label, fact in facts]
            parts.append("\n".join(rows))
        elif prefs.get("structure_bias") == "bullets":
            parts.append("\n".join(f"- {fact} *(source: {label})*" for label, fact in facts))
        else:
            parts.append(" ".join(fact for _, fact in facts))

        kpis = placeholders.get("industry_kpis", "")
        if request.get("layers", {}).get("section_prompt", "").find("KPI") >= 0 or kpis:
            if kpis and not kpis.startswith("("):
                parts.append(f"**Industry KPI framework applied:** {kpis.splitlines()[0]}")

        feedback = request.get("feedback") or {}
        coverage = []
        for omission in feedback.get("omissions") or []:
            coverage.append(f"- {omission}: not evidenced in the supplied sources "
                            "[data gap: input required]")
        if coverage:
            parts.append("**Materiality coverage (per check agent):**\n" + "\n".join(coverage))
        if feedback.get("inconsistencies"):
            # facts-only recomposition above already realigns the figures
            parts.append("*Figures realigned to the extracted fact base per the "
                         "consistency check.*")

        if request.get("fixed_format"):
            parts.append("*Prepared in the bank's prescribed fixed format for this section.*")

        content = "\n\n".join(parts)
        return GenResult(content=content, model=self.model,
                         usage=_estimate_usage(system, user, content))

    def edit(self, request: dict, system: str, user: str) -> GenResult:
        current = request.get("current_content", "")
        instruction = (request.get("instruction") or "").lower()
        docs = request.get("grounding_docs") or []

        if "shorten" in instruction or "concise" in instruction or "summar" in instruction:
            sentences = [s for s in _SENTENCE_SPLIT.split(current) if s.strip()]
            keep = max(1, len(sentences) // 2)
            content = " ".join(s.strip() for s in sentences[:keep])
            rationale = f"Shortened the content from {len(sentences)} to {keep} sentences."
        elif "table" in instruction:
            rows = ["| # | Point |", "|---|---|"]
            idx = 0
            for s in _SENTENCE_SPLIT.split(current):
                s = " ".join(s.split()).strip("-| ")
                if _HAS_DIGIT.search(s) and len(s) > 10:
                    idx += 1
                    rows.append(f"| {idx} | {s} |")
            content = "\n".join(rows) if idx else current
            rationale = (f"Converted {idx} quantitative points into a markdown table."
                         if idx else "No quantitative points found to tabulate; content unchanged.")
        elif docs:
            facts = []
            for doc in docs:
                facts += [f"- {fact} *(source: {doc.get('label') or doc.get('doctype_code')})*"
                          for fact in _fact_sentences(doc.get("text", ""), 4)]
            supplement = ("\n\n**Supplementary analysis from newly supplied documents:**\n"
                          + ("\n".join(facts) if facts
                             else "- The supplied documents contain no additional quantitative "
                                  "data points. [data gap]"))
            content = current + supplement
            rationale = f"Incorporated {len(docs)} newly supplied document(s) as additional grounding."
        else:
            content = " ".join(x.strip() for x in current.splitlines() if x.strip())
            if content == current:
                content = current + "\n\n*Reviewed against the instruction; no factual changes required.*"
            rationale = ("Reformatted the section per the instruction without altering facts "
                         "or figures (mock provider).")

        return GenResult(content=content, model=self.model, rationale=rationale,
                         usage=_estimate_usage(system, user, content))

    def classify(self, request: dict, system: str, user: str) -> GenResult:
        """Deterministic semantic-ish fallback: bag-of-words overlap between the
        document and each doctype's whole vocabulary (name, code, synonyms,
        keywords AND description) — catches documents whose wording overlaps a
        type without containing its exact master phrases."""
        import json
        import re

        words = set(re.split(r"[^a-z0-9]+",
                             f"{request.get('filename', '')} {request.get('text', '')}".lower()))
        words.discard("")
        best_code, best_overlap = None, 0
        for doctype in request.get("doctypes") or []:
            vocab = " ".join([doctype.get("code", "").replace("_", " "),
                              doctype.get("name", ""), doctype.get("description", ""),
                              " ".join(doctype.get("synonyms") or []),
                              " ".join(doctype.get("keywords") or [])]).lower()
            vocab_words = {w for w in re.split(r"[^a-z0-9]+", vocab) if len(w) > 2}
            overlap = len(words & vocab_words)
            if overlap > best_overlap:
                best_code, best_overlap = doctype.get("code"), overlap
        payload = {"code": best_code if best_overlap >= 3 else None,
                   "confidence": round(best_overlap / (best_overlap + 3.0), 3),
                   "rationale": (f"{best_overlap} vocabulary words overlap with "
                                 f"'{best_code}'" if best_code else "no meaningful overlap")}
        return GenResult(content=json.dumps(payload), model=self.model,
                         usage=_estimate_usage(system, user, json.dumps(payload)))

    # agentic pipeline roles — deterministic mirrors of the model behaviour
    def extract(self, request: dict, system: str, user: str) -> GenResult:
        import json

        from . import agents
        content = json.dumps(agents.mock_extract(request))
        return GenResult(content=content, model=self.model,
                         usage=_estimate_usage(system, user, content))

    def materiality(self, request: dict, system: str, user: str) -> GenResult:
        import json

        from . import agents
        content = json.dumps(agents.mock_materiality(request))
        return GenResult(content=content, model=self.model,
                         usage=_estimate_usage(system, user, content))

    def consistency(self, request: dict, system: str, user: str) -> GenResult:
        import json

        from . import agents
        content = json.dumps(agents.mock_consistency(request))
        return GenResult(content=content, model=self.model,
                         usage=_estimate_usage(system, user, content))


# ----------------------------------------------------------------- anthropic

# Models where sampling params (temperature/top_p/top_k) are rejected by the API.
_NO_SAMPLING_PREFIXES = ("claude-opus-4-7", "claude-opus-4-8", "claude-fable",
                         "claude-mythos", "claude-sonnet-5")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: Settings):
        import anthropic  # optional dependency: pip install "cam-platform[anthropic]"

        self._anthropic = anthropic
        self.client = anthropic.Anthropic()  # credentials from env / vault (NFR-06)
        self.settings = settings

    def _call(self, request: dict, system: str, user: str) -> GenResult:
        overrides = request.get("model_overrides") or {}
        model = overrides.get("model") or self.settings.genai_model
        max_tokens = overrides.get("max_tokens") or self.settings.genai_max_tokens

        kwargs: dict = {}
        temperature = overrides.get("temperature")
        if temperature is not None and not model.startswith(_NO_SAMPLING_PREFIXES):
            kwargs["temperature"] = temperature

        try:
            response = self.client.messages.create(
                model=model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": user}], **kwargs)
        except self._anthropic.APIStatusError as exc:
            raise ApiError(502, "genai_upstream_error",
                           f"model endpoint returned {exc.status_code}: {exc.message}")
        except self._anthropic.APIConnectionError:
            raise ApiError(502, "genai_upstream_error", "model endpoint unreachable")

        if response.stop_reason == "refusal":
            raise ApiError(502, "model_refusal",
                           "the model declined this request; section flagged for manual drafting")

        content = "".join(b.text for b in response.content if b.type == "text")
        usage = {"input_tokens": response.usage.input_tokens,
                 "output_tokens": response.usage.output_tokens}
        return GenResult(content=content, model=response.model, usage=usage)

    def generate(self, request: dict, system: str, user: str) -> GenResult:
        return self._call(request, system, user)

    def edit(self, request: dict, system: str, user: str) -> GenResult:
        result = self._call(request, system, user)
        result.rationale = "Revision proposed by the model per the analyst's instruction."
        return result

    def classify(self, request: dict, system: str, user: str) -> GenResult:
        return self._call(request, system, user)

    def extract(self, request: dict, system: str, user: str) -> GenResult:
        return self._call(request, system, user)

    def materiality(self, request: dict, system: str, user: str) -> GenResult:
        return self._call(request, system, user)

    def consistency(self, request: dict, system: str, user: str) -> GenResult:
        return self._call(request, system, user)


# ------------------------------------------------------- openai-compatible

class OpenAICompatibleProvider:
    """A user-supplied, OpenAI-compatible chat-completions endpoint.

    One HTTP path serves every role; the pre-assembled ``system`` and ``user``
    strings are sent verbatim as chat messages (the provider never re-assembles
    prompts). The API key is read from the env var named by
    ``settings.genai_api_key_env`` and held only on the HTTP client's headers —
    it is never stored on Settings and never logged (NFR-06). Upstream failures
    map to the same 502 envelope the Anthropic path uses.
    """

    name = "openai"

    def __init__(self, settings: Settings):
        if not settings.genai_base_url:
            raise ApiError(500, "genai_misconfigured",
                           "CAM_GENAI_BASE_URL must be set when CAM_LLM_PROVIDER=openai")
        self.settings = settings
        self._url = settings.genai_base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        key = os.environ.get(settings.genai_api_key_env, "")
        if key:
            scheme = (settings.genai_auth_scheme or "").strip()
            headers["Authorization"] = f"{scheme} {key}".strip()
        # kept for the process lifetime (provider is a get_provider() singleton)
        self.client = httpx.Client(timeout=settings.genai_timeout_seconds, headers=headers)

    def _call(self, request: dict, system: str, user: str) -> GenResult:
        overrides = request.get("model_overrides") or {}
        model = overrides.get("model") or self.settings.genai_model
        max_tokens = overrides.get("max_tokens") or self.settings.genai_max_tokens
        temperature = overrides.get("temperature")
        if temperature is None:
            temperature = self.settings.genai_temperature

        body = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            resp = self.client.post(self._url, json=body)
        except httpx.HTTPError:
            # message deliberately carries no request/response detail (NFR-06)
            raise ApiError(502, "genai_upstream_error", "model endpoint unreachable")

        if resp.status_code >= 400:
            raise ApiError(502, "genai_upstream_error",
                           f"model endpoint returned {resp.status_code}")
        try:
            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = message.get("content") or ""
            finish = choice.get("finish_reason")
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            # AttributeError: a 200 body that is a JSON array/scalar, not an object
            raise ApiError(502, "genai_upstream_error",
                           "model endpoint returned an unreadable response")

        if isinstance(content, list):  # some gateways return content as parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))

        if finish == "content_filter":
            raise ApiError(502, "model_refusal",
                           "the model declined this request; section flagged for manual drafting")

        usage_raw = data.get("usage") or {}
        usage = {"input_tokens": int(usage_raw.get("prompt_tokens", 0) or 0),
                 "output_tokens": int(usage_raw.get("completion_tokens", 0) or 0)}
        if not usage["input_tokens"] and not usage["output_tokens"]:
            usage = _estimate_usage(system, user, content)
        return GenResult(content=content, model=data.get("model") or model, usage=usage)

    def generate(self, request: dict, system: str, user: str) -> GenResult:
        return self._call(request, system, user)

    def edit(self, request: dict, system: str, user: str) -> GenResult:
        result = self._call(request, system, user)
        result.rationale = "Revision proposed by the model per the analyst's instruction."
        return result

    def classify(self, request: dict, system: str, user: str) -> GenResult:
        return self._call(request, system, user)

    def extract(self, request: dict, system: str, user: str) -> GenResult:
        return self._call(request, system, user)

    def materiality(self, request: dict, system: str, user: str) -> GenResult:
        return self._call(request, system, user)

    def consistency(self, request: dict, system: str, user: str) -> GenResult:
        return self._call(request, system, user)


def make_provider(settings: Settings):
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(settings)
    if settings.llm_provider == "openai":
        return OpenAICompatibleProvider(settings)
    return MockProvider(settings)
