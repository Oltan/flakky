"""Proje kaydı ve etkin ayarların (proje ayarı ?? sunucu varsayılanı) çözümü."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, insert, select, update

from .config import Settings
from .db import projects
from .models import ProjectSettings, ProjectSettingsPatch
from .rules import DEFAULT_ENV_PATTERNS


@dataclass(frozen=True)
class Effective:
    env_patterns: list[str]
    hints: str
    flaky_window: int
    auto_confidence: float
    llm_enabled: bool


def effective(raw: dict | None, settings: Settings) -> Effective:
    ps = ProjectSettings.model_validate(raw or {})
    patterns = list(ps.env_patterns if ps.env_patterns is not None else DEFAULT_ENV_PATTERNS)
    patterns += [p for p in ps.env_patterns_extra if p not in patterns]
    return Effective(
        env_patterns=patterns,
        hints=ps.hints,
        flaky_window=ps.flaky_window or settings.flaky_window,
        auto_confidence=ps.auto_confidence if ps.auto_confidence is not None else settings.auto_confidence,
        llm_enabled=ps.llm_enabled if ps.llm_enabled is not None else settings.llm_enabled_default,
    )


def get_by_name(conn: Connection, name: str):
    return conn.execute(select(projects).where(projects.c.name == name)).first()


def get_by_id(conn: Connection, project_id: int):
    return conn.execute(select(projects).where(projects.c.id == project_id)).first()


def get_or_create(conn: Connection, name: str):
    row = get_by_name(conn, name)
    if row is not None:
        return row
    conn.execute(insert(projects).values(name=name, settings={}))
    return get_by_name(conn, name)


def patch_settings(conn: Connection, project_id: int, patch: ProjectSettingsPatch) -> dict:
    row = get_by_id(conn, project_id)
    current = dict(row.settings or {})
    for k, v in patch.model_dump(exclude_unset=True).items():
        if v is None:
            current.pop(k, None)  # null → sunucu varsayılanına dön
        else:
            current[k] = v
    ProjectSettings.model_validate(current)
    conn.execute(update(projects).where(projects.c.id == project_id).values(settings=current))
    return current
