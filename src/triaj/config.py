"""Ortam değişkenlerinden okunan ayarlar.

Tokenlar SecretStr olarak tutulur; repr/log çıktısında görünmez.
"""

from __future__ import annotations

import re
from datetime import time
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_HOURS_RE = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")
_DAYS_RE = re.compile(r"^([1-7])-([1-7])$")


def parse_hours(value: str) -> tuple[time, time]:
    m = _HOURS_RE.match(value.strip())
    if not m:
        raise ValueError(f"saat aralığı 'SS:DD-SS:DD' biçiminde olmalı: {value!r}")
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    start, end = time(h1, m1), time(h2, m2)
    if start >= end:
        raise ValueError(f"saat aralığı başı sonundan küçük olmalı: {value!r}")
    return start, end


def parse_days(value: str) -> set[int]:
    """'1-5' → {1..5} (ISO: Pazartesi=1). Virgüllü liste de kabul: '1,3,5'."""
    value = value.strip()
    m = _DAYS_RE.match(value)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a > b:
            raise ValueError(f"gün aralığı geçersiz: {value!r}")
        return set(range(a, b + 1))
    try:
        days = {int(x) for x in value.split(",") if x.strip()}
    except ValueError as e:
        raise ValueError(f"gün listesi geçersiz: {value!r}") from e
    if not days or not days <= set(range(1, 8)):
        raise ValueError(f"gün listesi 1-7 arasında olmalı: {value!r}")
    return days


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    database_url: str = "sqlite:///triaj.db"
    api_tokens: SecretStr = SecretStr("")

    llm_base_url: str = "http://localhost:8001/v1"
    llm_api_key: SecretStr = SecretStr("yok")
    llm_model: str = "varsayilan"
    llm_json_mode: Literal["schema", "object", "marker"] = "schema"
    llm_timeout_s: float = 300.0
    llm_max_output: int = 1024
    llm_context_budget_tokens: int = 60000
    llm_busy_hours: str = "08:30-18:00"
    llm_busy_days: str = "1-5"
    llm_busy_concurrency: int = 1
    llm_busy_gap_s: float = 5.0
    llm_idle_concurrency: int = 4
    llm_max_tries: int = 3
    llm_job_expire_h: float = 24.0
    llm_enabled_default: bool = False

    max_per_run: int = 20
    flaky_window: int = 10
    auto_confidence: float = 0.70
    tz: str = "Europe/Istanbul"

    worker_poll_s: float = 2.0

    @field_validator("llm_busy_hours")
    @classmethod
    def _v_hours(cls, v: str) -> str:
        parse_hours(v)
        return v

    @field_validator("llm_busy_days")
    @classmethod
    def _v_days(cls, v: str) -> str:
        parse_days(v)
        return v

    @property
    def tokens(self) -> set[str]:
        return {t.strip() for t in self.api_tokens.get_secret_value().split(",") if t.strip()}

    @property
    def secrets(self) -> list[str]:
        """Loglardan silinecek gizli değerler."""
        vals = list(self.tokens)
        key = self.llm_api_key.get_secret_value()
        if key and key != "yok":
            vals.append(key)
        return [v for v in vals if len(v) >= 4]

    @property
    def busy_window(self) -> tuple[time, time]:
        return parse_hours(self.llm_busy_hours)

    @property
    def busy_days(self) -> set[int]:
        return parse_days(self.llm_busy_days)


@lru_cache
def get_settings() -> Settings:
    return Settings()
