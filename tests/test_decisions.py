from conftest import t
from triaj import decisions as dec


def _result_id(client, upload, **kw):
    r = upload([t("A", "failed", message="expected 1 but was 2")], finish=True, **kw)
    tri = client.get(f"/v1/runs/{r['run_id']}/triage").json()
    return r["run_id"], tri["items"][0]["result_id"]


def test_decisions_precedence_admin_llm_rule(client, upload, engine):
    run_id, rid = _result_id(client, upload)
    with engine.begin() as c:
        dec.add(c, rid, "test_kodu", "llm", "llm gerekçe", auto_confidence=0.7, confidence=0.9, model="m")
    tri = client.get(f"/v1/runs/{run_id}/triage").json()["items"][0]
    assert tri["decision"]["source"] == "llm" and tri["decision"]["category"] == "test_kodu"
    assert tri["rule_decision"]["category"] == "urun"

    r = client.post(
        f"/v1/results/{rid}/decisions", json={"category": "ortam", "author": "ayse", "reason": "ağ"}
    )
    assert r.status_code == 201 and r.json()["source"] == "admin"
    # Admin'den sonra gelen yüksek güvenli LLM kararı admin'i ezmez.
    with engine.begin() as c:
        dec.add(c, rid, "urun", "llm", "yeni", auto_confidence=0.7, confidence=0.99, model="m")
    tri = client.get(f"/v1/runs/{run_id}/triage").json()["items"][0]
    assert tri["decision"]["source"] == "admin" and tri["decision"]["category"] == "ortam"
    assert tri["llm_suggestion"]["category"] == "urun"
    hist = client.get("/v1/projects/p/tests/history", params={"test_key": "A"}).json()
    assert hist[0]["category"] == "ortam" and hist[0]["source"] == "admin"


def test_decisions_low_confidence_is_suggestion(client, upload, engine):
    run_id, rid = _result_id(client, upload)
    with engine.begin() as c:
        dec.add(c, rid, "test_kodu", "llm", "emin değil", auto_confidence=0.7, confidence=0.5, model="m")
    tri = client.get(f"/v1/runs/{run_id}/triage").json()["items"][0]
    assert tri["decision"]["source"] == "rule"
    assert tri["llm_suggestion"]["category"] == "test_kodu"
    assert tri["next_action"] == "open_bug"


def test_decisions_auto_confidence_project_setting(client, upload, engine):
    client.put("/v1/projects/p/settings", json={"auto_confidence": 1.01})
    run_id, rid = _result_id(client, upload)
    with engine.begin() as c:
        dec.add(c, rid, "test_kodu", "llm", "x", auto_confidence=1.01, confidence=1.0, model="m")
    tri = client.get(f"/v1/runs/{run_id}/triage").json()["items"][0]
    assert tri["decision"]["source"] == "rule"


def test_decisions_never_deleted(client, upload, engine):
    _, rid = _result_id(client, upload)
    for cat in ("urun", "ortam", "test_kodu"):
        client.post(f"/v1/results/{rid}/decisions", json={"category": cat, "author": "a"})
    hist = client.get(f"/v1/results/{rid}/decisions").json()
    assert [h["source"] for h in hist] == ["admin", "admin", "admin", "rule"]
    assert hist[0]["category"] == "test_kodu"


def test_decisions_reason_truncated(client, upload):
    _, rid = _result_id(client, upload)
    r = client.post(
        f"/v1/results/{rid}/decisions", json={"category": "urun", "author": "a", "reason": "x" * 5000}
    )
    assert len(r.json()["reason"]) == 2000


def test_admin_decision_requires_author(client, upload):
    _, rid = _result_id(client, upload)
    assert client.post(f"/v1/results/{rid}/decisions", json={"category": "urun"}).status_code == 422
    assert (
        client.post(f"/v1/results/{rid}/decisions", json={"category": "uydurma", "author": "a"}).status_code
        == 422
    )
