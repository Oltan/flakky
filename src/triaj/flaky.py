"""Flaky tespiti.

Proje + branch bazında son `window` koşumda: failed|broken bir denemeden sonra aynı sürümde
(commit_sha; boşsa '') passed gelmesi — aynı sürümlü daha yeni bir koşumda ya da aynı koşumun
daha büyük attempt'inde. Tüm denemeler kullanılır (is_latest süzgeci yok).
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import Connection, text

from .models import Evidence

FLAKY_SQL = text(
    """
WITH recent AS (
    SELECT id, external_id, COALESCE(commit_sha, '') AS surum
      FROM runs
     WHERE project_id = :p AND branch = :b
     ORDER BY id DESC
     LIMIT :w
)
SELECT DISTINCT f.test_key, f.run_id AS failed_run, p.run_id AS passed_run, rf.surum,
       rf.external_id AS failed_ext, rp.external_id AS passed_ext
  FROM results f
  JOIN recent rf ON rf.id = f.run_id
  JOIN recent rp ON rp.surum = rf.surum AND rp.id >= rf.id
  JOIN results p ON p.run_id = rp.id AND p.test_key = f.test_key AND p.status = 'passed'
 WHERE f.status IN ('failed', 'broken')
   AND (p.run_id > f.run_id OR p.attempt > f.attempt)
 ORDER BY f.test_key, failed_run, passed_run
"""
)


def flaky_keys(
    conn: Connection, project_id: int, branch: str, window: int, test_keys: list[str] | None = None
) -> dict[str, list[Evidence]]:
    rows = conn.execute(FLAKY_SQL, {"p": project_id, "b": branch, "w": window}).all()
    out: dict[str, list[Evidence]] = defaultdict(list)
    wanted = set(test_keys) if test_keys is not None else None
    for r in rows:
        if wanted is not None and r.test_key not in wanted:
            continue
        out[r.test_key].append(
            Evidence(
                failed_run=r.failed_run,
                passed_run=r.passed_run,
                failed_run_external_id=r.failed_ext,
                passed_run_external_id=r.passed_ext,
                commit_sha=r.surum,
            )
        )
    return dict(out)
