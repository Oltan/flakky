"""Kural kararı: LLM olmadan anında verilen sınıflandırma.

Sıra: flaky → ortam kalıbı (mesaj + iz) → broken → failed → bilinmiyor.
"""

from __future__ import annotations

from .models import Category

DEFAULT_ENV_PATTERNS: list[str] = [
    # Java ağ / zaman aşımı
    "TimeoutException",
    "timed out",
    "Timed out",
    "ConnectException",
    "Connection refused",
    "UnknownHostException",
    "NoRouteToHostException",
    "SocketTimeoutException",
    # Ekran
    "Can't connect to X11",
    "cannot open display",
    "Unable to open DISPLAY",
    "HeadlessException",
    "No X11 DISPLAY",
    "Failed to initialize the display",
    # Türkçe zaman aşımı
    "zaman aşımı",
    "Zaman aşımı",
    "zaman aşımına uğradı",
    # Python
    "TimeoutError",
    "ConnectionRefusedError",
    "ConnectionResetError",
    "socket.timeout",
    "Name or service not known",
    "requests.exceptions.ConnectionError",
    "MemoryError",
]


def match_env(message: str | None, trace: str | None, patterns: list[str]) -> str | None:
    hay = f"{message or ''}\n{trace or ''}"
    for p in patterns:
        if p and p in hay:
            return p
    return None


def rule_decision(
    status: str,
    message: str | None,
    trace: str | None,
    is_flaky: bool,
    patterns: list[str],
) -> tuple[Category, str]:
    if is_flaky:
        return Category.flaky, "Aynı sürümde (commit) başarısız olup sonra geçti: flaky."
    hit = match_env(message, trace, patterns)
    if hit is not None:
        return Category.ortam, f"Ortam kalıbı eşleşti: {hit!r}."
    if status == "broken":
        return Category.test_kodu, "Durum broken: doğrulama dışı bir hata, test kodu sorunu varsayıldı."
    if status == "failed":
        return Category.urun, "Durum failed: doğrulama başarısız, ürün hatası varsayıldı."
    return Category.bilinmiyor, f"Durum {status!r} için kural yok."
