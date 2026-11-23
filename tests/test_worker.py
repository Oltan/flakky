"""Worker + LLM akışı, süreç içi sahte LLM sunucusuyla (tools/sahte_llm.py)."""

import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from tools import sahte_llm

from conftest import LLM_KEY, TOKEN, make_settings, t
from triaj.api import create_app
from triaj.db import llm_jobs, pending_context
from triaj.llm.client import make_client
from triaj.worker import Worker

FAIL_TESTS = [
    t("U", "failed", message="expected 120 but was 118", trace="at a.Fiyat.kdv(Fiyat.java:5)"),
    t("K", "broken", message="NullPointerException: musteri", trace="at a.T.t(T.java:9)"),
    t("O", "failed", message="Connection refused", trace="at a.T.o(T.java:3)"),
]


class Clock:
    """Gerçek zaman + kaydırma: işler gerçek zamanla oluşur, testler zamanı ileri sarar."""

    def __init__(self):
        self.offset = timedelta()

    def __call__(self):
        return datetime.now(UTC) + self.offset

    def tick(self, s):
        self.offset += timedelta(seconds=s)

    def set(self, dt):
        self.offset = dt - datetime.now(UTC)


@pytest.fixture
def llm():
    sahte_llm.STATE.update(mode="ok", slow_s=10.0, retry_after=2, calls=0, once_used=False)
    return sahte_llm.STATE


@pytest.fixture
def lsettings():
    # Mesai sınırları bu testlerde etkisiz (ayrı testte ölçülüyor).
    return make_settings(
        llm_enabled_default=True, llm_timeout_s=0.5, llm_max_tries=3, llm_busy_concurrency=4, llm_busy_gap_s=0
    )


@pytest.fixture
def lapp(engine, lsettings):
    return create_app(lsettings, engine)


@pytest.fixture
def lclient(lapp):
    with TestClient(lapp) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def worker(engine, lsettings, clock):
    def factory():
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=sahte_llm.app), base_url="http://sahte")
        return make_client(lsettings, http_client=http)

    return Worker(engine, lsettings, client_factory=factory, clock=clock)


def run(lclient, tests=FAIL_TESTS, commit="c1", ext=None):

    ext = ext or f"w-{next(_ids)}"
    r = lclient.post(
        "/v1/runs",
        json={"project": "p", "branch": "main", "commit_sha": commit, "external_id": ext, "tests": tests},
    )
    assert r.status_code == 200, r.text
    rid = r.json()["run_id"]
    f = lclient.post(f"/v1/runs/{rid}/finish").json()
    return rid, f


import itertools  # noqa: E402

_ids = itertools.count(1)


def triage(lclient, rid):
    return {i["test_key"]: i for i in lclient.get(f"/v1/runs/{rid}/triage").json()["items"]}


def jobs(engine):
    with engine.connect() as c:
        return {r.result_id: r for r in c.execute(select(llm_jobs))}


async def test_worker_writes_llm_decisions(lclient, worker, llm, engine):
    rid, f = run(lclient)
    assert f["llm_queued"] == 3
    assert {i["llm_state"] for i in triage(lclient, rid).values()} == {"queued"}
    states = await worker.run_once()
    assert states == ["done"] * 3
    tri = triage(lclient, rid)
    assert {k: (v["decision"]["source"], v["decision"]["category"]) for k, v in tri.items()} == {
        "U": ("llm", "urun"),
        "K": ("llm", "test_kodu"),
        "O": ("llm", "ortam"),
    }
    assert tri["U"]["decision"]["model"] == "sahte-model" and tri["U"]["rule_decision"]["source"] == "rule"
    # payload iş bitince silinir; ölçüm meta'da
    for j in jobs(engine).values():
        assert j.payload is None and j.state == "done" and j.meta["completion_tokens"] > 0
    # Gerçek istek JSON şeması ile gitti.
    assert llm["last"]["response_format"]["type"] == "json_schema"
    assert llm["last"]["max_tokens"] == 1024


async def test_worker_marker_mode(engine, lclient, clock, llm):
    s = make_settings(llm_enabled_default=True, llm_json_mode="marker", llm_timeout_s=2)
    llm["mode"] = "marker"
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=sahte_llm.app), base_url="http://sahte")
    w = Worker(engine, s, client_factory=lambda: make_client(s, http_client=http), clock=clock)
    rid, _ = run(lclient, FAIL_TESTS[:1])
    assert await w.run_once() == ["done"]
    assert "response_format" not in llm["last"]
    assert triage(lclient, rid)["U"]["decision"]["category"] == "urun"


