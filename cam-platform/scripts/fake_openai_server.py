"""A minimal OpenAI-compatible /v1/chat/completions server.

It stands in for a real customer LLM endpoint so the *live* provider path
(CAM_LLM_PROVIDER=openai) can be exercised end-to-end offline — same HTTP shape
a real endpoint uses. It inspects the system prompt to tell which agent role is
calling and returns a well-formed reply for that role, grounded only in the
figures present in the user message (so the no-fabrication trace check and the
agentic checks stay meaningful).

    python scripts/fake_openai_server.py --port 8909
    # then: CAM_LLM_PROVIDER=openai CAM_GENAI_BASE_URL=http://127.0.0.1:8909/v1 \
    #       CAM_GENAI_MODEL=fake-1 python scripts/run_stack.py

Not for production — a test/demo aid only.
"""
from __future__ import annotations

import argparse
import json
import re

from fastapi import FastAPI, Request
import uvicorn

app = FastAPI(title="fake-openai-compatible")

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> list[str]:
    seen, out = set(), []
    for tok in _NUM.findall(text):
        t = tok.replace(",", "")
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _reply(system: str, user: str) -> str:
    s = system.lower()
    if "extraction agent" in s:
        facts = [{"item": f"figure {n}", "value": n, "unit": "", "source": "DOC",
                  "quote": f"value {n} as reported"} for n in _numbers(user)[:12]]
        return json.dumps({"facts": facts})
    if "materiality check agent" in s:
        return json.dumps({"passed": True, "omissions": [], "flags": [],
                           "notes": "all material items covered (fake endpoint)"})
    if "consistency check agent" in s:
        return json.dumps({"passed": True, "inconsistencies": [],
                           "notes": "draft agrees with the extracted facts (fake endpoint)"})
    if "classif" in s or "document type" in s:
        return json.dumps({"code": None, "confidence": 0.0,
                           "rationale": "fake endpoint does not classify"})
    # generate / edit — echo only numbers present in the grounding
    nums = _numbers(user)
    body = ("Fake-endpoint draft grounded on the supplied sources. "
            + ("Observed figures: " + ", ".join(nums) + "." if nums
               else "No quantitative data points were present in the sources."))
    return body


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> dict:
    payload = await request.json()
    messages = payload.get("messages") or []
    system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
    user = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
    content = _reply(system, user)
    return {
        "id": "fake-cmpl-1",
        "object": "chat.completion",
        "model": payload.get("model", "fake-1"),
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": (len(system) + len(user)) // 4,
                  "completion_tokens": len(content) // 4,
                  "total_tokens": (len(system) + len(user) + len(content)) // 4},
    }


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8909)
    args = ap.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
