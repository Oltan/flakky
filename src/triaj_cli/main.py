"""triaj CI istemcisi.

    triaj upload --format allure --dir build/allure-results [--context --repo .]
    triaj finish
    triaj wait --timeout 600

Proje/branch/commit/koşum GitLab değişkenlerinden okunur (CI_PROJECT_PATH, CI_COMMIT_REF_NAME,
CI_COMMIT_SHA, CI_JOB_ID), bayrakla ezilebilir. Sunucu yoksa ya da hata dönerse uyarı basılır ve
çıkış kodu 0 olur (CI kırılmaz); --strict ile 1.
"""

from __future__ import annotations

import json
import os
import sys
import time
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from .adapters.allure import read_allure
from .adapters.junit import read_junit
from .client import Client, TriajError

app = typer.Typer(add_completion=False, no_args_is_help=True, help="triaj CI istemcisi")

STATE_FILE = ".triaj-run.json"
CHUNK = 500


class Format(StrEnum):
    allure = "allure"
    junit = "junit"


def _env(*names: str) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


def warn(msg: str) -> None:
    typer.echo(f"triaj: UYARI: {msg}", err=True)


def _fail(msg: str, strict: bool) -> None:
    warn(msg)
    raise typer.Exit(1 if strict else 0)


Url = Annotated[str | None, typer.Option(envvar="TRIAJ_URL", help="Servis adresi")]
Token = Annotated[
    str | None, typer.Option(envvar="TRIAJ_TOKEN", help="API token (env önerilir)", show_default=False)
]
Strict = Annotated[bool, typer.Option(help="Hata durumunda çıkış kodu 1")]


def _client(url: str | None, token: str | None, strict: bool) -> Client:
    if not url or not token:
        _fail("TRIAJ_URL ve TRIAJ_TOKEN gerekli; yükleme atlandı", strict)
    return Client(url, token)


def _save_state(run_id: int, external_id: str) -> None:
    try:
        Path(STATE_FILE).write_text(json.dumps({"run_id": run_id, "external_id": external_id}))
    except OSError:
        pass


def _load_run_id(run_id: int | None) -> int | None:
    if run_id:
        return run_id
    try:
        return json.loads(Path(STATE_FILE).read_text())["run_id"]
    except (OSError, ValueError, KeyError):
        return None


def build_meta() -> dict:
    keys = {
        "pipeline_id": "CI_PIPELINE_ID",
        "pipeline_url": "CI_PIPELINE_URL",
        "pipeline_created_at": "CI_PIPELINE_CREATED_AT",
        "job_id": "CI_JOB_ID",
        "job_name": "CI_JOB_NAME",
        "job_url": "CI_JOB_URL",
        "merge_request_iid": "CI_MERGE_REQUEST_IID",
    }
    return {k: v for k, e in keys.items() if (v := os.environ.get(e))}


def collect(fmt: Format, dirs: list[Path]) -> list[dict]:
    records: list[dict] = []
    for d in dirs:
        if not d.exists():
            warn(f"bulunamadı: {d}")
            continue
        records += read_allure(d) if fmt == Format.allure else read_junit(d)
    return records


@app.command()
def upload(
    dirs: Annotated[list[Path], typer.Option("--dir", help="Sonuç klasörü/dosyası (birden çok verilebilir)")],
    fmt: Annotated[Format, typer.Option("--format")] = Format.allure,
    project: Annotated[str | None, typer.Option(envvar="CI_PROJECT_PATH")] = None,
    branch: Annotated[str | None, typer.Option(envvar="CI_COMMIT_REF_NAME")] = None,
    commit: Annotated[str | None, typer.Option(envvar="CI_COMMIT_SHA")] = None,
    run: Annotated[
        str | None, typer.Option("--run", envvar="CI_JOB_ID", help="Koşum kimliği (external_id)")
    ] = None,
    context: Annotated[bool, typer.Option(help="Başarısız testler için depodan bağlam topla")] = False,
    repo: Annotated[Path, typer.Option(help="Depo kökü (--context için)")] = Path("."),
    context_max_kb: Annotated[int, typer.Option(help="Test başına bağlam üst sınırı")] = 200,
    finish: Annotated[bool, typer.Option(help="Yüklemeden sonra koşumu bitir")] = False,
    dry_run: Annotated[bool, typer.Option(help="Göndermeden JSON'u stdout'a bas")] = False,
    url: Url = None,
    token: Token = None,
    strict: Strict = False,
) -> None:
    """Test sonuçlarını yükler."""
    records = collect(fmt, dirs)
    if not records:
        _fail("yüklenecek test sonucu bulunamadı", strict)
    if context:
        from .context import attach_context

        attach_context(records, repo, max_kb=context_max_kb)
    tests = [{k: v for k, v in r.items() if not k.startswith("_")} for r in records]

    if not project or not branch or not run:
        _fail(
            "project/branch/run belirlenemedi (CI_PROJECT_PATH, CI_COMMIT_REF_NAME, CI_JOB_ID ya da bayraklar)",
            strict,
        )
    base = {
        "project": project,
        "branch": branch,
        "commit_sha": commit or None,
        "external_id": run,
        "meta": build_meta(),
    }
    if dry_run:
        typer.echo(json.dumps({**base, "tests": tests}, ensure_ascii=False, indent=2))
        return

    cl = _client(url, token, strict)
    try:
        out = None
        inserted = dup = 0
        for i in range(0, len(tests), CHUNK):
            out = cl.post_run({**base, "tests": tests[i : i + CHUNK]})
            inserted += out["inserted"]
            dup += out["duplicates"]
        assert out is not None
        for w in out.get("warnings", []):
            warn(w)
        _save_state(out["run_id"], run)
        typer.echo(f"triaj: koşum {out['run_id']} ({run}): {inserted} sonuç yüklendi, {dup} tekrar atlandı")
        if finish:
            f = cl.finish(out["run_id"])
            typer.echo(_finish_line(f))
    except TriajError as e:
        _fail(str(e), strict)
    finally:
        cl.close()


