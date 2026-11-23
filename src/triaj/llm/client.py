"""OpenAI uyumlu gateway çağrısı ve yanıt ayrıştırma.

Modlar:
- schema: response_format={"type": "json_schema", ...}
- object: response_format={"type": "json_object"}
- marker: serbest metin, son "TRIAGE:" satırı
Yanıt geçersizse bir kez düzeltme istenir; yine geçersizse InvalidOutput.
Her çağrıda sert zaman aşımı vardır (kota/limitte istemci sessizce bekleyebilir).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

from ..config import Settings
from ..models import Category
from .limits import retry_after_s
from .prompt import JSON_SCHEMA


class LlmOutput(BaseModel):
    category: Category
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)


class LlmError(Exception):
    pass


class LlmTimeout(LlmError):
    pass


class LlmRateLimited(LlmError):
    def __init__(self, retry_after: float | None):
        super().__init__("429 hız sınırı")
        self.retry_after = retry_after


class LlmServerError(LlmError):
    """Tekrar denenebilir: 5xx, bağlantı hatası."""


class LlmRequestError(LlmError):
    """Tekrar denenmez: 4xx (ör. desteklenmeyen response_format)."""


class InvalidOutput(LlmError):
    pass


@dataclass
class CallInfo:
    elapsed_s: float = 0.0
    completion_tokens: int | None = None
    prompt_tokens: int | None = None
    rounds: int = 0
    model: str | None = None


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_MD_PREFIX = re.compile(r"^[\s*_\\#>\-`]+")
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _clean(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def parse_json(text: str) -> LlmOutput:
    text = _clean(text)
    candidates = [text]
    candidates += _FENCE_RE.findall(text)
    if "{" in text and "}" in text:
        candidates.append(text[text.find("{") : text.rfind("}") + 1])
    last: Exception | None = None
    for c in candidates:
        try:
            return LlmOutput.model_validate(json.loads(c))
        except (ValueError, ValidationError, TypeError) as e:
            last = e
    raise InvalidOutput(f"geçersiz JSON yanıtı: {type(last).__name__ if last else 'boş'}")


def parse_marker(text: str) -> LlmOutput:
    """Son geçerli 'TRIAGE:' satırı. Satır başındaki Markdown süsü atılır; şablon satırı sayılmaz."""
    for raw in reversed(_clean(text).splitlines()):
        line = _MD_PREFIX.sub("", raw).rstrip(" *_`")
        if not line.upper().startswith("TRIAGE:"):
            continue
        body = line[len("TRIAGE:") :].strip()
        parts = [p.strip().strip("*_`") for p in body.split("|", 2)]
        # Şablon satırı ("TRIAGE: <kategori> | <güven> | …") sonuç sayılmaz; gerekçede '<' olabilir.
        if len(parts) < 2 or "<" in parts[0] or "<" in parts[1]:
            continue
        try:
            conf = float(parts[1].replace(",", "."))
        except ValueError:
            continue
        reason = parts[2] if len(parts) > 2 and parts[2] else "(gerekçe yok)"
        try:
            return LlmOutput(category=parts[0].lower(), confidence=conf, reason=reason)
        except ValidationError:
            continue
    raise InvalidOutput("TRIAGE: satırı bulunamadı")


FIX_JSON = (
    "Yanıtın geçerli değil. Yalnızca şu alanları içeren tek bir JSON nesnesi ver, başka hiçbir şey yazma: "
    '{"category": "test_kodu|urun|ortam|flaky|bilinmiyor", "confidence": 0.0-1.0, "reason": "..."}'
)
FIX_MARKER = "Yanıtın son satırı eksik. Yalnızca tek satır ver: TRIAGE: kategori | güven | gerekçe"


def make_client(settings: Settings, http_client=None) -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        timeout=settings.llm_timeout_s,
        max_retries=0,
        http_client=http_client,
    )


def _response_format(mode: str):
    if mode == "schema":
        return {
            "type": "json_schema",
            "json_schema": {"name": "triage", "strict": True, "schema": JSON_SCHEMA},
        }
    if mode == "object":
        return {"type": "json_object"}
    return None


async def _call(client: AsyncOpenAI, settings: Settings, messages: list[dict]) -> tuple[str, object]:
    kw: dict = {
        "model": settings.llm_model,
        "messages": messages,
        "max_tokens": settings.llm_max_output,
        "temperature": 0,
    }
    rf = _response_format(settings.llm_json_mode)
    if rf is not None:
        kw["response_format"] = rf
    hard = settings.llm_timeout_s * 1.1 + 1  # istemcinin kendi zaman aşımı çalışmasa bile
    try:
        resp = await asyncio.wait_for(client.chat.completions.create(**kw), timeout=hard)
    except (TimeoutError, openai.APITimeoutError) as e:
        raise LlmTimeout(f"zaman aşımı ({settings.llm_timeout_s:g} sn)") from e
    except openai.RateLimitError as e:
        raise LlmRateLimited(retry_after_s(e.response.headers.get("retry-after"))) from e
    except openai.APIStatusError as e:
        if e.status_code >= 500:
            raise LlmServerError(f"HTTP {e.status_code}") from e
        raise LlmRequestError(f"HTTP {e.status_code}") from e
    except openai.APIConnectionError as e:
        raise LlmServerError("bağlantı hatası") from e
    if not resp.choices:
        raise InvalidOutput("boş yanıt")
    return resp.choices[0].message.content or "", resp


async def classify(
    client: AsyncOpenAI, settings: Settings, messages: list[dict]
) -> tuple[LlmOutput, CallInfo]:
    info = CallInfo()
    parse = parse_marker if settings.llm_json_mode == "marker" else parse_json
    t0 = time.monotonic()
    msgs = list(messages)
    for round_ in (1, 2):
        info.rounds = round_
        text, resp = await _call(client, settings, msgs)
        usage = getattr(resp, "usage", None)
        if usage is not None:
            info.completion_tokens = (info.completion_tokens or 0) + (usage.completion_tokens or 0)
            info.prompt_tokens = (info.prompt_tokens or 0) + (usage.prompt_tokens or 0)
        info.model = getattr(resp, "model", None)
        try:
            out = parse(text)
            info.elapsed_s = round(time.monotonic() - t0, 3)
            return out, info
        except InvalidOutput:
            if round_ == 2:
                info.elapsed_s = round(time.monotonic() - t0, 3)
                raise
            msgs = msgs + [
                {"role": "assistant", "content": text[:4000]},
                {"role": "user", "content": FIX_MARKER if settings.llm_json_mode == "marker" else FIX_JSON},
            ]
    raise AssertionError("ulaşılmaz")
