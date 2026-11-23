"""OpenAI uyumlu sahte LLM sunucusu (geliştirme ve testler için).

    uvicorn tools.sahte_llm:app --port 8001
    curl -X POST localhost:8001/_mode -H 'content-type: application/json' -d '{"mode": "429"}'

Modlar:
  ok          : istemdeki ipuçlarına göre JSON karar döner
  marker      : serbest metin + son satırda TRIAGE:
  429         : hep 429 (Retry-After: retry_after)
  429_once    : ilk çağrı 429, sonrakiler ok
  slow        : slow_s saniye bekleyip ok
  500 / 500_once
  garbage     : hep geçersiz yanıt
  garbage_once: ilk yanıt geçersiz, düzeltme turunda ok
"""

from __future__ import annotations

import asyncio
import json
import re
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="sahte-llm")

STATE: dict = {"mode": "ok", "slow_s": 10.0, "retry_after": 2, "calls": 0, "once_used": False, "last": None}

RULES = [
    (r"NullPointer|AttributeError|TypeError|Cannot invoke", "test_kodu", 0.82),
    (r"Connection refused|ConnectException|TimeoutException|zaman aşımı|ConnectionRefused", "ortam", 0.86),
    (r"expected|beklenen|AssertionError|assert", "urun", 0.78),
]


def decide(prompt: str) -> tuple[str, float, str]:
    for rx, cat, conf in RULES:
        m = re.search(rx, prompt)
        if m:
            return cat, conf, f"Sahte LLM: '{m.group(0)}' ifadesi {cat} kategorisini gösteriyor."
    return "bilinmiyor", 0.2, "Sahte LLM: belirgin işaret yok."


def completion(content: str, model: str) -> dict:
    return {
        "id": f"sahte-{STATE['calls']}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": max(1, len(content) // 4), "total_tokens": 0},
    }


@app.post("/_mode")
async def set_mode(req: Request):
    body = await req.json()
    STATE.update({k: v for k, v in body.items() if k in ("mode", "slow_s", "retry_after")})
    STATE["once_used"] = False
    STATE["calls"] = 0
    return STATE


@app.get("/_stats")
async def stats():
    return STATE


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    STATE["calls"] += 1
    STATE["last"] = body
    mode = STATE["mode"]
    model = body.get("model", "sahte-model")
    msgs = body.get("messages", [])
    user = "\n".join(m.get("content", "") for m in msgs if m.get("role") == "user")
    is_fix = len(msgs) > 2

    if mode == "429" or (mode == "429_once" and not STATE["once_used"]):
        STATE["once_used"] = True
        return JSONResponse(
            {"error": {"message": "rate limit", "type": "rate_limit"}},
            status_code=429,
            headers={"Retry-After": str(STATE["retry_after"])},
        )
    if mode == "500" or (mode == "500_once" and not STATE["once_used"]):
        STATE["once_used"] = True
        return JSONResponse({"error": {"message": "iç hata"}}, status_code=500)
    if mode == "slow":
        await asyncio.sleep(float(STATE["slow_s"]))

    cat, conf, reason = decide(user)
    if mode == "garbage" or (mode == "garbage_once" and not is_fix):
        return completion("Bence bu bir ürün hatası olabilir ama emin değilim.", model)
    if mode == "marker":
        text = (
            "Analiz: izdeki satırlara baktım.\n"
            "TRIAGE: <kategori> | <güven> | <gerekçe>\n"  # şablon satırı: sonuç sayılmamalı
            f"**TRIAGE: {cat} | {conf} | {reason}**"
        )
        return completion(text, model)
    return completion(
        json.dumps({"category": cat, "confidence": conf, "reason": reason}, ensure_ascii=False), model
    )
