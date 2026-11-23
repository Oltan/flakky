"""triaj servisine HTTP istemcisi. Token hiçbir çıktıda görünmez."""

from __future__ import annotations

import time
from typing import Any

import httpx


class TriajError(Exception):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


class Client:
    def __init__(self, url: str, token: str, timeout: float = 30.0, retries: int = 2):
        self.http = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        self.retries = retries

    def close(self) -> None:
        self.http.close()

    def _req(self, method: str, path: str, **kw) -> Any:
        last: Exception | None = None
        for i in range(self.retries + 1):
            try:
                r = self.http.request(method, path, **kw)
            except httpx.HTTPError as e:
                last = TriajError(f"sunucuya ulaşılamadı: {type(e).__name__}")
            else:
                if r.status_code < 400:
                    return r.json()
                detail = _detail(r)
                if r.status_code < 500:
                    raise TriajError(f"HTTP {r.status_code}: {detail}", r.status_code)
                last = TriajError(f"HTTP {r.status_code}: {detail}", r.status_code)
            if i < self.retries:
                time.sleep(min(2**i, 5))
        assert last is not None
        raise last

    def post_run(self, body: dict) -> dict:
        return self._req("POST", "/v1/runs", json=body)

    def finish(self, run_id: int) -> dict:
        return self._req("POST", f"/v1/runs/{run_id}/finish")

    def triage(self, run_id: int) -> dict:
        return self._req("GET", f"/v1/runs/{run_id}/triage")


def _detail(r: httpx.Response) -> str:
    try:
        d = r.json().get("detail")
    except ValueError:
        return r.text[:200]
    return str(d)[:500]
