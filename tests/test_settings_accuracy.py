from conftest import t
from triaj import decisions as dec


def test_settings_validation(client):
    r = client.put("/v1/projects/grup/p/settings", json={"auto_confidence": 1.5})
    assert r.status_code == 422
    assert client.put("/v1/projects/grup/p/settings", json={"flaky_window": 0}).status_code == 422
    assert client.put("/v1/projects/grup/p/settings", json={"env_patterns": [" "]}).status_code == 422
    assert client.put("/v1/projects/grup/p/settings", json={"bilinmeyen": 1}).status_code == 422

    r = client.put(
        "/v1/projects/grup/p/settings",
        json={
            "hints": "JavaFX Toolkit hatası ortamdır",
            "auto_confidence": 1.01,
            "env_patterns_extra": ["Grid dolu"],
        },
    )
    assert r.status_code == 200, r.text
    eff = r.json()["effective"]
    assert eff["auto_confidence"] == 1.01 and eff["hints"].startswith("JavaFX")
    assert "Grid dolu" in eff["env_patterns"] and "TimeoutException" in eff["env_patterns"]
    # Kısmi güncelleme diğer alanları korur; null sunucu varsayılanına döndürür.
    r = client.put("/v1/projects/grup/p/settings", json={"auto_confidence": None, "llm_enabled": True})
    stored = r.json()["stored"]
    assert "auto_confidence" not in stored and stored["hints"].startswith("JavaFX") and stored["llm_enabled"]
    assert r.json()["effective"]["auto_confidence"] == 0.7
    g = client.get("/v1/projects/grup/p/settings").json()
    assert g["stored"] == stored


def test_llm_accuracy_confusion_matrix(client, upload, engine):
    r = upload(
        [
            t("A", "failed", message="m1"),
            t("B", "broken", message="m2"),
            t("C", "failed", message="m3"),
            t("D", "failed", message="m4"),
        ],
        finish=True,
    )
    items = {
        i["test_key"]: i["result_id"] for i in client.get(f"/v1/runs/{r['run_id']}/triage").json()["items"]
    }
    with engine.begin() as c:
        dec.add(c, items["A"], "urun", "llm", "x", auto_confidence=0.7, confidence=0.9, model="m")
        dec.add(c, items["B"], "ortam", "llm", "x", auto_confidence=0.7, confidence=0.6, model="m")
        dec.add(c, items["C"], "test_kodu", "llm", "x", auto_confidence=0.7, confidence=0.95, model="m")
    for key, cat in {"A": "urun", "B": "test_kodu", "C": "test_kodu", "D": "urun"}.items():
        client.post(f"/v1/results/{items[key]}/decisions", json={"category": cat, "author": "admin"})
    # Admin'den SONRA gelen LLM kararı ölçüme girmez.
    with engine.begin() as c:
        dec.add(c, items["D"], "ortam", "llm", "geç", auto_confidence=0.7, confidence=0.99, model="m")

    acc = client.get("/v1/projects/p/llm-accuracy").json()
    assert acc["admin_decisions"] == 4
    llm = acc["llm"]
    assert llm["total"] == 3 and llm["correct"] == 2 and llm["accuracy"] == 0.667
    assert llm["matrix"]["test_kodu"]["ortam"] == 1
    assert llm["per_category"]["urun"] == {"precision": 1.0, "recall": 1.0, "support": 1}
    # Kural: A urun✓, B test_kodu✓, C urun✗, D urun✓
    assert acc["rule"]["total"] == 4 and acc["rule"]["correct"] == 3
    buckets = {(b["min"], b["max"]): b for b in acc["llm_by_confidence"]}
    assert buckets[(0.5, 0.7)] == {"min": 0.5, "max": 0.7, "n": 1, "accuracy": 0.0}
    assert buckets[(0.9, 1.0)]["n"] == 2 and buckets[(0.9, 1.0)]["accuracy"] == 1.0
    assert client.get("/v1/projects/yok/llm-accuracy").status_code == 404
