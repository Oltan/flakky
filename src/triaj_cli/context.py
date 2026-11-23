"""Başarısız testler için depodan ilgili dosya parçalarını toplar (--context).

- İz çerçeveleri depodaki dosyalara eşlenir; satırın ±80 satırı alınır:
  Java/Kotlin `at a.b.C.m(C.java:42)` → **/a/b/C.java; Python `File "x.py", line N` ve pytest
  `tests/x.py:22: AssertionError`; Cucumber `(classpath:ozellik/a.feature:16)`.
- Cucumber: Allure fullName'den feature dosyası + adım metniyle eşleşen @Given/@When/@Then/@And/@But
  adım tanımı (Cucumber Expressions ve regex).
- src/main gibi ürün yolları dahil edilir ama role="product" ile işaretlenir.
- Test başına üst sınır (--context-max-kb); dosya okumaları önbelleklenir.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "target",
    "build",
    "dist",
    "out",
    ".gradle",
    ".idea",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    "allure-results",
    "allure-report",
    "site-packages",
}
SOURCE_EXT = {".java", ".kt", ".groovy", ".scala", ".py", ".feature", ".js", ".ts", ".cs", ".rb", ".go"}
STEP_EXT = {".java", ".kt", ".groovy", ".scala", ".py", ".js", ".ts", ".rb"}
MAX_INDEX = 200_000
MAX_FILE_BYTES = 2_000_000
FRAME_RADIUS = 80
STEP_RADIUS = 40

JAVA_FRAME = re.compile(r"at\s+([\w$.]+)\.[\w$<>]+\(([\w$]+\.(?:java|kt|groovy|scala)):(\d+)\)")
PY_FRAME = re.compile(r'File "([^"]+\.py)", line (\d+)')
PYTEST_FRAME = re.compile(r"^([\w./\\-]+\.py):(\d+): ", re.MULTILINE)
FEATURE_FRAME = re.compile(r"\((?:classpath:|file:(?://)?)?([^():]+\.feature):(\d+)\)")
FULLNAME_FEATURE = re.compile(r"^(?:classpath:|file:(?://)?)?(.+\.feature)(?::(\d+))?$")

STEP_KEYWORDS = re.compile(
    r"^\s*(Given|When|Then|And|But|\*|Diyelim ki|Eğer ki|Eğer|O zaman|Ve|Fakat|Ama)\s+", re.IGNORECASE
)
STEP_ANNOT = re.compile(
    r"@(?:Given|When|Then|And|But|Diyelimki|Eğerki|Ozaman|Ve|Fakat|given|when|then|step)\s*\(\s*"
    r"(?:value\s*=\s*)?(?:u|r)?(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')"
)
JAVA_IMPORT = re.compile(r"^\s*import\s+(?!static)([\w.]+)\s*;", re.MULTILINE)
PY_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE)
MAX_IMPORTS = 5
TEST_PATH = re.compile(
    r"(^|/)(src/test|src/it|tests?|spec|features?|steps?|stepdefs?|adimlar)/|(_test|Test|Tests|IT|Steps?)\.\w+$"
    r"|(^|/)test_[^/]+\.py$"
)


def role_for(path: str) -> str:
    if path.endswith(".feature"):
        return "feature"
    if "src/main/" in path or (not TEST_PATH.search(path) and "/test" not in path):
        return "product"
    return "frame"


def cucumber_regex(expr: str) -> re.Pattern[str] | None:
    """Cucumber Expression ya da düz regex → derlenmiş regex."""
    if expr.startswith("^") or expr.endswith("$"):
        try:
            return re.compile(expr)
        except re.error:
            return None
    out = []
    i = 0
    params = {
        "int": r"-?\d+",
        "float": r"-?\d*[.,]?\d+",
        "double": r"-?\d*[.,]?\d+",
        "long": r"-?\d+",
        "byte": r"-?\d+",
        "short": r"-?\d+",
        "bigdecimal": r"-?\d*[.,]?\d+",
        "biginteger": r"-?\d+",
        "word": r"\S+",
        "string": r"(?:\"[^\"]*\"|'[^']*')",
        "": r".*",
    }
    tokens = re.split(r"(\{[^}]*\}|\([^)]*\)|\S+/\S+)", expr)
    for tok in tokens:
        if not tok:
            continue
        if tok.startswith("{") and tok.endswith("}"):
            out.append(params.get(tok[1:-1].lower(), r".+?"))
        elif tok.startswith("(") and tok.endswith(")"):
            out.append(f"(?:{re.escape(tok[1:-1])})?")
        elif "/" in tok and not tok.startswith("/") and not tok.endswith("/"):
            out.append("(?:" + "|".join(re.escape(a) for a in tok.split("/")) + ")")
        else:
            out.append(re.escape(tok))
        i += 1
    try:
        return re.compile("^" + "".join(out) + "$")
    except re.error:
        return None


def _unquote(s: str) -> str:
    q = s[0]
    body = s[1:-1]
    return body.replace("\\" + q, q).replace("\\\\", "\\")


@dataclass
class StepDef:
    expr: str
    rx: re.Pattern[str]
    path: str
    line: int


class Repo:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._cache: dict[str, list[str] | None] = {}

    @cached_property
    def files(self) -> list[str]:
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for f in filenames:
                if os.path.splitext(f)[1] in SOURCE_EXT:
                    rel = os.path.relpath(os.path.join(dirpath, f), self.root).replace(os.sep, "/")
                    out.append(rel)
                    if len(out) >= MAX_INDEX:
                        return out
        return out

    def find_suffix(self, suffix: str) -> list[str]:
        suffix = suffix.lstrip("/")
        return [f for f in self.files if f == suffix or f.endswith("/" + suffix)]

    def lines(self, rel: str) -> list[str] | None:
        if rel not in self._cache:
            p = self.root / rel
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    self._cache[rel] = None
                else:
                    self._cache[rel] = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                self._cache[rel] = None
        return self._cache[rel]

    def rel_from_any(self, path: str) -> str | None:
        """Mutlak ya da göreli yolu depodaki dosyaya eşler."""
        path = path.replace("\\", "/")
        if "site-packages/" in path or "/lib/python" in path:
            return None
        try:
            ap = Path(path)
            if ap.is_absolute():
                rel = ap.resolve().relative_to(self.root).as_posix()
                return rel if rel in set(self.files) else None
        except (ValueError, OSError):
            pass
        cands = self.find_suffix(path)
        if len(cands) == 1:
            return cands[0]
        # Birden çok aday: en kısa eşleşme.
        return min(cands, key=len) if cands else None

    @cached_property
    def step_defs(self) -> list[StepDef]:
        defs: list[StepDef] = []
        for rel in self.files:
            if os.path.splitext(rel)[1] not in STEP_EXT:
                continue
            lines = self.lines(rel)
            if not lines:
                continue
            text = "\n".join(lines)
            if "@" not in text:
                continue
            for m in STEP_ANNOT.finditer(text):
                expr = _unquote(m.group(1))
                rx = cucumber_regex(expr)
                if rx is not None:
                    defs.append(StepDef(expr, rx, rel, text.count("\n", 0, m.start()) + 1))
        return defs


def snippet(lines: list[str], centers: list[int], radius: int) -> str:
    """Merkez satırların ±radius penceresi; satır numaralı, hata satırı '>>' ile işaretli."""
    keep: set[int] = set()
    for c in centers:
        keep.update(range(max(1, c - radius), min(len(lines), c + radius) + 1))
    out: list[str] = []
    prev = 0
    for n in sorted(keep):
        if prev and n != prev + 1:
            out.append("…")
        mark = ">>" if n in centers else "  "
        out.append(f"{mark}{n:5d}| {lines[n - 1]}")
        prev = n
    return "\n".join(out)


def frames_in(trace: str) -> list[tuple[str, str, int]]:
    """(tür, yol ipucu, satır) — izdeki sırayla."""
    found: list[tuple[int, tuple[str, str, int]]] = []
    for m in JAVA_FRAME.finditer(trace):
        cls, fname, line = m.group(1), m.group(2), int(m.group(3))
        pkg = cls.split("$")[0].rsplit(".", 1)[0] if "." in cls else ""
        hint = (pkg.replace(".", "/") + "/" if pkg else "") + fname
        found.append((m.start(), ("java", hint, line)))
    for m in PY_FRAME.finditer(trace):
        found.append((m.start(), ("py", m.group(1), int(m.group(2)))))
    for m in PYTEST_FRAME.finditer(trace):
        found.append((m.start(), ("py", m.group(1), int(m.group(2)))))
    for m in FEATURE_FRAME.finditer(trace):
        found.append((m.start(), ("feature", m.group(1), int(m.group(2)))))
    found.sort(key=lambda x: x[0])
    return [f for _, f in found]


def imported_files(rel: str, repo: Repo) -> list[str]:
    lines = repo.lines(rel)
    if not lines:
        return []
    text = "\n".join(lines[:300])
    out: list[str] = []
    if rel.endswith((".java", ".kt", ".groovy", ".scala")):
        for m in JAVA_IMPORT.finditer(text):
            base = m.group(1).replace(".", "/")
            for ext in (".java", ".kt", ".groovy", ".scala"):
                cands = repo.find_suffix(base + ext)
                if cands:
                    out.append(min(cands, key=len))
                    break
    elif rel.endswith(".py"):
        here = rel.rsplit("/", 1)[0] if "/" in rel else ""
        for m in PY_IMPORT.finditer(text):
            mod = (m.group(1) or m.group(2)).replace(".", "/")
            for cand in (f"{here}/{mod}.py" if here else f"{mod}.py", f"{mod}.py", f"{mod}/__init__.py"):
                if cand in set(repo.files):
                    out.append(cand)
                    break
    return [o for o in dict.fromkeys(out) if o != rel]


def step_text(name: str) -> str:
    return STEP_KEYWORDS.sub("", name.strip(), count=1).strip()


def collect_for(rec: dict, repo: Repo, max_kb: int) -> list[dict]:
    budget = max_kb * 1024
    # yol → (rol, merkez satırlar, yarıçap); ekleme sırası öncelik sırasıdır.
    wanted: dict[str, tuple[str, list[int], int]] = {}

    def want(rel: str, role: str, line: int | None, radius: int) -> None:
        if rel in wanted:
            r, centers, rad = wanted[rel]
            if line and line not in centers:
                centers.append(line)
            return
        wanted[rel] = (role, [line] if line else [], radius)

    meta = rec.get("_meta") or {}
    # 1) feature dosyası
    fn = meta.get("full_name") or ""
    m = FULLNAME_FEATURE.match(fn)
    if m:
        rel = repo.rel_from_any(m.group(1))
        if rel:
            want(rel, "feature", int(m.group(2)) if m.group(2) else None, FRAME_RADIUS)

    # 2) iz çerçeveleri
    for kind, hint, line in frames_in(rec.get("trace") or ""):
        rel = repo.rel_from_any(hint)
        if not rel:
            continue
        role = "feature" if kind == "feature" else role_for(rel)
        want(rel, role, line, FRAME_RADIUS)

    # 3) adım tanımları (önce başarısız adım)
    steps = rec.get("steps") or []
    if steps and repo.step_defs:
        ordered = sorted(steps, key=lambda s: 0 if s.get("status") in ("failed", "broken") else 1)
        for s in ordered:
            txt = step_text(s.get("name") or "")
            for d in repo.step_defs:
                if d.rx.match(txt):
                    want(d.path, "steps", d.line, STEP_RADIUS)
                    break

    # 4) test/adım dosyalarının depo içi importları (ör. test edilen ürün sınıfı), merkezsiz
    for rel, (role, _c, _r) in list(wanted.items()):
        if role not in ("frame", "steps"):
            continue
        for imp in imported_files(rel, repo)[:MAX_IMPORTS]:
            if imp not in wanted:
                want(imp, role_for(imp), None, FRAME_RADIUS)

    files: list[dict] = []
    used = 0
    priority = {"feature": 0, "frame": 1, "steps": 2, "product": 3, "other": 4}
    for rel, (role, centers, radius) in sorted(wanted.items(), key=lambda kv: priority.get(kv[1][0], 9)):
        lines = repo.lines(rel)
        if not lines:
            continue
        if role == "feature" and len(lines) <= 2 * FRAME_RADIUS:
            content = "\n".join(
                f"{'>>' if n in centers else '  '}{n:5d}| {ln}" for n, ln in enumerate(lines, 1)
            )
        elif centers:
            content = snippet(lines, centers, radius)
        else:
            content = "\n".join(lines[: 2 * radius])
        truncated = False
        size = len(content.encode("utf-8"))
        if used + size > budget:
            room = budget - used
            if room < 1024:
                break
            content = content.encode("utf-8")[:room].decode("utf-8", errors="ignore") + "\n[… kırpıldı]"
            truncated = True
        files.append({"path": rel, "role": role, "content": content, "truncated": truncated})
        used += len(content.encode("utf-8"))
        if truncated:
            break
    return files


def attach_context(records: list[dict], repo_root: Path, max_kb: int = 200) -> int:
    """Her test_key'in son denemesi failed|broken ise context ekler. Eklenen test sayısını döner."""
    repo = Repo(repo_root)
    last: dict[str, dict] = {}
    for r in records:
        if r["test_key"] not in last or r["attempt"] >= last[r["test_key"]]["attempt"]:
            last[r["test_key"]] = r
    n = 0
    for r in last.values():
        if r["status"] not in ("failed", "broken"):
            continue
        files = collect_for(r, repo, max_kb)
        if files:
            r["context"] = {"files": files}
            n += 1
    return n
