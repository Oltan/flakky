"""Alembic göçü tablolarla (db.metadata) birebir aynı şemayı üretmeli."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from triaj.db import SCHEMA, drop_schema, is_postgres, make_engine, metadata


def _cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = url
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(params=["sqlite", "postgres"])
def url(request, tmp_path):
    if request.param == "sqlite":
        return f"sqlite:///{tmp_path / 'm.db'}"
    from conftest import PG_URL

    if not PG_URL:
        pytest.skip("TRIAJ_TEST_PG_URL yok")
    drop_schema(make_engine(PG_URL))
    return PG_URL


def test_migrations_match_metadata(url):
    command.upgrade(_cfg(url), "head")
    eng = make_engine(url)
    with eng.connect() as conn:
        opts = {"include_object": lambda o, n, t, *_: not (t == "table" and n == "alembic_version")}
        if is_postgres(eng):
            opts["version_table_schema"] = SCHEMA
        diff = compare_metadata(MigrationContext.configure(conn, opts=opts), metadata)
    assert diff == []
    command.downgrade(_cfg(url), "base")
    if is_postgres(eng):
        drop_schema(eng)
    eng.dispose()