def _finish_line(f: dict) -> str:
    if f.get("already_finished"):
        return f"triaj: koşum {f['run_id']} zaten bitmiş"
    return (
        f"triaj: koşum {f['run_id']} bitti: {f['remaining']} kalan sonuç, LLM kuyruğu {f['llm_queued']}, "
        f"tekrar kullanılan {f['llm_reused']}, atlanan {f['llm_skipped']}"
    )


@app.command("finish")
def finish_cmd(
    run_id: Annotated[
        int | None, typer.Option("--run-id", help="Sunucu koşum id'si (yoksa son upload)")
    ] = None,
    url: Url = None,
    token: Token = None,
    strict: Strict = False,
) -> None:
    """Koşumu bitirir: kural kararları verilir, LLM işleri kuyruğa girer."""
    rid = _load_run_id(run_id)
    if not rid:
        _fail(f"koşum id'si yok ({STATE_FILE} bulunamadı; --run-id verin)", strict)
    cl = _client(url, token, strict)
    try:
        typer.echo(_finish_line(cl.finish(rid)))
    except TriajError as e:
        _fail(str(e), strict)
    finally:
        cl.close()


def render_summary(tri: dict) -> str:
    lines = [
        f"triaj: {tri['project']} {tri['branch']} {(tri.get('commit_sha') or '')[:10]} "
        f"koşum {tri['external_id']}: {len(tri['items'])} kalan sonuç"
    ]
    for k, v in sorted(tri.get("summary", {}).items()):
        lines.append(f"  {k:<11} {v}")
    for it in tri["items"]:
        d = it.get("decision") or {}
        src = d.get("source", "-")
        conf = f" {d['confidence']:.2f}" if d.get("confidence") is not None else ""
        lines.append(
            f"  - [{d.get('category', '?'):<10}] {it['name'][:80]}  ({src}{conf}, {it.get('next_action')})"
        )
        sug = it.get("llm_suggestion")
        if sug:
            lines.append(f"      LLM önerisi: {sug['category']} ({sug.get('confidence') or 0:.2f})")
    return "\n".join(lines)


@app.command()
def wait(
    run_id: Annotated[int | None, typer.Option("--run-id")] = None,
    timeout: Annotated[int, typer.Option(help="En fazla bekleme (sn)")] = 600,
    as_json: Annotated[bool, typer.Option("--json", help="Triage'ı JSON bas")] = False,
    url: Url = None,
    token: Token = None,
    strict: Strict = False,
) -> None:
    """LLM kararlarını bekler ve özet basar."""
    rid = _load_run_id(run_id)
    if not rid:
        _fail(f"koşum id'si yok ({STATE_FILE} bulunamadı; --run-id verin)", strict)
    cl = _client(url, token, strict)
    deadline = time.monotonic() + timeout
    delay = 2.0
    try:
        while True:
            tri = cl.triage(rid)
            pending = [i for i in tri["items"] if i.get("llm_state") in ("queued", "running")]
            if not pending or time.monotonic() >= deadline:
                break
            time.sleep(min(delay, max(0.0, deadline - time.monotonic())))
            delay = min(delay * 1.5, 30.0)
        if pending:
            warn(f"zaman aşımı: {len(pending)} sonuç için LLM kararı henüz yok")
        typer.echo(json.dumps(tri, ensure_ascii=False, indent=2) if as_json else render_summary(tri))
    except TriajError as e:
        _fail(str(e), strict)
    finally:
        cl.close()


def main() -> None:  # pragma: no cover
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)
