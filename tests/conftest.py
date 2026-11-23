"""Ortak test fikstürleri.

DB'ye dokunan testler SQLite'ta ve TRIAJ_TEST_PG_URL verilmişse Postgres'te de koşar.
"""

from __future__ import annotations

import itertools
import os

import pytest
from fastapi.testclient import TestClient

from triaj.api import create_app
from triaj.config import Settings
from triaj.db import create_schema, drop_schema, make_engine

TOKEN = "test-token-abcdef123456"
LLM_KEY = "sk-gizli-llm-anahtari-987654"

PG_URL = os.environ.get("TRIAJ_TEST_PG_URL")
BACKENDS = ["sqlite"] + (["postgres"] if PG_URL else [])


def make_settings(**kw) -> Settings:
    base = dict(
        database_url="sqlite://",
        api_tokens=TOKEN,
        llm_api_key=LLM_KEY,
        llm_base_url="http://sahte-llm.test/v1",
        llm_model="sahte-model",
        llm_timeout_s=5,
        llm_busy_gap_s=0,
    )
    base.update(kw)
    return Settings(**base)


@pytest.fixture(params=BACKENDS)
def engine(request, tmp_path):
    if request.param == "sqlite":
        eng = make_engine(f"sqlite:///{tmp_path / 'triaj.db'}")
    else:
        eng = make_engine(PG_URL)
        drop_schema(eng)
    create_schema(eng)
    yield eng
    if request.param == "postgres":
        drop_schema(eng)
    eng.dispose()


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def app(engine, settings):
    return create_app(settings, engine)


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c


_ext = itertools.count(1)


def t(key: str, status: str, attempt: int | None = None, **kw) -> dict:
    d = {"test_key": key, "status": status, **kw}
    if attempt is not None:
        d["attempt"] = attempt
    return d


@pytest.fixture
def upload(client):
    """upload(tests, commit='c1', branch='main', project='p', external_id=None, finish=False)"""

    def _up(tests, commit="c1", branch="main", project="p", external_id=None, finish=False):
        body = {
            "project": project,
            "branch": branch,
            "commit_sha": commit,
            "external_id": external_id or f"run-{next(_ext)}",
            "tests": tests,
        }
        r = client.post("/v1/runs", json=body)
        assert r.status_code == 200, r.text
        out = r.json()
        if finish:
            f = client.post(f"/v1/runs/{out['run_id']}/finish")
            assert f.status_code == 200, f.text
            out["finish"] = f.json()
        return out

    return _up
