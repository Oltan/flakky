"""Uçtan uca senaryo: çalışan api + worker + sahte LLM'e karşı.

    docker compose up -d --build
    python tools/senaryo.py --url http://localhost:8000 --token yerel-token --llm http://localhost:8001

Worker'ın LLM_TIMEOUT_S değeri --slow-s'den küçük olmalı (yavaş mod senaryosu için).
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid

import httpx

OK = "\033[32mGEÇTİ\033[0m"
FAIL = "\033[31mKALDI\033[0m"


class Senaryo:
    def __init__(self, url: str, token: str, llm: str):
        self.api = httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        self.llm = httpx.Client(base_url=llm, timeout=10)
        self.project = f"senaryo/{uuid.uuid4().hex[:8]}"
        self.failures = 0
        self.n = 0

    def check(self, name: str, cond: bool, detail: str = "") -> None:
        print(f"  [{OK if cond else FAIL}] {name}" + (f" — {detail}" if detail and not cond else ""))
        if not cond:
            self.failures += 1

    def run(self, tests: list[dict], commit: str, finish: bool = True) -> int:
        self.n += 1
        r = self.api.post(
            "/v1/runs",
            json={
                "project": self.project,
                "branch": "main",
                "commit_sha": commit,
                "external_id": f"koşum-{self.n}",
                "tests": tests,
            },
        )
        r.raise_for_status()
        rid = r.json()["run_id"]
        if finish:
            self.api.post(f"/v1/runs/{rid}/finish").raise_for_status()
        return rid

    def triage(self, rid: int) -> dict:
        return {i["test_key"]: i for i in self.api.get(f"/v1/runs/{rid}/triage").json()["items"]}

    def wait_llm(self, rid: int, timeout: float) -> dict:
        end = time.monotonic() + timeout
        while True:
            tri = self.triage(rid)
            if (
                all(i["llm_state"] not in ("queued", "running") for i in tri.values())
                or time.monotonic() > end
            ):
                return tri
            time.sleep(0.5)

    def mode(self, **kw) -> None:
        self.llm.post("/_mode", json=kw).raise_for_status()

    def main(self, slow_s: float) -> int:
        print(f"proje: {self.project}")
        self.api.put(f"/v1/projects/{self.project}/settings", json={"llm_enabled": True}).raise_for_status()
        self.mode(mode="ok")

        print("1) Aynı commit: kaldı → kaldı → geçti ⇒ flaky")
        r1 = self.run([{"test_key": "A", "status": "failed", "message": "beklenen 1"}], "c1")
        self.run([{"test_key": "A", "status": "failed", "message": "beklenen 1"}], "c1")
        r3 = self.run([{"test_key": "A", "status": "passed"}], "c1")
        fl = {
            i["test_key"]: i
            for i in self.api.get(f"/v1/projects/{self.project}/flaky", params={"branch": "main"}).json()
        }
        self.check("A flaky listesinde", "A" in fl, str(fl))
        ev = fl.get("A", {}).get("evidence", [])
        self.check(
            "kanıt ilk kalan koşum → geçen koşum",
            any(e["failed_run"] == r1 and e["passed_run"] == r3 for e in ev),
            str(ev),
        )

        print("2) Commit değişince yeni hata flaky sayılmaz")
        self.run([{"test_key": "B", "status": "passed"}], "c1")
        self.run([{"test_key": "B", "status": "failed", "message": "yeni hata"}], "c2")
        fl = {
            i["test_key"]
            for i in self.api.get(f"/v1/projects/{self.project}/flaky", params={"branch": "main"}).json()
        }
        self.check("B flaky değil", "B" not in fl, str(fl))

        print("3) finish → kural kararı anında, worker LLM kararı yazar")
        tests = [
            {"test_key": "U", "status": "failed", "message": "expected 120 but was 118"},
            {"test_key": "K", "status": "broken", "message": "NullPointerException"},
            {"test_key": "O", "status": "failed", "message": "Connection refused"},
        ]
        rid = self.run(tests, "c3")
        tri = self.triage(rid)
        self.check("kural kararları hazır", all(i["rule_decision"] for i in tri.values()))
        tri = self.wait_llm(rid, 30)
        got = {k: (v["decision"]["source"], v["decision"]["category"]) for k, v in tri.items()}
        self.check(
            "LLM kararları yazıldı",
            got == {"U": ("llm", "urun"), "K": ("llm", "test_kodu"), "O": ("llm", "ortam")},
            str(got),
        )

        print("4) Sahte LLM 429 (Retry-After 3 sn) → iş bekleyip biter")
        self.mode(mode="429_once", retry_after=3)
        t0 = time.monotonic()
        rid = self.run([{"test_key": "R", "status": "failed", "message": "expected x but was y"}], "c4")
        tri = self.wait_llm(rid, 30)
        took = time.monotonic() - t0
        self.check("429 sonrası karar", tri["R"]["llm_state"] == "done", str(tri["R"]["llm_state"]))
        self.check("Retry-After'a uyuldu (≥3 sn)", took >= 3, f"{took:.1f} sn")

        print(f"5) Sahte LLM yavaş ({slow_s} sn) → zaman aşımı, kural kararı kalır")
        self.mode(mode="slow", slow_s=slow_s)
        rid = self.run([{"test_key": "S", "status": "failed", "message": "expected a but was b"}], "c5")
        tri = self.wait_llm(rid, slow_s + 30)
        s = tri["S"]
        self.check("iş failed", s["llm_state"] == "failed", str(s["llm_state"]))
        self.check(
            "kural kararı geçerli", s["decision"]["source"] == "rule" and s["next_action"] == "open_bug"
        )
        self.mode(mode="ok")

        print("6) Admin kararı ve isabet ölçümü")
        rid_u = tri["S"]["result_id"]
        self.api.post(f"/v1/results/{rid_u}/decisions", json={"category": "urun", "author": "senaryo"})
        acc = self.api.get(f"/v1/projects/{self.project}/llm-accuracy").json()
        self.check("llm-accuracy yanıt veriyor", acc["admin_decisions"] == 1, str(acc))

        print("\nSONUÇ:", "tüm adımlar geçti" if not self.failures else f"{self.failures} kontrol kaldı")
        return 1 if self.failures else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--token", default="yerel-token")
    ap.add_argument("--llm", default="http://localhost:8001")
    ap.add_argument("--slow-s", type=float, default=8.0)
    a = ap.parse_args()
    sys.exit(Senaryo(a.url, a.token, a.llm).main(a.slow_s))


if __name__ == "__main__":
    main()
