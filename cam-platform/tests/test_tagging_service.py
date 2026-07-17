"""tagging service tests — deterministic scorer unit tests + classify
endpoint auth/caching."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.conftest import make_service_headers, make_user_headers

from cam.services.tagging import main as tag_main
from cam.services.tagging import scorer

ANALYST = make_user_headers("analyst1", ["analyst"])
REVIEWER = make_user_headers("reviewer1", ["reviewer"])
ADMIN = make_user_headers("admin1", ["business_admin"])
SERVICE = make_service_headers("document")

DOCTYPES = [
    {"code": "financials", "name": "Audited Financials",
     "synonyms": ["annual report", "financial statements"],
     "keywords": ["balance sheet", "profit and loss"], "active": True},
    {"code": "kyc", "name": "KYC Pack",
     "synonyms": ["know your customer"], "keywords": [], "active": True},
    {"code": "stock", "name": "Stock Statement",
     "synonyms": [], "keywords": ["inventory"], "active": True},
    {"code": "legacy", "name": "Legacy Type",
     "synonyms": ["legacy"], "keywords": [], "active": False},
]


@pytest.fixture()
def client():
    with TestClient(tag_main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def clear_cache():
    tag_main._cache.clear()
    yield
    tag_main._cache.clear()


@pytest.fixture()
def mocked_masters(monkeypatch):
    monkeypatch.setattr(tag_main, "fetch_published_doctypes", lambda: DOCTYPES)
    monkeypatch.setattr(tag_main, "fetch_threshold", lambda: 0.6)


# ---------------------------------------------------------------- scorer

def test_filename_hit_beats_text_hit():
    result = scorer.classify("annual_report_2025.pdf", "know your customer form",
                             DOCTYPES, 0.55)
    codes = [c["doctype_code"] for c in result["candidates"]]
    assert codes == ["financials", "kyc"]
    # filename phrase hit = 3.0 -> 3/(3+4); single text hit = 1.0 -> 1/(1+4)
    assert result["candidates"][0]["confidence"] == round(3.0 / 7.0, 3)
    assert result["candidates"][1]["confidence"] == round(1.0 / 5.0, 3)
    assert result["best"]["doctype_code"] == "financials"


def test_multiword_phrases_match_contiguously_only():
    scattered = scorer.classify("", "the balance of the sheet is fine", DOCTYPES, 0.55)
    assert scattered["candidates"] == []
    assert scattered["best"] is None

    contiguous = scorer.classify("", "the balance sheet is fine", DOCTYPES, 0.55)
    assert contiguous["best"]["doctype_code"] == "financials"
    assert contiguous["best"]["confidence"] == round(1.0 / 5.0, 3)


def test_text_occurrences_capped_per_phrase():
    result = scorer.classify("", "inventory " * 8, DOCTYPES, 0.55)
    best = result["best"]
    assert best["doctype_code"] == "stock"
    # 8 occurrences capped at 5 -> score 5.0 -> 5/9
    assert best["confidence"] == round(5.0 / 9.0, 3)


def test_needs_review_follows_threshold():
    high = scorer.classify("annual_report.pdf", "", DOCTYPES, 0.9)
    assert high["best"]["needs_review"] is True
    low = scorer.classify("annual_report.pdf", "", DOCTYPES, 0.3)
    assert low["best"]["needs_review"] is False
    assert high["threshold"] == 0.9


def test_empty_text_classifies_on_filename_alone():
    result = scorer.classify("kyc_pack_2024.pdf", "", DOCTYPES, 0.55)
    best = result["best"]
    assert best["doctype_code"] == "kyc"
    # name words 'kyc' + 'pack' both hit the filename -> 6.0 -> 6/10
    assert best["confidence"] == 0.6


def test_inactive_doctypes_and_no_match():
    result = scorer.classify("legacy.pdf", "legacy legacy legacy", DOCTYPES, 0.55)
    assert result["candidates"] == []
    assert result["best"] is None

    nothing = scorer.classify("random.bin", "nothing relevant here", DOCTYPES, 0.55)
    assert nothing == {"candidates": [], "threshold": 0.55, "best": None}


def test_candidates_capped_at_five_and_sorted():
    many = [{"code": f"d{i}", "name": f"T{i}", "synonyms": [],
             "keywords": [f"term{i}"], "active": True} for i in range(7)]
    # d_i gets i+1 text occurrences, capped at 5 -> d4/d5/d6 tie at 5.0 and
    # sort deterministically by code; then d3 (4.0), d2 (3.0)
    text = " ".join(f"term{i} " * (i + 1) for i in range(7))
    result = scorer.classify("", text, many, 0.55)
    assert [c["doctype_code"] for c in result["candidates"]] == ["d4", "d5", "d6", "d3", "d2"]
    confidences = [c["confidence"] for c in result["candidates"]]
    assert confidences == sorted(confidences, reverse=True)


# -------------------------------------------------------------- endpoint

def test_classify_endpoint_auth(client, mocked_masters):
    body = {"filename": "annual_report.pdf", "text": ""}
    # user tokens without masters:settings -> 403
    for headers in (ANALYST, REVIEWER):
        resp = client.post("/api/tagging/classify", json=body, headers=headers)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden"
    # no token -> 401
    assert client.post("/api/tagging/classify", json=body).status_code == 401
    # service token and business_admin -> 200
    assert client.post("/api/tagging/classify", json=body, headers=SERVICE).status_code == 200
    assert client.post("/api/tagging/classify", json=body, headers=ADMIN).status_code == 200


def test_classify_endpoint_contract_shape(client, mocked_masters):
    resp = client.post("/api/tagging/classify",
                       json={"filename": "annual_report.pdf",
                             "text": "balance sheet and profit and loss"},
                       headers=SERVICE)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"candidates", "threshold", "best", "llm_consulted"}
    assert body["threshold"] == 0.6
    # filename 'annual report' (3.0) + text 'balance sheet' (1.0)
    # + 'profit and loss' (1.0) = 5.0 -> 5/9 = 0.556 < 0.6 -> needs review
    assert body["best"] == {"doctype_code": "financials",
                            "confidence": round(5.0 / 9.0, 3),
                            "needs_review": True, "method": "keyword"}
    assert body["candidates"][0]["doctype_code"] == "financials"
    assert all(set(c) == {"doctype_code", "confidence"} for c in body["candidates"])


def test_masters_lookups_cached_for_60s(client, monkeypatch):
    calls = {"doctypes": 0, "threshold": 0}

    def fake_doctypes():
        calls["doctypes"] += 1
        return DOCTYPES

    def fake_threshold():
        calls["threshold"] += 1
        return 0.5

    monkeypatch.setattr(tag_main, "fetch_published_doctypes", fake_doctypes)
    monkeypatch.setattr(tag_main, "fetch_threshold", fake_threshold)

    for _ in range(3):
        resp = client.post("/api/tagging/classify",
                           json={"filename": "kyc.pdf", "text": ""}, headers=SERVICE)
        assert resp.status_code == 200
    assert calls == {"doctypes": 1, "threshold": 1}

    # age the cache past the TTL -> next call refetches
    for key, (ts, value) in list(tag_main._cache.items()):
        tag_main._cache[key] = (ts - 61.0, value)
    assert client.post("/api/tagging/classify",
                       json={"filename": "kyc.pdf", "text": ""},
                       headers=SERVICE).status_code == 200
    assert calls == {"doctypes": 2, "threshold": 2}


def test_threshold_fallback_default():
    assert tag_main.DEFAULT_THRESHOLD == 0.55


# ---------------------------------------------------------------- llm fallback

def test_llm_fallback_used_when_keyword_misses(client, mocked_masters, monkeypatch):
    calls = []

    def fake_llm(filename, text, doctypes):
        calls.append(filename)
        return {"code": "stock", "confidence": 0.82, "rationale": "inventory-like content"}

    monkeypatch.setattr(tag_main, "llm_classify", fake_llm)
    body = client.post("/api/tagging/classify",
                       json={"filename": "scan_991.pdf",
                            "text": "goods lying at the godown were physically verified"},
                       headers=SERVICE).json()
    assert calls, "LLM must be consulted when keyword scoring finds nothing"
    assert body["llm_consulted"] is True
    assert body["best"] == {"doctype_code": "stock", "confidence": 0.82,
                            "needs_review": False, "method": "llm",
                            "rationale": "inventory-like content"}
    assert body["candidates"][0] == {"doctype_code": "stock", "confidence": 0.82}


def test_llm_fallback_below_threshold_flags_review(client, mocked_masters, monkeypatch):
    monkeypatch.setattr(tag_main, "llm_classify",
                        lambda f, t, d: {"code": "kyc", "confidence": 0.4, "rationale": ""})
    body = client.post("/api/tagging/classify", json={"filename": "x.pdf", "text": "y"},
                       headers=SERVICE).json()
    assert body["best"]["method"] == "llm" and body["best"]["needs_review"] is True


def test_llm_not_consulted_when_keyword_confident(client, mocked_masters, monkeypatch):
    def boom(filename, text, doctypes):
        raise AssertionError("LLM must not be consulted for a confident keyword match")

    monkeypatch.setattr(tag_main, "llm_classify", boom)
    body = client.post("/api/tagging/classify",
                       json={"filename": "annual_report.pdf",
                             "text": "balance sheet profit and loss balance sheet"},
                       headers=SERVICE).json()
    assert body["llm_consulted"] is False
    assert body["best"]["method"] == "keyword" and body["best"]["needs_review"] is False


def test_llm_failure_keeps_keyword_result(client, mocked_masters, monkeypatch):
    monkeypatch.setattr(tag_main, "llm_classify", lambda f, t, d: None)  # gateway down
    body = client.post("/api/tagging/classify",
                       json={"filename": "annual_report.pdf", "text": ""},
                       headers=SERVICE).json()
    assert body["llm_consulted"] is False
    assert body["best"]["doctype_code"] == "financials"          # weak keyword hit survives
    assert body["best"]["method"] == "keyword" and body["best"]["needs_review"] is True


def test_llm_weaker_or_null_never_downgrades_keyword(client, mocked_masters, monkeypatch):
    # LLM returns a lower-confidence guess than the (weak) keyword best -> keyword kept
    monkeypatch.setattr(tag_main, "llm_classify",
                        lambda f, t, d: {"code": "kyc", "confidence": 0.1, "rationale": ""})
    body = client.post("/api/tagging/classify",
                       json={"filename": "annual_report.pdf", "text": ""},
                       headers=SERVICE).json()
    assert body["llm_consulted"] is True
    assert body["best"]["doctype_code"] == "financials"
    assert body["best"]["method"] == "keyword"

    # LLM abstains (null code) -> keyword result untouched
    monkeypatch.setattr(tag_main, "llm_classify",
                        lambda f, t, d: {"code": None, "confidence": 0.0, "rationale": "none"})
    body = client.post("/api/tagging/classify",
                       json={"filename": "annual_report.pdf", "text": ""},
                       headers=SERVICE).json()
    assert body["best"]["doctype_code"] == "financials"
