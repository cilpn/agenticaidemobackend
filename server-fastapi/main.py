"""
FastAPI backend for /api/agent-reply.

This is the ONLY place the Anthropic API key lives. It reads from a
server-side env var (ANTHROPIC_API_KEY) that is never bundled into the
React/Vite client — the browser only ever calls this same-origin endpoint.

Equivalent in behavior to the Vercel edge-function version:
- POST /api/agent-reply
- body: { "systemPrompt": str, "history": [{"role": "user"|"assistant", "content": str}, ...] }
- response: { "reply": str }
"""

import os
import time
from collections import defaultdict
from typing import List, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "https://aidemo.cilpron.com")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# Basic guardrails so a stray client bug (or a bad actor probing this
# public endpoint) can't run up a huge bill.
MAX_HISTORY = 40
MAX_TOKENS = 400

# Naive in-memory per-IP rate limit. The key is never exposed to the
# browser, but this endpoint is still an unauthenticated public proxy to
# the Anthropic API, so this caps how much any one caller can run up.
# Resets if the process restarts; fine for a single-instance demo.
RATE_LIMIT = 20  # requests
WINDOW_SECONDS = 60
_hits: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))


def is_rate_limited(ip: str) -> bool:
    count, reset_at = _hits[ip]
    now = time.time()
    if now > reset_at:
        count, reset_at = 0, now + WINDOW_SECONDS
    count += 1
    _hits[ip] = (count, reset_at)
    return count > RATE_LIMIT


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AgentReplyRequest(BaseModel):
    systemPrompt: str
    history: List[ChatMessage]


class AgentReplyResponse(BaseModel):
    reply: str


app = FastAPI()

# CORS is defense-in-depth, not the security boundary — the real
# protection is that ANTHROPIC_API_KEY only ever lives in this process.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.post("/api/agent-reply", response_model=AgentReplyResponse)
async def agent_reply(payload: AgentReplyRequest, request: Request):
    # Rate limit first — this guard should apply even if the server is
    # misconfigured, so a broken deploy can't be hammered for free.
    client_ip = request.client.host if request.client else "unknown"
    if is_rate_limited(client_ip):
        raise HTTPException(status_code=429, detail="Too many requests, try again shortly")

    if not API_KEY:
        raise HTTPException(status_code=500, detail="Server misconfigured: ANTHROPIC_API_KEY not set")

    if len(payload.history) > MAX_HISTORY:
        raise HTTPException(status_code=400, detail="Conversation too long")

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            anthropic_res = await client.post(
                ANTHROPIC_URL,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": API_KEY,
                    "anthropic-version": "2023-06-01",
                    # No dangerous-direct-browser-access header needed —
                    # this call happens server-to-server.
                },
                json={
                    "model": MODEL,
                    "max_tokens": MAX_TOKENS,
                    "system": [
                        {
                            "type": "text",
                            "text": payload.systemPrompt,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    "messages": [m.model_dump() for m in payload.history],
                },
            )
        except httpx.RequestError:
            raise HTTPException(status_code=502, detail="Upstream request to Anthropic failed")

    if anthropic_res.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Anthropic API error {anthropic_res.status_code}: {anthropic_res.text}",
        )

    data = anthropic_res.json()
    text_block = next((b for b in data.get("content", []) if b.get("type") == "text"), None)
    reply = (text_block or {}).get("text") or "Sorry, I didn't catch that — could you repeat it?"

    return AgentReplyResponse(reply=reply)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}