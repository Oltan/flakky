import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import triaj_cli.main as cli
from conftest import TOKEN
from triaj_cli.client import Client

DATA = Path(__file__).parent / "data"
runner = CliRunner()

GITLAB = {
    "CI_PROJECT_PATH": "grup/uygulama",
    "CI_COMMIT_REF_NAME": "main",
    "CI_COMMIT_SHA": "abc123",
    "CI_JOB_ID": "1001",
    "CI_PIPELINE_ID": "77",
    "CI_JOB_NAME": "test",
}


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for k in GITLAB:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("TRIAJ_URL", raising=False)
    monkeypatch.delenv("TRIAJ_TOKEN", raising=False)
    return tmp_path


@pytest.fixture
def served(app, monkeypatch, in_tmp):
    """CLI'nin HTTP istemcisini uygulamaya bağlar."""
    tc = TestClient(app)

    class _C(Client):
        def __init__(self, url, token, timeout=30.0, retries=0):
            self.http = tc
            self.http.headers["Authorization"] = f"Bearer {token}"
            self.retries = 0

        def close(self):
            pass

    monkeypatch.setattr(cli, "Client", _C)
    return tc


def test_cli_gitlab_env_defaults(in_tmp):
    res = runner.invoke(
        cli.app,
        ["upload", "--format", "junit", "--dir", str(DATA / "maven-junit"), "--dry-run"],
        env=GITLAB,
    )
    assert res.exit_code == 0, res.output
    body = json.loads(res.stdout)
    assert body["project"] == "grup/uygulama" and body["branch"] == "main"
    assert body["commit_sha"] == "abc123" and body["external_id"] == "1001"
    assert body["meta"]["pipeline_id"] == "77"
    assert all("_meta" not in t for t in body["tests"])
    res = runner.invoke(
        cli.app,
        [
            "upload",
            "--format",
            "junit",
            "--dir",
            str(DATA / "maven-junit"),
            "--dry-run",
            "--project",
            "baska",
            "--run",
            "x1",
        ],
        env=GITLAB,
    )
    body = json.loads(res.stdout)
    assert body["project"] == "baska" and body["external_id"] == "x1"


def test_cli_server_down_exits_zero(in_tmp):
    args = [
        "upload",
        "--format",
        "allure",
        "--dir",
        str(DATA / "pytest" / "allure-results"),
        "--url",
        "http://127.0.0.1:9",
        "--token",
        "t",
    ]
    res = runner.invoke(cli.app, args, env=GITLAB)
    assert res.exit_code == 0
    assert "UYARI" in res.stderr
    res = runner.invoke(cli.app, args + ["--strict"], env=GITLAB)
    assert res.exit_code == 1


def test_cli_missing_url_exits_zero(in_tmp):
    res = runner.invoke(cli.app, ["upload", "--dir", str(DATA / "pytest" / "allure-results")], env=GITLAB)
    assert res.exit_code == 0 and "TRIAJ_URL" in res.stderr


def test_cli_upload_finish_wait_end_to_end(served):
    env = {**GITLAB, "TRIAJ_URL": "http://test", "TRIAJ_TOKEN": TOKEN}
    res = runner.invoke(
        cli.app, ["upload", "--format", "allure", "--dir", str(DATA / "pytest" / "allure-results")], env=env
    )
    assert res.exit_code == 0, res.output
    assert "13 sonuç yüklendi" in res.stdout
    # Tekrar gönderim: çift kayıt yok.
    res = runner.invoke(
        cli.app, ["upload", "--format", "allure", "--dir", str(DATA / "pytest" / "allure-results")], env=env
    )
    assert "0 sonuç yüklendi, 13 tekrar atlandı" in res.stdout
    res = runner.invoke(cli.app, ["finish"], env=env)
    assert res.exit_code == 0 and "3 kalan sonuç" in res.stdout, res.output
    res = runner.invoke(cli.app, ["wait", "--timeout", "1"], env=env)
    assert res.exit_code == 0, res.output
    assert "urun" in res.stdout and "test_kodu" in res.stdout and "ortam" in res.stdout
    # Bitmiş koşuma yükleme 409 → uyarı, çıkış 0.
    res = runner.invoke(cli.app, ["upload", "--format", "junit", "--dir", str(DATA / "maven-junit")], env=env)
    assert res.exit_code == 0 and "409" in res.stderr


def test_cli_wrong_token_warns(served):
    env = {**GITLAB, "TRIAJ_URL": "http://test", "TRIAJ_TOKEN": "yanlis-token"}
    res = runner.invoke(cli.app, ["upload", "--format", "junit", "--dir", str(DATA / "maven-junit")], env=env)
    assert res.exit_code == 0 and "401" in res.stderr
    assert "yanlis-token" not in res.output


def test_cli_upload_with_context_dry_run(in_tmp):
    res = runner.invoke(
        cli.app,
        [
            "upload",
            "--format",
            "allure",
            "--dir",
            str(DATA / "maven-cucumber" / "allure-results"),
            "--context",
            "--repo",
            str(DATA.parent.parent / "tools" / "ornek" / "maven-cucumber"),
            "--dry-run",
        ],
        env=GITLAB,
    )
    assert res.exit_code == 0, res.output
    tests = json.loads(res.stdout)["tests"]
    with_ctx = [t for t in tests if t.get("context")]
    assert len(with_ctx) == 3
    roles = {f["role"] for t in with_ctx for f in t["context"]["files"]}
    assert roles == {"feature", "frame", "product"}
