from conftest import t


def test_ingest_idempotent_external_id(client, upload):
    a = upload([t("A", "failed", 1), t("B", "passed", 1)], external_id="pipe-1")
    b = upload([t("A", "failed", 1), t("A", "passed", 2)], external_id="pipe-1")
    assert a["run_id"] == b["run_id"]
    assert a["inserted"] == 2
    assert b["inserted"] == 1 and b["duplicates"] == 1


def test_ingest_attempts_last_is_latest(client, upload):
    r = upload([t("A", "failed"), t("A", "broken"), t("A", "passed"), t("B", "failed")], finish=True)
    tri = client.get(f"/v1/runs/{r['run_id']}/triage").json()
    # A'nın son denemesi passed → kalan değil; yalnızca B kalır.
    assert [i["test_key"] for i in tri["items"]] == ["B"]
    hist = client.get("/v1/projects/p/tests/history", params={"test_key": "A"}).json()
    assert len(hist) == 1 and hist[0]["attempt"] == 3 and hist[0]["status"] == "passed"


def test_ingest_attempt_continues_across_calls(client, upload):
    upload([t("A", "failed")], external_id="x")
    upload([t("A", "passed")], external_id="x")
    hist = client.get("/v1/projects/p/tests/history", params={"test_key": "A"}).json()
    assert hist[0]["attempt"] == 2 and hist[0]["status"] == "passed"


def test_ingest_finished_run_rejects_results(client, upload):
    upload([t("A", "failed")], external_id="y", finish=True)
    r = client.post(
        "/v1/runs",
        json={
            "project": "p",
            "branch": "main",
            "commit_sha": "c1",
            "external_id": "y",
            "tests": [t("B", "failed")],
        },
    )
    assert r.status_code == 409


def test_ingest_validation_error(client):
    r = client.post(
        "/v1/runs",
        json={
            "project": "p",
            "branch": "main",
            "external_id": "z",
            "tests": [{"test_key": "A", "status": "nope"}],
        },
    )
    assert r.status_code == 422


def test_auth_missing_token_401(client):
    r = client.get("/v1/runs/1/triage", headers={"Authorization": ""})
    assert r.status_code == 401
    r = client.get("/v1/runs/1/triage", headers={"Authorization": "Bearer yanlis"})
    assert r.status_code == 401
    assert client.get("/healthz").status_code == 200


def test_readyz(client):
    assert client.get("/readyz").json() == {"status": "ready"}


def test_finish_idempotent(client, upload):
    r = upload([t("A", "failed")], finish=True)
    assert r["finish"]["already_finished"] is False
    again = client.post(f"/v1/runs/{r['run_id']}/finish").json()
    assert again["already_finished"] is True
    res_id = client.get(f"/v1/runs/{r['run_id']}/triage").json()["items"][0]["result_id"]
    assert len(client.get(f"/v1/results/{res_id}/decisions").json()) == 1


def test_triage_next_action_mapping(client, upload):
    upload([t("F", "failed")], commit="c1")
    upload([t("F", "passed")], commit="c1")
    r = upload(
        [
            t("U", "failed", message="expected 1 but was 2"),
            t("K", "broken", message="NullPointerException"),
            t("O", "failed", message="Connection refused"),
            t("F", "failed"),
            t("S", "skipped"),
        ],
        commit="c1",
        finish=True,
    )
    tri = client.get(f"/v1/runs/{r['run_id']}/triage").json()
    got = {i["test_key"]: (i["decision"]["category"], i["next_action"]) for i in tri["items"]}
    assert got == {
        "U": ("urun", "open_bug"),
        "K": ("test_kodu", "fix_test"),
        "O": ("ortam", "notify"),
        "F": ("flaky", "none"),
    }
    assert tri["summary"] == {"urun": 1, "test_kodu": 1, "ortam": 1, "flaky": 1}
    f = next(i for i in tri["items"] if i["test_key"] == "F")
    assert f["flaky"] and f["flaky_evidence"]


def test_not_found(client):
    assert client.get("/v1/runs/999/triage").status_code == 404
    assert client.get("/v1/projects/yok/flaky", params={"branch": "m"}).status_code == 404
    assert client.get("/v1/results/999/decisions").status_code == 404
    assert (
        client.post("/v1/results/999/decisions", json={"category": "urun", "author": "a"}).status_code == 404
    )
