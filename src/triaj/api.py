"""FastAPI uygulaması. Uçlar /v1 altında, Bearer token ile."""

from __future__ import annotations

import logging
from collections.abc import Iterator

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Connection, Engine, select, text

from . import decisions as dec
from . import projects as proj
from .accuracy import llm_accuracy
from .auth import require_token
from .config import Settings, get_settings
from .db import make_engine, results
from .ingest import RunFinished, ingest_run
from .logsetup import setup_logging
from .models import (
    AdminDecisionIn,
    DecisionOut,
    FinishOut,
    FlakyItem,
    HistoryItem,
    IngestOut,
    ProjectSettingsPatch,
    RunIn,
    TriageOut,
)
from .triage import NotFound, finish_run, flaky_list, get_triage, result_auto_confidence, test_history

log = logging.getLogger("triaj.api")


def get_conn(request: Request) -> Iterator[Connection]:
    engine: Engine = request.app.state.engine
    with engine.begin() as conn:
        yield conn


def get_cfg(request: Request) -> Settings:
    return request.app.state.settings


v1 = APIRouter(prefix="/v1", dependencies=[Depends(require_token)])


@v1.post("/runs", response_model=IngestOut)
def post_runs(body: RunIn, conn: Connection = Depends(get_conn), cfg: Settings = Depends(get_cfg)):
    try:
        return ingest_run(conn, body, cfg)
    except RunFinished as e:
        raise HTTPException(409, str(e)) from None


@v1.post("/runs/{run_id}/finish", response_model=FinishOut)
def post_finish(run_id: int, conn: Connection = Depends(get_conn), cfg: Settings = Depends(get_cfg)):
    return finish_run(conn, run_id, cfg)


@v1.get("/runs/{run_id}/triage", response_model=TriageOut)
def get_run_triage(run_id: int, conn: Connection = Depends(get_conn), cfg: Settings = Depends(get_cfg)):
    return get_triage(conn, run_id, cfg)


@v1.get("/results/{result_id}/decisions", response_model=list[DecisionOut])
def get_decisions(result_id: int, conn: Connection = Depends(get_conn)):
    if conn.execute(select(results.c.id).where(results.c.id == result_id)).first() is None:
        raise NotFound(f"sonuç {result_id} yok")
    return dec.history(conn, result_id)


@v1.post("/results/{result_id}/decisions", response_model=DecisionOut, status_code=201)
def post_decision(
    result_id: int,
    body: AdminDecisionIn,
    conn: Connection = Depends(get_conn),
    cfg: Settings = Depends(get_cfg),
):
    auto = result_auto_confidence(conn, result_id, cfg)
    did = dec.add(
        conn, result_id, body.category, "admin", body.reason, auto_confidence=auto, author=body.author
    )
    return next(d for d in dec.history(conn, result_id) if d.id == did)


# Proje adı GitLab yolu gibi '/' içerebilir: {project:path} + sabit sonek.


@v1.get("/projects/{project:path}/flaky", response_model=list[FlakyItem])
def get_flaky(
    project: str,
    branch: str = Query(...),
    conn: Connection = Depends(get_conn),
    cfg: Settings = Depends(get_cfg),
):
    return flaky_list(conn, project, branch, cfg)


@v1.get("/projects/{project:path}/tests/history", response_model=list[HistoryItem])
def get_history(
    project: str,
    test_key: str = Query(..., min_length=1),
    branch: str | None = None,
    limit: int = Query(20, ge=1, le=500),
    conn: Connection = Depends(get_conn),
):
    return test_history(conn, project, test_key, branch, limit)


@v1.get("/projects/{project:path}/llm-accuracy")
def get_accuracy(project: str, conn: Connection = Depends(get_conn)):
    p = proj.get_by_name(conn, project)
    if p is None:
        raise NotFound(f"proje {project!r} yok")
    return llm_accuracy(conn, p.id)


@v1.get("/projects/{project:path}/settings")
def get_settings_ep(project: str, conn: Connection = Depends(get_conn), cfg: Settings = Depends(get_cfg)):
    p = proj.get_by_name(conn, project)
    if p is None:
        raise NotFound(f"proje {project!r} yok")
    eff = proj.effective(p.settings, cfg)
    return {"stored": p.settings or {}, "effective": eff.__dict__}


@v1.put("/projects/{project:path}/settings")
def put_settings(
    project: str,
    body: ProjectSettingsPatch,
    conn: Connection = Depends(get_conn),
    cfg: Settings = Depends(get_cfg),
):
    p = proj.get_or_create(conn, project)
    stored = proj.patch_settings(conn, p.id, body)
    return {"stored": stored, "effective": proj.effective(stored, cfg).__dict__}


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.secrets)
    app = FastAPI(title="triaj", version="0.1.0")
    app.state.settings = settings
    app.state.engine = engine or make_engine(settings.database_url)

    @app.exception_handler(NotFound)
    async def _nf(_req: Request, exc: NotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(Exception)
    async def _err(_req: Request, exc: Exception):
        log.exception("beklenmeyen hata")
        return JSONResponse({"detail": "iç hata"}, status_code=500)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        try:
            with app.state.engine.connect() as c:
                c.execute(text("SELECT 1 FROM runs LIMIT 1"))
        except Exception:
            log.warning("readyz: veritabanı hazır değil")
            return JSONResponse({"status": "db_unavailable"}, status_code=503)
        return {"status": "ready"}

    app.include_router(v1)
    return app


def app_factory() -> FastAPI:  # uvicorn --factory triaj.api:app_factory
    return create_app()
