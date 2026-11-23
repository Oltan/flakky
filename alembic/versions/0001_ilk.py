"""İlk şema: projects, runs, results, decisions, llm_jobs, pending_context, llm_state.

Revision ID: 0001
Revises:
Create Date: 2026-11-23 06:35:28.426828
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_state",
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("last_call_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "projects",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False
        ),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column(
            "settings",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "runs",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False
        ),
        sa.Column("project_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("external_id", sa.String(length=300), nullable=False),
        sa.Column("branch", sa.String(length=300), nullable=False),
        sa.Column("commit_sha", sa.String(length=100), nullable=True),
        sa.Column(
            "meta",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "external_id", name="uq_runs_project_external"),
    )
    op.create_index(
        "ix_runs_project_branch_id",
        "runs",
        ["project_id", "branch", sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_table(
        "results",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False
        ),
        sa.Column("run_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("test_key", sa.String(length=1000), nullable=False),
        sa.Column("name", sa.String(length=1000), nullable=False),
        sa.Column("suite", sa.String(length=1000), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("is_latest", sa.Boolean(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("trace", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column(
            "steps",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "texts",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("signature", sa.String(length=64), nullable=True),
        sa.Column("current_category", sa.String(length=20), nullable=True),
        sa.Column("current_source", sa.String(length=10), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('passed','failed','broken','skipped')", name="ck_results_status"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "test_key", "attempt", name="uq_results_run_key_attempt"),
    )
    op.create_index("ix_results_run_latest", "results", ["run_id", "is_latest"], unique=False)
    op.create_index("ix_results_test_key", "results", ["test_key"], unique=False)
    op.create_table(
        "decisions",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False
        ),
        sa.Column("result_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("author", sa.String(length=200), nullable=True),
        sa.Column("reused_from", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category IN ('test_kodu','urun','ortam','flaky','bilinmiyor')", name="ck_decisions_category"
        ),
        sa.CheckConstraint("source IN ('rule','llm','admin')", name="ck_decisions_source"),
        sa.ForeignKeyConstraint(["result_id"], ["results.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reused_from"], ["decisions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_decisions_result", "decisions", ["result_id"], unique=False)
    op.create_table(
        "llm_jobs",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False
        ),
        sa.Column("result_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column("tries", sa.Integer(), nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "meta",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('queued','running','done','failed','skipped')", name="ck_llm_jobs_state"
        ),
        sa.ForeignKeyConstraint(["result_id"], ["results.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("result_id"),
    )
    op.create_index("ix_llm_jobs_state_next", "llm_jobs", ["state", "next_at"], unique=False)
    op.create_table(
        "pending_context",
        sa.Column("result_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["result_id"], ["results.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("result_id"),
    )


def downgrade() -> None:
    op.drop_table("pending_context")
    op.drop_index("ix_llm_jobs_state_next", table_name="llm_jobs")
    op.drop_table("llm_jobs")
    op.drop_index("ix_decisions_result", table_name="decisions")
    op.drop_table("decisions")
    op.drop_index("ix_results_test_key", table_name="results")
    op.drop_index("ix_results_run_latest", table_name="results")
    op.drop_table("results")
    op.drop_index("ix_runs_project_branch_id", table_name="runs")
    op.drop_table("runs")
    op.drop_table("projects")
    op.drop_table("llm_state")
