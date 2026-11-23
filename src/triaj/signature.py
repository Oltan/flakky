"""Hata imzası: aynı hatanın tekrar LLM'e gönderilmesini önlemek için.

imza = sha256(maskelenmiş mesaj + izin ilk 5 çerçevesi (satır numaraları maskeli)).
Maskeleme sırası önemlidir: UUID ve zaman damgası, genel sayı maskesinden önce uygulanır.
"""

from __future__ import annotations

import hashlib
import re

_MASKS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
        "<uuid>",
    ),
    (
        re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?\b"),
        "<ts>",
    ),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(\.\d+)?\b"), "<ts>"),
    (re.compile(r"(/tmp|/var/folders|/private/var/folders)/[^\s'\"):,]+"), "<tmp>"),
    (re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+\\AppData\\Local\\Temp\\[^\s'\"):,]+"), "<tmp>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"\b(?=[0-9a-fA-F]*\d)(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{6,}\b"), "<hex>"),
    (re.compile(r"@[0-9a-fA-F]{4,}\b"), "@<hex>"),
    (re.compile(r"\d+(\.\d+)?"), "<n>"),
]

# Bir iz satırı mı? (Java "at ...", Python 'File "..."', JS "at ... (file:line:col)")
_FRAME_RE = re.compile(r'^\s*(at\s+\S|File\s+"|\w[\w.$]*\([^)]*:\d+\))')


def mask(text: str) -> str:
    out = text
    for rx, repl in _MASKS:
        out = rx.sub(repl, out)
    return out


def frames(trace: str | None, limit: int = 5) -> list[str]:
    if not trace:
        return []
    found = [ln.strip() for ln in trace.splitlines() if _FRAME_RE.match(ln)]
    return found[:limit]


def signature(message: str | None, trace: str | None) -> str:
    msg = mask((message or "").strip())
    fr = [mask(f) for f in frames(trace)]
    if not fr and trace and not message:
        # Çerçeve bulunamadıysa izin ilk satırı mesaj gibi kullanılır.
        msg = mask(trace.strip().splitlines()[0])
    raw = msg + "\n" + "\n".join(fr)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
