"""add owner_id for per-user isolation

Adds owner_id to documents and chunks, backfills existing rows to the seed owner,
indexes it for per-user retrieval filtering, and makes document dedup per-owner
instead of global.

Revision ID: 0002_add_owner_id
Revises: 0001_initial_schema
Create Date: 2026-06-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_add_owner_id"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_OWNER_ID = "public-seed"


def upgrade() -> None:
    # NOT NULL + server_default backfills every existing row to the seed owner in one shot.
    op.add_column(
        "documents",
        sa.Column("owner_id", sa.String(length=64), nullable=False, server_default=SEED_OWNER_ID),
    )
    op.add_column(
        "chunks",
        sa.Column("owner_id", sa.String(length=64), nullable=False, server_default=SEED_OWNER_ID),
    )
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"])
    op.create_index("ix_chunks_owner_id", "chunks", ["owner_id"])

    # Dedup is now per owner: different users may upload the same file.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # IF EXISTS so an unattended prod deploy can't crash if the auto-named unique
        # constraint is absent or named differently.
        op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_checksum_key")
    op.create_unique_constraint("uq_owner_checksum", "documents", ["owner_id", "checksum"])


def downgrade() -> None:
    op.drop_constraint("uq_owner_checksum", "documents", type_="unique")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_unique_constraint("documents_checksum_key", "documents", ["checksum"])
    op.drop_index("ix_chunks_owner_id", table_name="chunks")
    op.drop_index("ix_documents_owner_id", table_name="documents")
    op.drop_column("chunks", "owner_id")
    op.drop_column("documents", "owner_id")
