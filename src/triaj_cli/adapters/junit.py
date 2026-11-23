"""JUnit XML → triaj test kayıtları.

Desteklenen çeşitler:
- Maven surefire/failsafe: `flakyFailure`/`flakyError` (önceki başarısız denemeler, son deneme
  passed), `rerunFailure`/`rerunError` (ilk `failure`/`error`'dan sonraki başarısız denemeler).
- pytest `--junitxml`: pytest-rerunfailures aynı testcase'i tekrar yazar; sondan önceki boş
  testcase'ler başarısız deneme sayılır (ayrıntı raporda yok).
- Genel JUnit (Gradle, Jest-junit, …): testcase başına tek deneme.

test_key = "classname#name"; failure → failed, error → broken, skipped → skipped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lxml import etree

MAX_OUT = 20_000

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, recover=True)


def _text(el) -> str | None:
    if el is None:
        return None
    st = el.find("stackTrace")
    t = st.text if st is not None else el.text
    return t.strip() if t and t.strip() else None


def _msg(el) -> str | None:
    m = el.get("message")
    if m:
        return m
    t = _text(el)
    return t.splitlines()[0] if t else el.get("type")


def _attempt(status: str, el) -> dict:
    return {
        "status": status,
        "message": _msg(el) if el is not None else None,
        "trace": _text(el) if el is not None else None,
    }


def _tail(s: str | None) -> str | None:
    if not s or not s.strip():
        return None
    s = s.strip()
    return ("[… baştan kırpıldı]\n" + s[-MAX_OUT:]) if len(s) > MAX_OUT else s


def _case_attempts(tc) -> list[dict]:
    """Bir testcase öğesinden deneme listesi (eskiden yeniye)."""
    children = {c.tag: c for c in tc if isinstance(c.tag, str)}
    flaky = [c for c in tc if c.tag in ("flakyFailure", "flakyError")]
    reruns = [c for c in tc if c.tag in ("rerunFailure", "rerunError")]
    out: list[dict] = []
    if flaky:
        for c in flaky:
            out.append(_attempt("failed" if c.tag == "flakyFailure" else "broken", c))
        out.append({"status": "passed", "message": None, "trace": None})
        return out
    if "failure" in children:
        out.append(_attempt("failed", children["failure"]))
    elif "error" in children:
        out.append(_attempt("broken", children["error"]))
    elif "skipped" in children:
        sk = children["skipped"]
        out.append({"status": "skipped", "message": sk.get("message") or _text(sk), "trace": None})
    else:
        out.append({"status": "passed", "message": None, "trace": None})
    for c in reruns:
        out.append(_attempt("failed" if c.tag == "rerunFailure" else "broken", c))
    return out


def _iter_cases(root):
    suites = [root] if root.tag == "testsuite" else root.iter("testsuite")
    for s in suites:
        for tc in s.findall("testcase"):
            yield s.get("name"), tc


def read_junit(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    files = sorted(p.rglob("*.xml")) if p.is_dir() else [p]
    records: list[dict[str, Any]] = []
    for f in files:
        try:
            root = etree.parse(str(f), _PARSER).getroot()
        except (etree.XMLSyntaxError, OSError):
            continue
        if root is None or root.tag not in ("testsuite", "testsuites"):
            continue
        # Aynı dosyada aynı anahtar tekrar ediyorsa (pytest-rerunfailures) hepsi aynı testin denemeleri.
        grouped: dict[str, list[tuple[str | None, Any]]] = {}
        for suite, tc in _iter_cases(root):
            key = f"{tc.get('classname') or suite or ''}#{tc.get('name') or ''}"
            grouped.setdefault(key, []).append((suite, tc))
        for key, cases in grouped.items():
            attempts: list[dict] = []
            for i, (_suite, tc) in enumerate(cases):
                a = _case_attempts(tc)
                is_last = i == len(cases) - 1
                if not is_last and len(a) == 1 and a[0]["status"] == "passed":
                    # pytest-rerunfailures: yeniden denenen (yani başarısız) deneme, ayrıntısız.
                    a = [
                        {
                            "status": "failed",
                            "message": "yeniden denendi (ayrıntı raporda yok)",
                            "trace": None,
                        }
                    ]
                attempts += a
            suite, last = cases[-1]
            try:
                dur = int(float(last.get("time") or 0) * 1000)
            except ValueError:
                dur = None
            texts = []
            for tag in ("system-out", "system-err"):
                el = last.find(tag)
                if el is not None and (t := _tail(el.text)):
                    texts.append({"name": tag, "content": t})
            for n, a in enumerate(attempts, 1):
                records.append(
                    {
                        "test_key": key,
                        "name": last.get("name") or key,
                        "suite": suite or last.get("classname"),
                        "status": a["status"],
                        "attempt": n,
                        "message": a["message"],
                        "trace": a["trace"],
                        "duration_ms": dur if n == len(attempts) else None,
                        "steps": [],
                        "texts": texts if n == len(attempts) else [],
                        "_meta": {"classname": last.get("classname"), "file": last.get("file")},
                    }
                )
    return records
