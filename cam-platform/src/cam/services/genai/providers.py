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


@dataclass
class EmbedResult:
    vectors: list[list[float]]
    model: str
    dim: int
    usage: dict = field(default_factory=dict)


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
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        # Some models (e.g. Claude opus-4-x behind an OpenAI-compatible gateway)
        # reject sampling params — mirror the Anthropic path and omit them there.
        if temperature is not None and not model.startswith(_NO_SAMPLING_PREFIXES):
            body["temperature"] = temperature
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

        # usage is best-effort: default to an estimate, override only with clean
        # numeric counts (a non-dict or non-numeric usage must not 500 the call).
        usage = _estimate_usage(system, user, content)
        usage_raw = data.get("usage")
        if isinstance(usage_raw, dict):
            try:
                inp = int(usage_raw.get("prompt_tokens") or 0)
                out = int(usage_raw.get("completion_tokens") or 0)
                if inp or out:
                    usage = {"input_tokens": inp, "output_tokens": out}
            except (TypeError, ValueError):
                pass
        model_id = data.get("model") if isinstance(data.get("model"), str) else None
        return GenResult(content=content, model=model_id or model, usage=usage)

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


# ============================================================ embeddings
# Embeddings power large-document retrieval (RAG). A dedicated abstraction —
# NOT a fourth role on the chat providers — because the embed contract is
# texts -> vectors (not the request/system/user -> GenResult of chat), and
# Anthropic has no embeddings endpoint, so the embed backend must be selectable
# independently of the chat provider.

import hashlib
import math

_EMBED_TOKEN = re.compile(r"[^a-z0-9]+")


def _l2_normalise(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


class MockEmbedder:
    """Deterministic, offline embedder used for dev, tests and demos.

    Each text is tokenised the same way the mock classifier is, every token is
    hashed (stable ``hashlib`` digest — NOT the salted builtin ``hash``) into a
    fixed-dimension bag-of-words vector, and the vector is L2-normalised so
    cosine similarity is meaningful. Keyword-overlapping passages therefore rank
    together — enough for retrieval to work end-to-end without a network.
    """

    name = "mock"

    def __init__(self, settings: Settings):
        self.model = "mock-embed-v1"
        self.dim = max(16, int(settings.genai_embed_dim or 256))

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in _EMBED_TOKEN.split((text or "").lower()):
            if len(tok) <= 2:
                continue
            idx = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16) % self.dim
            vec[idx] += 1.0
        return _l2_normalise(vec)

    def embed(self, texts: list[str]) -> EmbedResult:
        vectors = [self._one(t) for t in texts]
        chars = sum(len(t or "") for t in texts)
        return EmbedResult(vectors=vectors, model=self.model, dim=self.dim,
                           usage={"input_tokens": chars // 4, "output_tokens": 0})


class OpenAIEmbedder:
    """OpenAI-compatible embeddings endpoint (POST ``<base_url>/embeddings``).

    Reuses the chat provider's URL/auth convention: ``genai_embed_base_url`` (or
    ``genai_base_url``) already carries the version prefix (e.g. ``/v1``) and
    ``/embeddings`` is appended. The API key is read from the env var named by
    ``genai_embed_api_key_env`` and held only on the HTTP client's headers —
    never stored on Settings, never logged (NFR-06). Inputs are sub-batched to
    stay within provider request limits; failures map to the 502 envelope.
    """

    name = "openai"
    _BATCH = 96

    def __init__(self, settings: Settings):
        base = (settings.genai_embed_base_url or settings.genai_base_url or "").rstrip("/")
        if not base:
            raise ApiError(500, "genai_misconfigured",
                           "CAM_GENAI_EMBED_BASE_URL (or CAM_GENAI_BASE_URL) must be set "
                           "when CAM_GENAI_EMBED_PROVIDER=openai")
        if not settings.genai_embed_model:
            raise ApiError(500, "genai_misconfigured",
                           "CAM_GENAI_EMBED_MODEL must be set when CAM_GENAI_EMBED_PROVIDER=openai")
        self.settings = settings
        self.model = settings.genai_embed_model
        self._url = base + "/embeddings"
        headers = {"Content-Type": "application/json"}
        key = os.environ.get(settings.genai_embed_api_key_env, "")
        if key:
            scheme = (settings.genai_auth_scheme or "").strip()
            headers["Authorization"] = f"{scheme} {key}".strip()
        self.client = httpx.Client(timeout=settings.genai_timeout_seconds, headers=headers)

    def _call_batch(self, texts: list[str]) -> tuple[list[list[float]], int]:
        try:
            resp = self.client.post(self._url, json={"model": self.model, "input": texts})
        except httpx.HTTPError:
            # message deliberately carries no request/response detail (NFR-06)
            raise ApiError(502, "genai_upstream_error", "embedding endpoint unreachable")
        if resp.status_code >= 400:
            raise ApiError(502, "genai_upstream_error",
                           f"embedding endpoint returned {resp.status_code}")
        try:
            data = resp.json()
            rows = sorted(data["data"], key=lambda d: d.get("index", 0))
            vectors = [[float(x) for x in row["embedding"]] for row in rows]
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise ApiError(502, "genai_upstream_error",
                           "embedding endpoint returned an unreadable response")
        if len(vectors) != len(texts):
            raise ApiError(502, "genai_upstream_error",
                           "embedding endpoint returned a mismatched vector count")
        usage_raw = data.get("usage") if isinstance(data, dict) else None
        toks = 0
        if isinstance(usage_raw, dict):
            try:
                toks = int(usage_raw.get("prompt_tokens") or usage_raw.get("total_tokens") or 0)
            except (TypeError, ValueError):
                toks = 0
        return vectors, toks

    def embed(self, texts: list[str]) -> EmbedResult:
        vectors: list[list[float]] = []
        tokens = 0
        for start in range(0, len(texts), self._BATCH):
            batch = texts[start:start + self._BATCH]
            vecs, toks = self._call_batch(batch)
            vectors.extend(vecs)
            tokens += toks
        dim = len(vectors[0]) if vectors else 0
        if tokens == 0:
            tokens = sum(len(t or "") for t in texts) // 4
        return EmbedResult(vectors=vectors, model=self.model, dim=dim,
                           usage={"input_tokens": tokens, "output_tokens": 0})


def make_embedder(settings: Settings):
    if settings.genai_embed_provider == "openai":
        return OpenAIEmbedder(settings)
    return MockEmbedder(settings)
