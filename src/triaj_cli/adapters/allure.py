"""Allure sonuç klasörü (allure-results) → triaj test kayıtları.

- *-result.json: test_key = historyId (yoksa testCaseId/fullName/name); aynı anahtar `start`
  sırasıyla attempt alır.
- Metin ekleri (text/*, json, xml) texts'e girer; adım ekleri de dahil.
- *-container.json befores/afters ekleri, `children` UUID'leri ile sonuca bağlanır. Container'lar
  sonuçlardan önce ya da sonra yazılmış olabilir: önce hepsi okunur, sonra eşlenir.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("triaj_cli.allure")

TEXT_TYPES = ("text/", "application/json", "application/xml", "application/x-yaml")
STATUSES = {"passed", "failed", "broken", "skipped"}


def _is_text(mime: str | None) -> bool:
    return bool(mime) and mime.startswith(TEXT_TYPES)


def _read_attachment(base: Path, att: dict, max_bytes: int) -> str | None:
    if not _is_text(att.get("type")):
        return None
    src = att.get("source")
    if not src or "/" in src or "\\" in src or src.startswith("."):
        return None
    p = base / src
    try:
        data = p.read_bytes()
    except OSError:
        return None
    cut = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    return text + ("\n[… kırpıldı]" if cut else "")


def _walk_steps(steps: list[dict], depth: int, out_steps: list, atts: list) -> None:
    for s in steps or []:
        out_steps.append({"name": ("  " * depth) + str(s.get("name", "")), "status": s.get("status")})
        for a in s.get("attachments") or []:
            atts.append((f"adım:{s.get('name', '')}", a))
        _walk_steps(s.get("steps") or [], depth + 1, out_steps, atts)


def _label(d: dict, name: str) -> str | None:
    for lb in d.get("labels") or []:
        if lb.get("name") == name:
            return lb.get("value")
    return None


def _load(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("okunamadı: %s (%s)", p.name, e)
        return None


def read_allure(directory: str | Path, max_text_kb: int = 50) -> list[dict[str, Any]]:
    base = Path(directory)
    max_bytes = max_text_kb * 1024
    results = [d for p in sorted(base.glob("*-result.json")) if (d := _load(p))]
    containers = [d for p in sorted(base.glob("*-container.json")) if (d := _load(p))]

    # child uuid → [(önek, ek)]
    fixture_atts: dict[str, list[tuple[str, dict]]] = {}
    for c in containers:
        extra: list[tuple[str, dict]] = []
        for kind in ("befores", "afters"):
            for fx in c.get(kind) or []:
                for a in fx.get("attachments") or []:
                    extra.append((f"{kind[:-1]}:{fx.get('name', '')}", a))
                fx_steps: list = []
                fx_atts: list = []
                _walk_steps(fx.get("steps") or [], 0, fx_steps, fx_atts)
                extra += [(f"{kind[:-1]}:{fx.get('name', '')}/{n}", a) for n, a in fx_atts]
        for child in c.get("children") or []:
            fixture_atts.setdefault(child, []).extend(extra)

    records: list[dict[str, Any]] = []
    for d in results:
        key = d.get("historyId") or d.get("testCaseId") or d.get("fullName") or d.get("name")
        if not key:
            continue
        status = d.get("status")
        if status not in STATUSES:
            status = "broken"
        det = d.get("statusDetails") or {}
        steps: list = []
        atts: list[tuple[str, dict]] = [("", a) for a in d.get("attachments") or []]
        _walk_steps(d.get("steps") or [], 0, steps, atts)
        fx = fixture_atts.get(d.get("uuid", ""), [])
        atts = (
            sorted((x for x in fx if x[0].startswith("before:")), key=lambda x: x[0])
            + atts
            + sorted((x for x in fx if x[0].startswith("after:")), key=lambda x: x[0])
        )
        texts = []
        for prefix, a in atts:
            content = _read_attachment(base, a, max_bytes)
            if content is None:
                continue
            name = a.get("name") or a.get("source") or "ek"
            texts.append({"name": f"{prefix}/{name}" if prefix else name, "content": content})
        start, stop = d.get("start"), d.get("stop")
        records.append(
            {
                "test_key": str(key),
                "name": d.get("name") or str(key),
                "suite": _label(d, "suite") or _label(d, "feature") or _label(d, "parentSuite"),
                "status": status,
                "message": det.get("message"),
                "trace": det.get("trace"),
                "duration_ms": (stop - start) if isinstance(start, int) and isinstance(stop, int) else None,
                "steps": steps,
                "texts": texts,
                "_start": start or 0,
                "_meta": {
                    "full_name": d.get("fullName"),
                    "feature": _label(d, "feature"),
                    "test_class": _label(d, "testClass"),
                    "package": _label(d, "package"),
                    "framework": _label(d, "framework"),
                },
            }
        )

    records.sort(key=lambda r: (r["test_key"], r["_start"]))
    counter: dict[str, int] = {}
    for r in records:
        counter[r["test_key"]] = counter.get(r["test_key"], 0) + 1
        r["attempt"] = counter[r["test_key"]]
        del r["_start"]
    return records
