"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-06-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("doc_type", sa.String(length=50), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False, unique=True),
        sa.Column("num_pages", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="indexed"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("document_id", sa.String(length=36), sa.ForeignKey("documents.id", ondelete="CASCADE")),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("page_end", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("section_title", sa.String(length=255)),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding_json", sa.Text()),
        sa.Column("search_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk"),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("last_active", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("conversation_id", sa.String(length=36), sa.ForeignKey("conversations.id", ondelete="CASCADE")),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sources_json", sa.Text()),
        sa.Column("confidence", sa.Float()),
        sa.Column("status", sa.String(length=50)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("prompt_tokens", sa.Integer()),
        sa.Column("completion_tokens", sa.Integer()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "feedback",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("message_id", sa.String(length=36), sa.ForeignKey("messages.id", ondelete="CASCADE")),
        sa.Column("rating", sa.String(length=10), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("metrics_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    if is_postgres:
        op.execute("ALTER TABLE chunks ADD COLUMN embedding halfvec(3072)")
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw "
            "ON chunks USING hnsw (embedding halfvec_cosine_ops)"
        )
        op.execute("ALTER TABLE chunks ADD COLUMN tsv tsvector")
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_chunks_tsv_gin ON chunks USING gin (tsv)"
        )
        op.execute(
            "CREATE OR REPLACE FUNCTION chunks_tsv_trigger() RETURNS trigger AS $$ "
            "begin new.tsv := to_tsvector('english', coalesce(new.search_text, '')); return new; end "
            "$$ LANGUAGE plpgsql"
        )
        op.execute(
            "CREATE TRIGGER chunks_tsv_update BEFORE INSERT OR UPDATE ON chunks "
            "FOR EACH ROW EXECUTE FUNCTION chunks_tsv_trigger()"
        )


def downgrade() -> None:
    op.drop_table("eval_runs")
    op.drop_table("feedback")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("chunks")
    op.drop_table("documents")
