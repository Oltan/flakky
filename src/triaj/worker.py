"""LLM kuyruk tüketicisi: python -m triaj.worker

- İş alma: `next_at <= now` olan queued iş, Postgres'te FOR UPDATE SKIP LOCKED ile.
- Sınırlar küreseldir (birden çok worker süreci): running iş sayısı ve son çağrı zamanı DB'den
  okunur; Postgres'te kontrol + alma bir advisory lock altında yapılır.
- 429 → Retry-After (yoksa üstel geri çekilme) kadar iş ve tüm kuyruk bekletilir; tries artmaz.
- Zaman aşımı → doğrudan failed (tekrar denenmez). 5xx/bağlantı → tries+1, LLM_MAX_TRIES sonra failed.
- Geçersiz çıktı (düzeltme turundan sonra) ve 4xx → failed.
- LLM hatası kural kararını bozmaz: karar yalnızca başarıda eklenir.
- Çöken worker'dan kalan running işler geri alınır; çok uzun 429'da bekleyen işler süresi dolunca failed.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, func, select, text, update

from . import decisions as dec
from . import projects as proj
from .config import Settings, get_settings
from .db import is_postgres, llm_jobs, llm_state, make_engine, projects, results, runs
from .llm import limits
from .llm.client import (
    InvalidOutput,
    LlmRateLimited,
    LlmRequestError,
    LlmServerError,
    LlmTimeout,
    classify,
    make_client,
)
from .llm.prompt import build_messages
from .logsetup import setup_logging

log = logging.getLogger("triaj.worker")

LOCK_KEY = 727_1907  # advisory lock anahtarı
PAUSE_KEY = "paused_until"
LAST_CALL_KEY = "last_call"


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class Worker:
    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        client_factory: Callable | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.engine = engine
        self.settings = settings
        self.client = (client_factory or (lambda: make_client(settings)))()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.pg = is_postgres(engine)
        self._stop = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()

    # --- DB yardımcıları ---------------------------------------------------

    def _get_state(self, conn, key: str) -> datetime | None:
        row = conn.execute(select(llm_state.c.last_call_at).where(llm_state.c.key == key)).first()
        return _utc(row[0]) if row else None

    def _set_state(self, conn, key: str, value: datetime) -> None:
        res = conn.execute(update(llm_state).where(llm_state.c.key == key).values(last_call_at=value))
        if res.rowcount == 0:
            conn.execute(llm_state.insert().values(key=key, last_call_at=value))

    def maintenance(self) -> None:
        """Çöken worker'dan kalan running işleri geri al; süresi dolan queued işleri kapat."""
        now = self.clock()
        stale = now - timedelta(seconds=self.settings.llm_timeout_s + 60)
        expire = now - timedelta(hours=self.settings.llm_job_expire_h)
        with self.engine.begin() as c:
            rows = c.execute(
                select(llm_jobs.c.id, llm_jobs.c.tries).where(
                    llm_jobs.c.state == "running", llm_jobs.c.updated_at < stale
                )
            ).all()
            for r in rows:
                tries = r.tries + 1
                failed = tries >= self.settings.llm_max_tries
                c.execute(
                    update(llm_jobs)
                    .where(llm_jobs.c.id == r.id, llm_jobs.c.state == "running")
                    .values(
                        state="failed" if failed else "queued",
                        tries=tries,
                        next_at=now,
                        updated_at=now,
                        last_error="worker yanıt vermedi (geri alındı)",
                        **({"payload": None} if failed else {}),
                    )
                )
                log.warning("iş %s running'de kaldı, %s", r.id, "failed" if failed else "kuyruğa döndü")
            n = c.execute(
                update(llm_jobs)
                .where(llm_jobs.c.state == "queued", llm_jobs.c.created_at < expire)
                .values(state="failed", last_error="süresi doldu (expired)", payload=None, updated_at=now)
            ).rowcount
            if n:
                log.warning("%d iş süresi dolduğu için failed", n)

    def claim(self):
        """Sınırlar izin veriyorsa bir iş alır (running yapar) ve döner; yoksa None."""
        now = self.clock()
        with self.engine.begin() as c:
            if self.pg:
                c.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LOCK_KEY})
            paused = self._get_state(c, PAUSE_KEY)
            if paused and paused > now:
                return None
            running = c.execute(
                select(func.count()).select_from(llm_jobs).where(llm_jobs.c.state == "running")
            ).scalar_one()
            if running >= limits.concurrency(now, self.settings):
                return None
            gap = limits.gap_s(now, self.settings)
            if gap > 0:
                last = self._get_state(c, LAST_CALL_KEY)
                if last and (now - last).total_seconds() < gap:
                    return None
            lock = " FOR UPDATE SKIP LOCKED" if self.pg else ""
            row = c.execute(
                text(
                    "UPDATE llm_jobs SET state='running', updated_at=:now "
                    "WHERE id = (SELECT id FROM llm_jobs WHERE state='queued' AND next_at <= :now "
                    f"ORDER BY next_at, id LIMIT 1{lock}) "
                    "RETURNING id, result_id, tries, payload"
                ),
                {"now": now},
            ).first()
            if row is None:
                return None
            self._set_state(c, LAST_CALL_KEY, now)
            return row

    def _load(self, result_id: int):
        with self.engine.connect() as c:
            return c.execute(
                select(results, runs.c.project_id, projects.c.settings.label("project_settings"))
                .select_from(results.join(runs, runs.c.id == results.c.run_id).join(projects))
                .where(results.c.id == result_id)
            ).first()

    def _finish_job(self, job_id: int, **values) -> None:
        values.setdefault("updated_at", self.clock())
        with self.engine.begin() as c:
            c.execute(update(llm_jobs).where(llm_jobs.c.id == job_id).values(**values))

    # --- İşleme -----------------------------------------------------------

    async def process(self, job) -> str:
        row = self._load(job.result_id)
        if row is None:
            self._finish_job(job.id, state="failed", last_error="sonuç bulunamadı", payload=None)
            return "failed"
        eff = proj.effective(row.project_settings, self.settings)
        payload = job.payload
        if isinstance(payload, str):  # SQLite JSON bazı sürümlerde metin döner
            import json

            payload = json.loads(payload)
        messages, pinfo = build_messages(
            dict(row._mapping),
            payload,
            eff.hints,
            self.settings.llm_context_budget_tokens,
            self.settings.llm_json_mode,
        )
        meta = {
            "prompt_chars": pinfo.chars,
            "files_included": pinfo.files_included,
            "files_truncated": pinfo.files_truncated,
            "files_omitted": pinfo.files_omitted,
        }
        now = self.clock
        try:
            out, cinfo = await classify(self.client, self.settings, messages)
        except LlmRateLimited as e:
            wait = e.retry_after if e.retry_after is not None else limits.backoff_s(1)
            until = now() + timedelta(seconds=wait)
            with self.engine.begin() as c:
                self._set_state(c, PAUSE_KEY, until)
                c.execute(
                    update(llm_jobs)
                    .where(llm_jobs.c.id == job.id)
                    .values(state="queued", next_at=until, updated_at=now(), last_error="429 hız sınırı")
                )
            log.info("iş %s: 429, %.0f sn sonra tekrar", job.id, wait)
            return "queued"
        except LlmTimeout as e:
            self._finish_job(job.id, state="failed", last_error=str(e), payload=None, meta=meta)
            log.warning("iş %s: %s; kural kararı geçerli kalır", job.id, e)
            return "failed"
        except LlmServerError as e:
            tries = job.tries + 1
            if tries >= self.settings.llm_max_tries:
                self._finish_job(
                    job.id, state="failed", tries=tries, last_error=str(e), payload=None, meta=meta
                )
                log.warning("iş %s: %s, deneme hakkı bitti", job.id, e)
                return "failed"
            self._finish_job(
                job.id,
                state="queued",
                tries=tries,
                last_error=str(e),
                next_at=now() + timedelta(seconds=limits.backoff_s(tries)),
            )
            log.info("iş %s: %s, tekrar denenecek (%d/%d)", job.id, e, tries, self.settings.llm_max_tries)
            return "queued"
        except (InvalidOutput, LlmRequestError) as e:
            self._finish_job(job.id, state="failed", last_error=str(e), payload=None, meta=meta)
            log.warning("iş %s: %s", job.id, e)
            return "failed"
        except Exception as e:  # beklenmeyen: tekrar denenebilir say
            log.exception("iş %s: beklenmeyen hata", job.id)
            tries = job.tries + 1
            state = "failed" if tries >= self.settings.llm_max_tries else "queued"
            self._finish_job(
                job.id,
                state=state,
                tries=tries,
                last_error=f"beklenmeyen: {type(e).__name__}",
                next_at=now() + timedelta(seconds=limits.backoff_s(tries)),
                **({"payload": None} if state == "failed" else {}),
            )
            return state

        meta.update(
            elapsed_s=cinfo.elapsed_s,
            completion_tokens=cinfo.completion_tokens,
            prompt_tokens=cinfo.prompt_tokens,
            rounds=cinfo.rounds,
            served_model=cinfo.model,
        )
        with self.engine.begin() as c:
            dec.add(
                c,
                row.id,
                out.category,
                "llm",
                out.reason,
                auto_confidence=eff.auto_confidence,
                confidence=out.confidence,
                model=self.settings.llm_model,
            )
            c.execute(
                update(llm_jobs)
                .where(llm_jobs.c.id == job.id)
                .values(state="done", payload=None, meta=meta, last_error=None, updated_at=now())
            )
        log.info("iş %s: %s (%.2f) %.1f sn", job.id, out.category.value, out.confidence, cinfo.elapsed_s)
        return "done"

    async def run_once(self) -> list[str]:
        """Sınırlar izin verdiği kadar iş alıp eşzamanlı işler; testler ve tek atış için."""
        self.maintenance()
        jobs = []
        while (job := self.claim()) is not None:
            jobs.append(job)
        return list(await asyncio.gather(*(self.process(j) for j in jobs)))

    async def run_forever(self) -> None:
        log.info("worker başladı (model=%s, mod=%s)", self.settings.llm_model, self.settings.llm_json_mode)
        last_maint = 0.0
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            if loop.time() - last_maint > 30:
                try:
                    self.maintenance()
                except Exception:
                    log.exception("bakım hatası")
                last_maint = loop.time()
            claimed = False
            try:
                job = self.claim()
            except Exception:
                log.exception("iş alma hatası")
                job = None
            if job is not None:
                claimed = True
                t = asyncio.create_task(self.process(job))
                self._tasks.add(t)
                t.add_done_callback(self._tasks.discard)
            if not claimed:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.settings.worker_poll_s)
                except TimeoutError:
                    pass
            else:
                await asyncio.sleep(0)
        if self._tasks:
            log.info("durduruluyor: %d iş bitiriliyor", len(self._tasks))
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def stop(self) -> None:
        self._stop.set()


async def _amain() -> None:
    settings = get_settings()
    setup_logging(settings.secrets)
    w = Worker(make_engine(settings.database_url), settings)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, w.stop)
        except NotImplementedError:  # pragma: no cover
            pass
    await w.run_forever()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
