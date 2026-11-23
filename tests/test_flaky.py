from conftest import t


def flaky(client, project="p", branch="main"):
    r = client.get(f"/v1/projects/{project}/flaky", params={"branch": branch})
    assert r.status_code == 200, r.text
    return {i["test_key"]: i["evidence"] for i in r.json()}


def test_flaky_fail_then_pass_same_commit(client, upload):
    a = upload([t("A", "failed")], commit="c1")
    upload([t("A", "failed")], commit="c1")
    c = upload([t("A", "passed")], commit="c1")
    f = flaky(client)
    assert set(f) == {"A"}
    ev = {(e["failed_run"], e["passed_run"]) for e in f["A"]}
    assert (a["run_id"], c["run_id"]) in ev
    assert all(e["commit_sha"] == "c1" for e in f["A"])


def test_flaky_retry_pass_same_run(client, upload):
    r = upload([t("A", "failed", 1), t("A", "passed", 2)])
    f = flaky(client)
    assert f["A"][0]["failed_run"] == r["run_id"] == f["A"][0]["passed_run"]


def test_flaky_not_when_commit_changes(client, upload):
    upload([t("A", "failed")], commit="c1")
    upload([t("A", "passed")], commit="c2")  # düzeltme
    assert flaky(client) == {}


def test_flaky_not_when_breaks_and_stays(client, upload):
    upload([t("A", "passed")], commit="c1")
    upload([t("A", "failed")], commit="c1")
    upload([t("A", "failed")], commit="c1")  # bozulma: önce geçen, sonra hep kalan
    assert flaky(client) == {}


def test_flaky_projects_and_branches_separate(client, upload):
    upload([t("A", "failed")], project="p1", commit="c1")
    upload([t("A", "passed")], project="p2", commit="c1")
    upload([t("A", "failed")], project="p1", branch="b1", commit="c1")
    upload([t("A", "passed")], project="p1", branch="b2", commit="c1")
    assert flaky(client, "p1") == {}
    assert flaky(client, "p1", "b1") == {}
    assert flaky(client, "p2") == {}


def test_flaky_window_limit(client, upload, settings):
    upload([t("A", "failed")], commit="c1")
    upload([t("A", "passed")], commit="c1")
    assert "A" in flaky(client)
    for _ in range(settings.flaky_window - 1):
        upload([t("B", "passed")], commit="c1")
    # Başarısız koşum pencereden çıktı.
    assert "A" not in flaky(client)


def test_flaky_window_project_setting(client, upload):
    upload([t("A", "failed")], commit="c1")
    upload([t("A", "passed")], commit="c1")
    upload([t("B", "passed")], commit="c1")
    assert "A" in flaky(client)
    r = client.put("/v1/projects/p/settings", json={"flaky_window": 2})
    assert r.status_code == 200
    assert "A" not in flaky(client)


def test_flaky_empty_commit_same_version(client, upload):
    r = upload([t("A", "broken")], commit=None)
    assert any("commit_sha" in w for w in r["warnings"])
    upload([t("A", "passed")], commit="")
    assert "A" in flaky(client)


def test_flaky_project_with_slash_in_name(client, upload):
    upload([t("A", "failed")], project="grup/alt/proje")
    upload([t("A", "passed")], project="grup/alt/proje")
    assert "A" in flaky(client, "grup/alt/proje")
