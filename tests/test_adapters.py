"""Dönüştürücüler gerçek araç çıktılarıyla (tests/data, tools/ornek/uret.sh) test edilir."""

from pathlib import Path

from triaj_cli.adapters.allure import read_allure
from triaj_cli.adapters.junit import read_junit

DATA = Path(__file__).parent / "data"


def by_name(records):
    out: dict[str, list] = {}
    for r in records:
        out.setdefault(r["name"], []).append(r)
    return out


def test_adapter_allure_attempts_and_containers():
    recs = by_name(read_allure(DATA / "pytest" / "allure-results"))
    flaky = recs["test_flaky_ilk_denemede_kalir"]
    assert [(r["attempt"], r["status"]) for r in flaky] == [(1, "failed"), (2, "passed")]
    assert len({r["test_key"] for r in flaky}) == 1
    # Adım eki
    assert flaky[0]["texts"][0]["content"] == "deneme=1"

    kdv = recs["test_kdv_urun_hatasi"]
    assert [r["attempt"] for r in kdv] == [1, 2, 3] and all(r["status"] == "failed" for r in kdv)
    assert "beklenen 120" in kdv[0]["message"] and "test_sepet.py" in kdv[0]["trace"]
    assert [s["name"] for s in kdv[0]["steps"]] == ["KDV eklenir", "Sonuç 120 olmalı"]
    # Fixture (container) ekleri children UUID'si ile bağlandı: önce before, sonra after.
    names = [t["name"] for t in kdv[0]["texts"]]
    assert names[0].startswith("before:sepet") and names[-1].startswith("after:sepet")
    assert kdv[0]["texts"][0]["content"] == "sepet kuruldu"

    assert recs["test_test_kodu_hatasi"][0]["status"] == "broken"
    assert recs["test_atlanan"][0]["status"] == "skipped"
    assert recs["test_indirim_dogru"][0]["status"] == "passed"


def test_adapter_allure_container_order_independent(tmp_path):
    """Container sonuçtan önce de yazılmış olsa eşleşme aynı kalır (dosya adı sırası değişince)."""
    src = DATA / "pytest" / "allure-results"
    for p in src.iterdir():
        name = p.name
        if name.endswith("-container.json"):
            name = "0000" + name  # alfabetik olarak sonuçlardan önce
        (tmp_path / name).write_bytes(p.read_bytes())
    a = {(r["test_key"], r["attempt"]): r["texts"] for r in read_allure(src)}
    b = {(r["test_key"], r["attempt"]): r["texts"] for r in read_allure(tmp_path)}
    assert a == b


def test_adapter_allure_cucumber():
    recs = by_name(read_allure(DATA / "maven-cucumber" / "allure-results"))
    kdv = recs["KDV dahil toplam"][0]
    assert kdv["suite"] == "Sepet"
    assert kdv["_meta"]["full_name"].startswith("ozellikler/sepet.feature:")
    assert [s["name"] for s in kdv["steps"]][-1] == "Then KDV dahil toplam 120.0 TL olmalı"
    assert {t["name"].split("/")[-1] for t in kdv["texts"]} == {"kurulum-log", "temizlik-log"}
    on = recs["Önbellekli sepet"]
    assert [(r["attempt"], r["status"]) for r in on] == [(1, "failed"), (2, "passed")]
    assert "TimeoutException" in recs["Ödeme yapılır"][0]["trace"]


def test_adapter_junit_surefire_flaky():
    recs = {}
    for r in read_junit(DATA / "maven-junit"):
        recs.setdefault(r["test_key"], []).append(r)
    fl = recs["ornek.FiyatTest#flakyIlkDenemedeKalir"]
    assert [(r["attempt"], r["status"]) for r in fl] == [(1, "failed"), (2, "passed")]
    assert "önbellek" in fl[0]["message"] and "AssertionFailedError" in fl[0]["trace"]
    kdv = recs["ornek.FiyatTest#kdvUrunHatasi"]
    assert [r["status"] for r in kdv] == ["failed"] * 3
    assert "FiyatTest.java" in kdv[0]["trace"]
    assert [r["status"] for r in recs["ornek.FiyatTest#odemeServisiOrtam"]] == ["broken"] * 3
    assert recs["ornek.FiyatTest#atlanan"][0]["status"] == "skipped"
    assert recs["ornek.FiyatTest#indirimDogru"][0]["status"] == "passed"


def test_adapter_junit_pytest_xml():
    recs = {}
    for r in read_junit(DATA / "pytest" / "junit.xml"):
        recs.setdefault(r["test_key"], []).append(r)
    fl = recs["tests.test_sepet#test_flaky_ilk_denemede_kalir"]
    assert [r["status"] for r in fl] == ["failed", "passed"]
    kdv = recs["tests.test_sepet#test_kdv_urun_hatasi"]
    assert [r["status"] for r in kdv] == ["failed"] * 3
    assert "beklenen 120" in kdv[-1]["message"]
    assert recs["tests.test_sepet#test_atlanan"][0]["status"] == "skipped"


def test_adapter_junit_xxe_not_resolved(tmp_path):
    secret = tmp_path / "gizli.txt"
    secret.write_text("GIZLI")
    (tmp_path / "r.xml").write_text(
        f'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file://{secret}">]>'
        '<testsuite name="s"><testcase classname="c" name="n"><failure message="&e;">x</failure>'
        "</testcase></testsuite>"
    )
    recs = read_junit(tmp_path / "r.xml")
    assert all("GIZLI" not in (r["message"] or "") for r in recs)
