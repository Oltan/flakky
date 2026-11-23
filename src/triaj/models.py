"""API sözleşmesi (Pydantic v2)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Status = Literal["passed", "failed", "broken", "skipped"]
Source = Literal["rule", "llm", "admin"]
LlmState = Literal["queued", "running", "done", "failed", "skipped"]


class Category(StrEnum):
    test_kodu = "test_kodu"
    urun = "urun"
    ortam = "ortam"
    flaky = "flaky"
    bilinmiyor = "bilinmiyor"


NEXT_ACTION: dict[str, str] = {
    Category.test_kodu: "fix_test",
    Category.urun: "open_bug",
    Category.ortam: "notify",
    Category.flaky: "none",
    Category.bilinmiyor: "review",
}

MAX_MESSAGE = 20_000
MAX_TRACE = 100_000
MAX_TEXT = 50_000


# --- Girdi -----------------------------------------------------------------


class Step(BaseModel):
    name: str
    status: str | None = None


class TextIn(BaseModel):
    name: str = Field(max_length=500)
    content: str


class ContextFile(BaseModel):
    path: str = Field(max_length=1000)
    role: Literal["feature", "test", "steps", "frame", "product", "other"] = "other"
    content: str
    truncated: bool = False


class ContextIn(BaseModel):
    files: list[ContextFile] = []
    log_tail: str | None = None


class TestIn(BaseModel):
    test_key: str = Field(min_length=1, max_length=1000)
    name: str | None = Field(default=None, max_length=1000)
    suite: str | None = Field(default=None, max_length=1000)
    status: Status
    attempt: int | None = Field(default=None, ge=1)
    message: str | None = None
    trace: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    steps: list[Step] = []
    texts: list[TextIn] = []
    context: ContextIn | None = None

    @field_validator("steps", mode="before")
    @classmethod
    def _steps(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [{"name": s} if isinstance(s, str) else s for s in v]
        return v

    @model_validator(mode="after")
    def _limits(self) -> TestIn:
        if self.name is None:
            self.name = self.test_key
        if self.message and len(self.message) > MAX_MESSAGE:
            self.message = self.message[:MAX_MESSAGE] + "\n[… kırpıldı]"
        if self.trace and len(self.trace) > MAX_TRACE:
            self.trace = self.trace[:MAX_TRACE] + "\n[… kırpıldı]"
        for t in self.texts:
            if len(t.content) > MAX_TEXT:
                t.content = t.content[:MAX_TEXT] + "\n[… kırpıldı]"
        return self


class RunIn(BaseModel):
    project: str = Field(min_length=1, max_length=300)
    branch: str = Field(min_length=1, max_length=300)
    commit_sha: str | None = Field(default=None, max_length=100)
    external_id: str = Field(min_length=1, max_length=300)
    meta: dict[str, Any] = {}
    tests: list[TestIn] = []


class AdminDecisionIn(BaseModel):
    category: Category
    reason: str | None = None
    author: str = Field(min_length=1, max_length=200)


class ProjectSettings(BaseModel):
    """Proje ayarları. None olan alanlar sunucu varsayılanını kullanır."""

    env_patterns: list[str] | None = None
    env_patterns_extra: list[str] = []
    hints: str = ""
    flaky_window: int | None = Field(default=None, ge=1, le=100)
    auto_confidence: float | None = Field(default=None, ge=0.0, le=1.01)
    llm_enabled: bool | None = None

    @field_validator("env_patterns", "env_patterns_extra")
    @classmethod
    def _nonempty(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and any(not p.strip() for p in v):
            raise ValueError("kalıplar boş olamaz")
        return v


class ProjectSettingsPatch(BaseModel):
    """PUT /settings: yalnızca verilen alanlar güncellenir."""

    model_config = {"extra": "forbid"}

    env_patterns: list[str] | None = None
    env_patterns_extra: list[str] | None = None
    hints: str | None = None
    flaky_window: int | None = Field(default=None, ge=1, le=100)
    auto_confidence: float | None = Field(default=None, ge=0.0, le=1.01)
    llm_enabled: bool | None = None

    _nonempty = field_validator("env_patterns", "env_patterns_extra")(ProjectSettings._nonempty.__func__)


# --- Çıktı -----------------------------------------------------------------


class IngestOut(BaseModel):
    run_id: int
    project_id: int
    inserted: int
    duplicates: int
    warnings: list[str] = []


class DecisionOut(BaseModel):
    id: int
    category: Category
    source: Source
    reason: str | None = None
    confidence: float | None = None
    model: str | None = None
    author: str | None = None
    reused_from: int | None = None
    created_at: datetime


class Evidence(BaseModel):
    failed_run: int
    passed_run: int
    failed_run_external_id: str | None = None
    passed_run_external_id: str | None = None
    commit_sha: str


class TriageItem(BaseModel):
    result_id: int
    test_key: str
    name: str
    suite: str | None
    status: Status
    attempt: int
    message: str | None
    flaky: bool
    flaky_evidence: list[Evidence]
    decision: DecisionOut | None
    rule_decision: DecisionOut | None
    llm_suggestion: DecisionOut | None
    llm_state: LlmState | None
    next_action: str | None


class TriageOut(BaseModel):
    run_id: int
    external_id: str
    project: str
    branch: str
    commit_sha: str | None
    finished: bool
    summary: dict[str, int]
    items: list[TriageItem]


class FinishOut(BaseModel):
    run_id: int
    already_finished: bool
    remaining: int
    llm_queued: int
    llm_reused: int
    llm_skipped: int


class FlakyItem(BaseModel):
    test_key: str
    evidence: list[Evidence]


class HistoryItem(BaseModel):
    run_id: int
    external_id: str
    branch: str
    commit_sha: str | None
    result_id: int
    status: Status
    attempt: int
    category: Category | None
    source: Source | None
    created_at: datetime
