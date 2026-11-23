"""Tablolar (SQLAlchemy Core) ve engine.

Postgres'te tablolar `triaj` şemasında durur; bağlantı `search_path=triaj` ile açıldığından
SQL metinleri şema öneki olmadan iki arka uçta da (Postgres, SQLite) çalışır.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Engine,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

SCHEMA = "triaj"

metadata = MetaData()

ID = BigInteger().with_variant(Integer(), "sqlite")
JSONT = JSON().with_variant(JSONB(), "postgresql")
TS = DateTime(timezone=True)


def now() -> datetime:
    return datetime.now(UTC)


projects = Table(
    "projects",
    metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("name", String(300), nullable=False, unique=True),
    Column("settings", JSONT, nullable=False, default=dict),
    Column("created_at", TS, nullable=False, default=now),
)

runs = Table(
    "runs",
    metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("project_id", ID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    Column("external_id", String(300), nullable=False),
    Column("branch", String(300), nullable=False),
    Column("commit_sha", String(100), nullable=True),
    Column("meta", JSONT, nullable=False, default=dict),
    Column("created_at", TS, nullable=False, default=now),
    Column("finished_at", TS, nullable=True),
    UniqueConstraint("project_id", "external_id", name="uq_runs_project_external"),
)
Index("ix_runs_project_branch_id", runs.c.project_id, runs.c.branch, runs.c.id.desc())

results = Table(
    "results",
    metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("run_id", ID, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
    Column("test_key", String(1000), nullable=False),
    Column("name", String(1000), nullable=False),
    Column("suite", String(1000), nullable=True),
    Column("status", String(20), nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("is_latest", Boolean, nullable=False, default=True),
    Column("message", Text, nullable=True),
    Column("trace", Text, nullable=True),
    Column("duration_ms", BigInteger, nullable=True),
    Column("steps", JSONT, nullable=False, default=list),
    Column("texts", JSONT, nullable=False, default=list),
    Column("signature", String(64), nullable=True),
    Column("current_category", String(20), nullable=True),
    Column("current_source", String(10), nullable=True),
    Column("created_at", TS, nullable=False, default=now),
    UniqueConstraint("run_id", "test_key", "attempt", name="uq_results_run_key_attempt"),
    CheckConstraint("status IN ('passed','failed','broken','skipped')", name="ck_results_status"),
)
Index("ix_results_test_key", results.c.test_key)
Index("ix_results_run_latest", results.c.run_id, results.c.is_latest)

decisions = Table(
    "decisions",
    metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("result_id", ID, ForeignKey("results.id", ondelete="CASCADE"), nullable=False),
    Column("category", String(20), nullable=False),
    Column("source", String(10), nullable=False),
    Column("reason", Text, nullable=True),
    Column("confidence", Float, nullable=True),
    Column("model", String(200), nullable=True),
    Column("author", String(200), nullable=True),
    Column("reused_from", ID, ForeignKey("decisions.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", TS, nullable=False, default=now),
    CheckConstraint("source IN ('rule','llm','admin')", name="ck_decisions_source"),
    CheckConstraint(
        "category IN ('test_kodu','urun','ortam','flaky','bilinmiyor')", name="ck_decisions_category"
    ),
)
Index("ix_decisions_result", decisions.c.result_id)

llm_jobs = Table(
    "llm_jobs",
    metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("result_id", ID, ForeignKey("results.id", ondelete="CASCADE"), nullable=False, unique=True),
    Column("state", String(10), nullable=False),
    Column("tries", Integer, nullable=False, default=0),
    Column("next_at", TS, nullable=False, default=now),
    Column("last_error", Text, nullable=True),
    Column("payload", JSONT, nullable=True),
    Column("meta", JSONT, nullable=False, default=dict),
    Column("created_at", TS, nullable=False, default=now),
    Column("updated_at", TS, nullable=False, default=now),
    CheckConstraint("state IN ('queued','running','done','failed','skipped')", name="ck_llm_jobs_state"),
)
Index("ix_llm_jobs_state_next", llm_jobs.c.state, llm_jobs.c.next_at)

# Ingest ile finish arasında bağlamı (dosya parçaları, log) tutar; finish'te iş payload'ına
# taşınır ya da silinir. Kalıcı kod saklanmaz.
pending_context = Table(
    "pending_context",
    metadata,
    Column("result_id", ID, ForeignKey("results.id", ondelete="CASCADE"), primary_key=True),
    Column("payload", JSONT, nullable=False),
)

# Küresel LLM hız sınırı koordinasyonu (birden çok worker süreci için).
llm_state = Table(
    "llm_state",
    metadata,
    Column("key", String(50), primary_key=True),
    Column("last_call_at", TS, nullable=True),
)


def normalize_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def is_postgres(engine: Engine) -> bool:
    return engine.dialect.name == "postgresql"


def make_engine(url: str, **kw) -> Engine:
    url = normalize_url(url)
    if url.startswith("sqlite"):
        kw.setdefault("connect_args", {"check_same_thread": False})
        engine = create_engine(url, **kw)

        @event.listens_for(engine, "connect")
        def _fk(dbapi_conn, _rec):  # pragma: no cover - basit pragma
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    kw.setdefault("connect_args", {"options": f"-csearch_path={SCHEMA}"})
    kw.setdefault("pool_pre_ping", True)
    return create_engine(url, **kw)


def create_schema(engine: Engine) -> None:
    """Testler ve geliştirme için: şemayı göç olmadan kurar."""
    if is_postgres(engine):
        with engine.begin() as c:
            c.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    metadata.create_all(engine)


def drop_schema(engine: Engine) -> None:
    if is_postgres(engine):
        with engine.begin() as c:
            c.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    else:
        metadata.drop_all(engine)
