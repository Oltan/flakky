from pathlib import Path

from triaj_cli.adapters.allure import read_allure
from triaj_cli.adapters.junit import read_junit
from triaj_cli.context import Repo, attach_context, collect_for, cucumber_regex, frames_in, role_for

ROOT = Path(__file__).parent.parent
DATA = ROOT / "tests" / "data"
ORNEK = ROOT / "tools" / "ornek"


def ctx_of(records, name):
    r = next(r for r in reversed(records) if r["name"] == name)
    return {f["path"]: f for f in r.get("context", {}).get("files", [])}


def test_context_java_frame_maps_file():
    recs = read_junit(DATA / "maven-junit")
    assert attach_context(recs, ORNEK / "maven-junit") == 3
    files = ctx_of(recs, "testKoduHatasi")
    f = files["src/test/java/ornek/FiyatTest.java"]
    assert f["role"] == "frame"
    assert ">>   28| " in f["content"]  # hata satırı işaretli
    # Test sınıfının depo içi importu yok; ürün sınıfı aynı pakette olduğu için eklenmez.
    assert all(not r.get("context") for r in recs if r["status"] == "passed")


def test_context_python_frame_maps_file():
    recs = read_allure(DATA / "pytest" / "allure-results")
    attach_context(recs, ORNEK / "pytest")
    files = ctx_of(recs, "test_kdv_urun_hatasi")
    assert ">>   22| " in files["tests/test_sepet.py"]["content"]
    assert "tests/hesap.py" in files  # import edilen modül
    # Python stdlib çerçevesi (/usr/lib/python…) depoya eşlenmez.
    assert all("socket.py" not in p for p in ctx_of(recs, "test_odeme_servisi_ortam"))
    # Yalnızca son denemesi kalanlara bağlam eklenir; flaky (son deneme passed) almaz.
    assert ctx_of(recs, "test_flaky_ilk_denemede_kalir") == {}


def test_context_cucumber_step_definition():
    recs = read_allure(DATA / "maven-cucumber" / "allure-results")
    attach_context(recs, ORNEK / "maven-cucumber")
    files = ctx_of(recs, "KDV dahil toplam")
    assert list(files) == [
        "src/test/resources/ozellikler/sepet.feature",
        "src/test/java/ornek/adimlar/SepetAdimlari.java",
        "src/main/java/ornek/Sepet.java",
    ]
    assert files["src/test/resources/ozellikler/sepet.feature"]["role"] == "feature"
    assert (
        ">>   12|     Then KDV dahil toplam 120.0 TL olmalı"
        in files["src/test/resources/ozellikler/sepet.feature"]["content"]
    )

    # İz olmadan da adım metninden adım tanımı bulunur.
    rec = next(r for r in reversed(recs) if r["name"] == "KDV dahil toplam")
    rec = {**rec, "trace": None}
    found = {f["path"]: f for f in collect_for(rec, Repo(ORNEK / "maven-cucumber"), 200)}
    steps = found["src/test/java/ornek/adimlar/SepetAdimlari.java"]
    assert steps["role"] == "steps" and '@Then("KDV dahil toplam {double} TL olmalı")' in steps["content"]


def test_cucumber_expressions():
    assert cucumber_regex("sepete {double} TL'lik ürün eklenir").match("sepete 100.0 TL'lik ürün eklenir")
    assert cucumber_regex("{int} ürün(ler) var").match("3 ürünler var")
    assert cucumber_regex("{int} ürün(ler) var").match("1 ürün var")
    assert cucumber_regex("kullanıcı {string} ile giriş yapar").match('kullanıcı "ali" ile giriş yapar')
    assert cucumber_regex("sayfa açılır/kapanır").match("sayfa kapanır")
    assert cucumber_regex(r"^(\d+) kalem$").match("5 kalem")
    assert not cucumber_regex("toplam {double} TL olmalı").match("KDV dahil toplam 120.0 TL olmalı")


def test_context_max_kb(tmp_path):
    big = "\n".join(f"    satir_{i} = {i}" for i in range(5000))
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_buyuk.py").write_text(big)
    trace = 'File "tests/test_buyuk.py", line 2500, in test_x\n'
    rec = {"test_key": "k", "name": "n", "status": "failed", "attempt": 1, "trace": trace, "steps": []}
    files = collect_for(rec, Repo(tmp_path), max_kb=2)
    assert len(files) == 1 and files[0]["truncated"]
    assert len(files[0]["content"].encode()) <= 2 * 1024 + 20
    files = collect_for(rec, Repo(tmp_path), max_kb=200)
    assert not files[0]["truncated"] and files[0]["content"].count("\n") == 160


def test_context_marks_product_paths():
    assert role_for("src/main/java/a/B.java") == "product"
    assert role_for("src/test/java/a/BTest.java") == "frame"
    assert role_for("tests/test_a.py") == "frame"
    assert role_for("app/servis.py") == "product"
    assert role_for("ozellik/a.feature") == "feature"


def test_frames_in_order():
    tr = (
        "java.lang.X\n\tat a.b.C$Inner.m(C.java:42)\n\tat a.b.D.lambda$x$0(D.kt:7)\n"
        '  File "/w/app/x.py", line 3, in f\n'
        "\tat ✽.adım(classpath:oz/a.feature:16)\n"
    )
    assert frames_in(tr) == [
        ("java", "a/b/C.java", 42),
        ("java", "a/b/D.kt", 7),
        ("py", "/w/app/x.py", 3),
        ("feature", "oz/a.feature", 16),
    ]