async def test_llm_reuses_same_signature_same_commit(lclient, worker, llm):
    run(lclient, FAIL_TESTS[:1], commit="c1")
    await worker.run_once()
    calls = llm["calls"]
    # Aynı commit, aynı hata (sayılar farklı ama maskeleniyor) → kopyalanır.
    same = [t("U", "failed", message="expected 999 but was 1", trace="at a.Fiyat.kdv(Fiyat.java:77)")]
    rid2, f2 = run(lclient, same, commit="c1")
    assert f2["llm_reused"] == 1 and f2["llm_queued"] == 0
    d = triage(lclient, rid2)["U"]
    assert d["decision"]["source"] == "llm" and d["decision"]["reused_from"] is not None
    assert d["llm_state"] == "done"
    # Farklı commit → yeni çağrı.
    rid3, f3 = run(lclient, same, commit="c2")
    assert f3["llm_queued"] == 1
    await worker.run_once()
    assert llm["calls"] == calls + 1


async def test_llm_invalid_json_retry_then_fail(lclient, worker, llm, engine):
    llm["mode"] = "garbage_once"
    rid, _ = run(lclient, FAIL_TESTS[:1])
    assert await worker.run_once() == ["done"]
    assert jobs(engine)[triage(lclient, rid)["U"]["result_id"]].meta["rounds"] == 2

    llm["mode"] = "garbage"
    rid, _ = run(lclient, FAIL_TESTS[1:2])
    assert await worker.run_once() == ["failed"]
    item = triage(lclient, rid)["K"]
    assert item["llm_state"] == "failed" and item["decision"]["source"] == "rule"


async def test_worker_429_respects_retry_after(lclient, worker, llm, engine, clock):
    llm.update(mode="429_once", retry_after=40)
    rid, _ = run(lclient, FAIL_TESTS[:2])
    states = await worker.run_once()
    assert "queued" in states  # 429 alan iş kuyruğa döndü
    j = [x for x in jobs(engine).values() if x.last_error == "429 hız sınırı"]
    assert len(j) == 1 and j[0].tries == 0
    # Retry-After dolmadan kimse çağrı yapmaz (küresel bekleme).
    queued_before = sum(1 for x in jobs(engine).values() if x.state == "queued")
    clock.tick(30)
    assert await worker.run_once() == []
    clock.tick(11)
    states = await worker.run_once()
    assert states.count("done") == queued_before
    assert {i["llm_state"] for i in triage(lclient, rid).values()} == {"done"}


async def test_worker_timeout_marks_failed(lclient, worker, llm, engine):
    llm.update(mode="slow", slow_s=3)
    rid, _ = run(lclient, FAIL_TESTS[:1])
    assert await worker.run_once() == ["failed"]
    j = next(iter(jobs(engine).values()))
    assert j.tries == 0 and "zaman aşımı" in j.last_error and j.payload is None
    # Tekrar denenmez.
    llm["mode"] = "ok"
    assert await worker.run_once() == []


async def test_llm_failure_keeps_rule_decision(lclient, worker, llm, engine, clock):
    llm["mode"] = "500"
    rid, _ = run(lclient, FAIL_TESTS[:1])
    assert await worker.run_once() == ["queued"]
    clock.tick(31)
    assert await worker.run_once() == ["queued"]
    clock.tick(61)
    assert await worker.run_once() == ["failed"]  # LLM_MAX_TRIES=3
    item = triage(lclient, rid)["U"]
    assert item["llm_state"] == "failed"
    assert item["decision"]["source"] == "rule" and item["decision"]["category"] == "urun"
    assert item["next_action"] == "open_bug"


async def test_max_per_run_skips_rest(engine, llm):
    s = make_settings(llm_enabled_default=True, max_per_run=2)
    with TestClient(create_app(s, engine)) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        tests = [t(f"T{i}", "failed", message=f"hata {chr(65 + i)}") for i in range(5)]
        tests.append(t("ORT", "failed", message="Connection refused"))
        rid, f = run(c, tests)
        assert (f["llm_queued"], f["llm_skipped"]) == (2, 4)
        tri = triage(c, rid)
        # Ortam kararı verilenler sona bırakılır.
        assert tri["ORT"]["llm_state"] == "skipped"
        assert sum(1 for i in tri.values() if i["llm_state"] == "queued") == 2


