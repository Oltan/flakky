"""Koşum ve sonuç yazma.

- (project, external_id) ile idempotent: aynı koşuma tekrar çağrı sonuç ekler.
- attempt verilmemişse aynı koşumda aynı test_key için sıradaki deneme numarası verilir.
  CI istemcisi tekrar gönderimlerde çift kayıt olmasın diye attempt'i her zaman açıkça gönderir.
- (run_id, test_key, attempt) zaten varsa satır atlanır (duplicates).
- is_latest her yazımdan sonra koşum için yeniden hesaplanır.
"""

from __future__ import annotations

from sqlalchemy import Connection, func, insert, select, text, update

from . import projects as proj
from .config import Settings
from .db import now, pending_context, results, runs
from .models import IngestOut, RunIn
from .signature import signature


class RunFinished(Exception):
    """Bitmiş koşuma sonuç eklenmeye çalışıldı."""


IS_LATEST_SQL = text(
    """
UPDATE results
   SET is_latest = (attempt = (SELECT MAX(r2.attempt) FROM results r2
                                WHERE r2.run_id = results.run_id
                                  AND r2.test_key = results.test_key))
 WHERE run_id = :r
"""
)


def ingest_run(conn: Connection, body: RunIn, settings: Settings) -> IngestOut:
    warnings: list[str] = []
    project = proj.get_or_create(conn, body.project)
    eff = proj.effective(project.settings, settings)
    commit = (body.commit_sha or "").strip() or None
    if commit is None:
        warnings.append("commit_sha boş: bu branch'teki commit'siz tüm koşumlar aynı sürüm sayılır.")

    run = conn.execute(
        select(runs).where(runs.c.project_id == project.id, runs.c.external_id == body.external_id)
    ).first()
    if run is None:
        run_id = conn.execute(
            insert(runs)
            .values(
                project_id=project.id,
                external_id=body.external_id,
                branch=body.branch,
                commit_sha=commit,
                meta=body.meta,
                created_at=now(),
            )
            .returning(runs.c.id)
        ).scalar_one()
    else:
        if run.finished_at is not None and body.tests:
            raise RunFinished(f"koşum {body.external_id!r} bitmiş; sonuç eklenemez")
        run_id = run.id
        if run.branch != body.branch or (run.commit_sha or None) != commit:
            warnings.append("Koşum zaten var; branch/commit_sha ilk yüklemedeki değerlerle kalır.")

    existing = conn.execute(
        select(results.c.test_key, results.c.attempt).where(results.c.run_id == run_id)
    ).all()
    seen: set[tuple[str, int]] = {(r.test_key, r.attempt) for r in existing}
    max_attempt: dict[str, int] = {}
    for key, att in seen:
        max_attempt[key] = max(att, max_attempt.get(key, 0))

    inserted = duplicates = 0
    for t in body.tests:
        attempt = t.attempt if t.attempt is not None else max_attempt.get(t.test_key, 0) + 1
        if (t.test_key, attempt) in seen:
            duplicates += 1
            continue
        seen.add((t.test_key, attempt))
        max_attempt[t.test_key] = max(attempt, max_attempt.get(t.test_key, 0))
        sig = signature(t.message, t.trace) if t.status in ("failed", "broken") else None
        rid = conn.execute(
            insert(results)
            .values(
                run_id=run_id,
                test_key=t.test_key,
                name=t.name or t.test_key,
                suite=t.suite,
                status=t.status,
                attempt=attempt,
                is_latest=True,
                message=t.message,
                trace=t.trace,
                duration_ms=t.duration_ms,
                steps=[s.model_dump(exclude_none=True) for s in t.steps],
                texts=[x.model_dump() for x in t.texts],
                signature=sig,
                created_at=now(),
            )
            .returning(results.c.id)
        ).scalar_one()
        inserted += 1
        if (
            eff.llm_enabled
            and t.context is not None
            and t.status in ("failed", "broken")
            and (t.context.files or t.context.log_tail)
        ):
            conn.execute(insert(pending_context).values(result_id=rid, payload=t.context.model_dump()))

    if inserted:
        conn.execute(IS_LATEST_SQL, {"r": run_id})

    return IngestOut(
        run_id=run_id,
        project_id=project.id,
        inserted=inserted,
        duplicates=duplicates,
        warnings=warnings,
    )


def count_results(conn: Connection, run_id: int) -> int:
    return conn.execute(select(func.count()).where(results.c.run_id == run_id)).scalar_one()


def mark_finished(conn: Connection, run_id: int) -> bool:
    """True: bu çağrı bitirdi; False: zaten bitmişti."""
    res = conn.execute(
        update(runs).where(runs.c.id == run_id, runs.c.finished_at.is_(None)).values(finished_at=now())
    )
    return res.rowcount == 1
