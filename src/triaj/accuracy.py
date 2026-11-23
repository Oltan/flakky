"""LLM isabet ölçümü: admin kararı olan sonuçlarda, admin'den önceki son LLM kararı ile karşılaştırma.

Kural kararı da aynı şekilde ölçülür (LLM'in kurala göre katkısını görmek için).
Güven aralıklarına göre isabet, otomatik eşik (auto_confidence) seçimine yardım eder.
"""

from __future__ import annotations

from sqlalchemy import Connection, text

from .models import Category

CATS = [c.value for c in Category]
BUCKETS = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]

PAIRS_SQL = text(
    """
WITH adm AS (
    SELECT d.result_id, MAX(d.id) AS admin_id
      FROM decisions d
      JOIN results r ON r.id = d.result_id
      JOIN runs ru ON ru.id = r.run_id
     WHERE d.source = 'admin' AND ru.project_id = :p
     GROUP BY d.result_id
)
SELECT a.result_id,
       ad.category AS admin_cat,
       (SELECT l.category FROM decisions l WHERE l.result_id = a.result_id AND l.source = 'llm'
           AND l.id < a.admin_id ORDER BY l.id DESC LIMIT 1) AS llm_cat,
       (SELECT l.confidence FROM decisions l WHERE l.result_id = a.result_id AND l.source = 'llm'
           AND l.id < a.admin_id ORDER BY l.id DESC LIMIT 1) AS llm_conf,
       (SELECT rl.category FROM decisions rl WHERE rl.result_id = a.result_id AND rl.source = 'rule'
           AND rl.id < a.admin_id ORDER BY rl.id DESC LIMIT 1) AS rule_cat
  FROM adm a
  JOIN decisions ad ON ad.id = a.admin_id
"""
)


def _metrics(pairs: list[tuple[str, str]]) -> dict:
    matrix = {a: {p: 0 for p in CATS} for a in CATS}
    for actual, pred in pairs:
        matrix[actual][pred] += 1
    total = len(pairs)
    correct = sum(matrix[c][c] for c in CATS)
    per = {}
    for c in CATS:
        tp = matrix[c][c]
        pred_n = sum(matrix[a][c] for a in CATS)
        act_n = sum(matrix[c].values())
        per[c] = {
            "precision": round(tp / pred_n, 3) if pred_n else None,
            "recall": round(tp / act_n, 3) if act_n else None,
            "support": act_n,
        }
    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 3) if total else None,
        "matrix": matrix,  # matrix[admin][tahmin]
        "per_category": per,
    }


def llm_accuracy(conn: Connection, project_id: int) -> dict:
    rows = conn.execute(PAIRS_SQL, {"p": project_id}).all()
    llm_pairs = [(r.admin_cat, r.llm_cat) for r in rows if r.llm_cat]
    rule_pairs = [(r.admin_cat, r.rule_cat) for r in rows if r.rule_cat]
    buckets = []
    for lo, hi in BUCKETS:
        sel = [r for r in rows if r.llm_cat and r.llm_conf is not None and lo <= r.llm_conf < hi]
        ok = sum(1 for r in sel if r.llm_cat == r.admin_cat)
        buckets.append(
            {
                "min": lo,
                "max": min(hi, 1.0),
                "n": len(sel),
                "accuracy": round(ok / len(sel), 3) if sel else None,
            }
        )
    return {
        "admin_decisions": len(rows),
        "llm": _metrics(llm_pairs),
        "rule": _metrics(rule_pairs),
        "llm_by_confidence": buckets,
    }
