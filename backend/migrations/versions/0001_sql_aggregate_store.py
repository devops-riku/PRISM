"""Create the portable PRISM aggregate store.

Revision ID: 0001_sql_aggregate_store
Revises: None
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_sql_aggregate_store"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    portable_json = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    if "aggregate_records" not in existing:
        op.create_table(
            "aggregate_records",
            sa.Column("scope", sa.String(length=64), nullable=False),
            sa.Column("kind", sa.String(length=48), nullable=False),
            sa.Column("record_id", sa.String(length=255), nullable=False),
            sa.Column("payload", portable_json, nullable=False),
            sa.Column("sort_key", sa.String(length=64), nullable=False),
            sa.Column("lookup_key", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.String(length=32), nullable=False),
            sa.Column("updated_at", sa.String(length=32), nullable=False),
            sa.PrimaryKeyConstraint("scope", "kind", "record_id", name="pk_aggregate_records"),
            sa.UniqueConstraint(
                "kind", "lookup_key", name="uq_aggregate_records_kind_lookup_key"
            ),
        )
        op.create_index(
            "ix_aggregate_records_scope_kind_sort",
            "aggregate_records",
            ["scope", "kind", "sort_key"],
            unique=False,
        )

    if "reference_counters" not in existing:
        op.create_table(
            "reference_counters",
            sa.Column("scope", sa.String(length=64), nullable=False),
            sa.Column("series", sa.String(length=32), nullable=False),
            sa.Column("next_value", sa.BigInteger(), nullable=False),
            sa.PrimaryKeyConstraint("scope", "series", name="pk_reference_counters"),
        )

    if "legacy_imports" not in existing:
        op.create_table(
            "legacy_imports",
            sa.Column("scope", sa.String(length=64), nullable=False),
            sa.Column("kind", sa.String(length=48), nullable=False),
            sa.Column("record_id", sa.String(length=255), nullable=False),
            sa.Column("imported_at", sa.String(length=32), nullable=False),
            sa.PrimaryKeyConstraint("scope", "kind", "record_id", name="pk_legacy_imports"),
        )


def downgrade() -> None:
    op.drop_table("legacy_imports")
    op.drop_table("reference_counters")
    op.drop_index("ix_aggregate_records_scope_kind_sort", table_name="aggregate_records")
    op.drop_table("aggregate_records")
