"""Alembic ortamı. URL DATABASE_URL'den (ya da -x url=...) okunur."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import text

from triaj.config import Settings
from triaj.db import SCHEMA, is_postgres, make_engine, metadata

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)


def _url() -> str:
    x = context.get_x_argument(as_dictionary=True)
    return x.get("url") or config.attributes.get("url") or Settings().database_url


def run_migrations_online() -> None:
    engine = make_engine(_url())
    with engine.connect() as conn:
        pg = is_postgres(engine)
        if pg:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
            conn.commit()
        context.configure(
            connection=conn,
            target_metadata=metadata,
            version_table_schema=SCHEMA if pg else None,
            render_as_batch=not pg,
            compare_type=True,
            include_object=lambda obj, name, type_, *_: not (type_ == "table" and name == "alembic_version"),
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("offline mod desteklenmiyor")
run_migrations_online()
