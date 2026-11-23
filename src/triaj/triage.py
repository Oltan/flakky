"""Koşum bitirme (kural kararları + LLM kuyruğu) ve okuma uçlarının iş mantığı."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import Connection, delete, insert, select, text

from . import decisions as dec
from . import projects as proj
from .config import Settings
from .db import decisions, llm_jobs, now, pending_context, results, runs
from .flaky import flaky_keys
from .ingest import mark_finished
from .models import (
    NEXT_ACTION,
    Category,
    FinishOut,
    FlakyItem,
    HistoryItem,
    TriageItem,
    TriageOut,
)
from .rules import rule_decision


class NotFound(Exception):
    pass


def get_run(conn: Connection, run_id: int):
    run = conn.execute(select(runs).where(runs.c.id == run_id)).first()
    if run is None:
        raise NotFound(f"koşum {run_id} yok")
    return run


def remaining_results(conn: Connection, run_id: int):
    """Kalan sonuç: koşumdaki son deneme ve durumu failed|broken."""
    return conn.execute(
        select(results)
        .where(
            results.c.run_id == run_id,
            results.c.is_latest.is_(True),
            results.c.status.in_(("failed", "broken")),
        )
        .order_by(results.c.id)
    ).all()


REUSE_SQL = text(
    """
SELECT d.id, COALESCE(d.reused_from, d.id) AS origin, d.category, d.reason, d.confidence, d.model
  FROM decisions d
  JOIN results r ON r.id = d.result_id
  JOIN runs ru ON ru.id = r.run_id
 WHERE ru.project_id = :p
   AND r.test_key = :k
   AND COALESCE(ru.commit_sha, '') = :c
   AND r.signature = :s
   AND r.id <> :rid
   AND d.source = 'llm'
 ORDER BY d.id DESC
 LIMIT 1
