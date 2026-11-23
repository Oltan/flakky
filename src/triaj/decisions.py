"""Kararlar: yalnızca eklenir, silinmez.

Geçerli karar önceliği: admin > LLM (güven ≥ auto_confidence) > kural; her kaynakta en yenisi.
Geçerli karar sonuç satırına da yazılır (refresh).
"""

from __future__ import annotations

from sqlalchemy import Connection, insert, select, update

from .db import decisions, now, results
from .models import Category, DecisionOut

MAX_REASON = 2000


def _row_to_out(row) -> DecisionOut:
    return DecisionOut.model_validate(row._mapping)


def add(
    conn: Connection,
    result_id: int,
    category: Category | str,
    source: str,
    reason: str | None,
    *,
    auto_confidence: float,
    confidence: float | None = None,
    model: str | None = None,
    author: str | None = None,
    reused_from: int | None = None,
) -> int:
    category = Category(category)
    if reason is not None and len(reason) > MAX_REASON:
        reason = reason[:MAX_REASON]
    res = conn.execute(
        insert(decisions)
        .values(
            result_id=result_id,
            category=category.value,
            source=source,
            reason=reason,
            confidence=confidence,
            model=model,
            author=author,
            reused_from=reused_from,
            created_at=now(),
        )
        .returning(decisions.c.id)
    )
    did = res.scalar_one()
    refresh(conn, result_id, auto_confidence)
    return did


def history(conn: Connection, result_id: int) -> list[DecisionOut]:
    rows = conn.execute(
        select(decisions).where(decisions.c.result_id == result_id).order_by(decisions.c.id.desc())
    ).all()
    return [_row_to_out(r) for r in rows]


def pick_current(hist: list[DecisionOut], auto_confidence: float) -> DecisionOut | None:
    """hist yeniden eskiye sıralı olmalı."""
    for d in hist:
        if d.source == "admin":
            return d
    for d in hist:
        if d.source == "llm" and d.confidence is not None and d.confidence >= auto_confidence:
            return d
    for d in hist:
        if d.source == "rule":
            return d
    return None


def latest_of(hist: list[DecisionOut], source: str) -> DecisionOut | None:
    return next((d for d in hist if d.source == source), None)


def current(conn: Connection, result_id: int, auto_confidence: float) -> DecisionOut | None:
    return pick_current(history(conn, result_id), auto_confidence)


def refresh(conn: Connection, result_id: int, auto_confidence: float) -> DecisionOut | None:
    cur = current(conn, result_id, auto_confidence)
    conn.execute(
        update(results)
        .where(results.c.id == result_id)
        .values(
            current_category=cur.category.value if cur else None,
            current_source=cur.source if cur else None,
        )
    )
    return cur