async def test_limits_busy_concurrency_in_worker(engine, lclient, llm, clock):
    s = make_settings(
        llm_enabled_default=True,
        llm_busy_concurrency=1,
        llm_idle_concurrency=4,
        llm_busy_gap_s=60,
        llm_job_expire_h=1000,
    )
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=sahte_llm.app), base_url="http://sahte")
    w = Worker(engine, s, client_factory=lambda: make_client(s, http_client=http), clock=clock)
    run(lclient)
    # Gelecekteki ilk Pazartesi 10:00 (İstanbul, UTC+3): mesai.
    now = datetime.now(UTC)
    monday = (now + timedelta(days=7 - now.weekday())).replace(hour=7, minute=0, second=0, microsecond=0)
    clock.set(monday)
    assert len(await w.run_once()) == 1
    clock.tick(30)
    assert await w.run_once() == []  # çağrılar arası boşluk
    clock.tick(31)
    assert len(await w.run_once()) == 1
    clock.set(monday.replace(hour=16))  # 19:00 yerel: mesai dışı
    assert len(await w.run_once()) == 1  # kalan tek iş


async def test_worker_reclaims_stale_running(lclient, worker, engine, clock, llm):
    run(lclient, FAIL_TESTS[:1])
    job = worker.claim()
    assert job is not None
    # Worker çöktü: iş running'de kaldı.
    clock.tick(5)
    assert await worker.run_once() == []
    clock.tick(120)
    assert await worker.run_once() == ["done"]
    assert next(iter(jobs(engine).values())).tries == 1


async def test_worker_expires_old_queued(lclient, worker, engine, clock, llm):
    run(lclient, FAIL_TESTS[:1])
    with engine.begin() as c:
        c.execute(update(llm_jobs).values(created_at=clock() - timedelta(hours=25)))
    assert await worker.run_once() == []
    assert next(iter(jobs(engine).values())).state == "failed"


async def test_context_payload_moves_to_job(lclient, worker, engine, llm):
    tests = [
        dict(
            FAIL_TESTS[0],
            context={"files": [{"path": "a/Fiyat.java", "role": "product", "content": "class Fiyat {}"}]},
        )
    ]
    rid, _ = run(lclient, tests)
    with engine.connect() as c:
        assert c.execute(select(pending_context)).first() is None  # finish'te taşındı
        assert next(iter(jobs(engine).values())).payload["context"]["files"][0]["path"] == "a/Fiyat.java"
    await worker.run_once()
    assert "a/Fiyat.java" in llm["last"]["messages"][1]["content"]
    assert next(iter(jobs(engine).values())).meta["files_included"] == ["a/Fiyat.java"]


def test_context_not_stored_when_llm_disabled(client, engine):
    tests = [dict(FAIL_TESTS[0], context={"files": [{"path": "x", "content": "gizli kod"}]})]
    r = client.post("/v1/runs", json={"project": "p", "branch": "m", "external_id": "e", "tests": tests})
    assert r.status_code == 200
    with engine.connect() as c:
        assert c.execute(select(pending_context)).first() is None


async def test_tokens_not_logged(lclient, worker, llm, caplog, engine):
    from triaj.logsetup import RedactFilter

    caplog.handler.addFilter(RedactFilter([TOKEN, LLM_KEY]))
    caplog.set_level(logging.DEBUG)
    llm["mode"] = "500"
    run(lclient, FAIL_TESTS[:1])
    await worker.run_once()
    logging.getLogger("triaj.test").error("istek başlığı Bearer %s anahtar %s", TOKEN, LLM_KEY)
    # Yanlış token yanıtında da sızıntı yok.
    r = lclient.get("/v1/runs/1/triage", headers={"Authorization": f"Bearer {TOKEN}x"})
    assert TOKEN not in r.text
    out = caplog.text
    assert TOKEN not in out and LLM_KEY not in out
    assert "***" in out
    with engine.connect() as c:
        for j in c.execute(select(llm_jobs)):
            assert LLM_KEY not in (j.last_error or "")
    # Ayar nesnesinin repr'ı da gizli.
    assert LLM_KEY not in repr(make_settings()) and TOKEN not in repr(make_settings())