"""
)


def finish_run(conn: Connection, run_id: int, settings: Settings) -> FinishOut:
    run = get_run(conn, run_id)
    project = proj.get_by_id(conn, run.project_id)
    eff = proj.effective(project.settings, settings)
    rem = remaining_results(conn, run_id)

    if not mark_finished(conn, run_id):
        return FinishOut(
            run_id=run_id,
            already_finished=True,
            remaining=len(rem),
            llm_queued=0,
            llm_reused=0,
            llm_skipped=0,
        )

    flaky = flaky_keys(conn, run.project_id, run.branch, eff.flaky_window, [r.test_key for r in rem])
    rule_cats: dict[int, Category] = {}
    for r in rem:
        cat, reason = rule_decision(r.status, r.message, r.trace, r.test_key in flaky, eff.env_patterns)
        rule_cats[r.id] = cat
        dec.add(conn, r.id, cat, "rule", reason, auto_confidence=eff.auto_confidence)

    queued = reused = skipped = 0
    if eff.llm_enabled and rem:
        ctx = {
            row.result_id: row.payload
            for row in conn.execute(
                select(pending_context).where(pending_context.c.result_id.in_([r.id for r in rem]))
            )
        }
        # Ortam ve flaky dışındakiler önce: LLM'in en çok katkı vereceği sonuçlar.
        order = sorted(rem, key=lambda r: (rule_cats[r.id] in (Category.ortam, Category.flaky), r.id))
        ts = now()
        for r in order:
            prev = None
            if r.signature:
                prev = conn.execute(
                    REUSE_SQL,
                    {
                        "p": run.project_id,
                        "k": r.test_key,
                        "c": run.commit_sha or "",
                        "s": r.signature,
                        "rid": r.id,
                    },
                ).first()
            if prev is not None:
                dec.add(
                    conn,
                    r.id,
                    prev.category,
                    "llm",
                    prev.reason,
                    auto_confidence=eff.auto_confidence,
                    confidence=prev.confidence,
                    model=prev.model,
                    reused_from=prev.origin,
                )
                state, payload, meta = "done", None, {"reused_from": prev.origin}
                reused += 1
            elif queued < settings.max_per_run:
                state, payload, meta = "queued", {"context": ctx.get(r.id)}, {}
                queued += 1
            else:
                state, payload, meta = "skipped", None, {"reason": "max_per_run"}
                skipped += 1
            conn.execute(
                insert(llm_jobs).values(
                    result_id=r.id,
                    state=state,
                    tries=0,
                    next_at=ts,
                    payload=payload,
                    meta=meta,
                    created_at=ts,
                    updated_at=ts,
                )
            )

    # Bağlam artık iş payload'ında (ya da gereksiz): geçici kayıtlar silinir.
    run_result_ids = select(results.c.id).where(results.c.run_id == run_id).scalar_subquery()
    conn.execute(delete(pending_context).where(pending_context.c.result_id.in_(run_result_ids)))

    return FinishOut(
        run_id=run_id,
        already_finished=False,
        remaining=len(rem),
        llm_queued=queued,
        llm_reused=reused,
        llm_skipped=skipped,
    )


def get_triage(conn: Connection, run_id: int, settings: Settings) -> TriageOut:
    run = get_run(conn, run_id)
    project = proj.get_by_id(conn, run.project_id)
    eff = proj.effective(project.settings, settings)
    rem = remaining_results(conn, run_id)
    ids = [r.id for r in rem]
    flaky = flaky_keys(conn, run.project_id, run.branch, eff.flaky_window, [r.test_key for r in rem])

    hist: dict[int, list] = defaultdict(list)
    jobs: dict[int, str] = {}
    if ids:
        for d in conn.execute(
            select(decisions).where(decisions.c.result_id.in_(ids)).order_by(decisions.c.id.desc())
        ):
            hist[d.result_id].append(dec._row_to_out(d))
        for j in conn.execute(
            select(llm_jobs.c.result_id, llm_jobs.c.state).where(llm_jobs.c.result_id.in_(ids))
        ):
            jobs[j.result_id] = j.state

    items: list[TriageItem] = []
    summary: dict[str, int] = defaultdict(int)
    for r in rem:
        h = hist[r.id]
        cur = dec.pick_current(h, eff.auto_confidence)
        llm_latest = dec.latest_of(h, "llm")
        suggestion = (
            llm_latest if llm_latest is not None and (cur is None or cur.id != llm_latest.id) else None
        )
        if cur is not None:
            summary[cur.category.value] += 1
        else:
            summary["kararsiz"] += 1
        items.append(
            TriageItem(
                result_id=r.id,
                test_key=r.test_key,
                name=r.name,
                suite=r.suite,
                status=r.status,
                attempt=r.attempt,
                message=r.message,
                flaky=r.test_key in flaky,
                flaky_evidence=flaky.get(r.test_key, []),
                decision=cur,
                rule_decision=dec.latest_of(h, "rule"),
                llm_suggestion=suggestion,
                llm_state=jobs.get(r.id),
                next_action=NEXT_ACTION[cur.category] if cur else None,
            )
        )
    return TriageOut(
        run_id=run.id,
        external_id=run.external_id,
        project=project.name,
        branch=run.branch,
        commit_sha=run.commit_sha,
        finished=run.finished_at is not None,
        summary=dict(summary),
        items=items,
    )


def flaky_list(conn: Connection, project_name: str, branch: str, settings: Settings) -> list[FlakyItem]:
    project = proj.get_by_name(conn, project_name)
    if project is None:
        raise NotFound(f"proje {project_name!r} yok")
    eff = proj.effective(project.settings, settings)
    found = flaky_keys(conn, project.id, branch, eff.flaky_window)
    return [FlakyItem(test_key=k, evidence=v) for k, v in sorted(found.items())]


def test_history(
    conn: Connection, project_name: str, test_key: str, branch: str | None, limit: int
) -> list[HistoryItem]:
    project = proj.get_by_name(conn, project_name)
    if project is None:
        raise NotFound(f"proje {project_name!r} yok")
    q = (
        select(
            runs.c.id.label("run_id"),
            runs.c.external_id,
            runs.c.branch,
            runs.c.commit_sha,
            results.c.id.label("result_id"),
            results.c.status,
            results.c.attempt,
            results.c.current_category.label("category"),
            results.c.current_source.label("source"),
            results.c.created_at,
        )
        .select_from(results.join(runs, runs.c.id == results.c.run_id))
        .where(runs.c.project_id == project.id, results.c.test_key == test_key, results.c.is_latest.is_(True))
        .order_by(runs.c.id.desc())
        .limit(limit)
    )
    if branch:
        q = q.where(runs.c.branch == branch)
    return [HistoryItem.model_validate(r._mapping) for r in conn.execute(q)]


def result_auto_confidence(conn: Connection, result_id: int, settings: Settings) -> float:
    row = conn.execute(
        select(results.c.id, runs.c.project_id)
        .select_from(results.join(runs, runs.c.id == results.c.run_id))
        .where(results.c.id == result_id)
    ).first()
    if row is None:
        raise NotFound(f"sonuç {result_id} yok")
    project = proj.get_by_id(conn, row.project_id)
    return proj.effective(project.settings, settings).auto_confidence
