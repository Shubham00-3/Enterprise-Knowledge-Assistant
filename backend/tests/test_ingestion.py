"""Ingestion building blocks used by the authenticated upload flow (Phase 2)."""

import os
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Chunk, Document
from app.rag_core.ingestion.service import index_document_file, ingest_file
from app.rag_core.utils import stable_embedding

DIMS = 64


class FakeEmbeddings:
    semantic_reliable = True

    def embed(self, texts):
        return [stable_embedding(t, DIMS) for t in texts]


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _write(content: str) -> Path:
    fd, name = tempfile.mkstemp(suffix=".md")
    os.close(fd)  # Windows: release the handle before reopening to write.
    Path(name).write_text(content, encoding="utf-8")
    return Path(name)


def test_ingest_file_indexes_under_owner() -> None:
    Session = _session()
    path = _write("# Handbook\nPaid leave is 24 days per year for employees.")
    with Session() as session:
        written = ingest_file(session, path, FakeEmbeddings(), owner_id="alice", original_name="HR.md")
        session.commit()
        assert written and written > 0
        doc = session.scalars(select(Document)).one()
        assert doc.owner_id == "alice"
        assert doc.filename == "HR.md"
        assert doc.status == "indexed"
        chunk_owners = set(session.scalars(select(Chunk.owner_id)).all())
        assert chunk_owners == {"alice"}
    path.unlink(missing_ok=True)


def test_ingest_file_dedups_per_owner() -> None:
    Session = _session()
    path = _write("# Doc\nSame content uploaded by two different people.")
    with Session() as session:
        assert ingest_file(session, path, FakeEmbeddings(), owner_id="alice") is not None
        # Same file, same owner -> duplicate.
        assert ingest_file(session, path, FakeEmbeddings(), owner_id="alice") is None
        # Same file, different owner -> allowed (their own copy).
        assert ingest_file(session, path, FakeEmbeddings(), owner_id="bob") is not None
        session.commit()
        owners = set(session.scalars(select(Document.owner_id)).all())
        assert owners == {"alice", "bob"}
    path.unlink(missing_ok=True)


def test_index_document_file_fills_pending_document() -> None:
    # Mirrors the upload flow: a 'processing' doc is pre-created, then indexed in the background.
    Session = _session()
    path = _write("# Policy\nRefunds are available within 30 days of purchase.")
    with Session() as session:
        doc = Document(owner_id="carol", filename="policy.md", title="policy", doc_type="md",
                       checksum="abc123", num_pages=0, status="processing")
        session.add(doc)
        session.flush()
        written = index_document_file(session, doc, path, FakeEmbeddings())
        session.commit()
        assert written > 0
        assert doc.status == "indexed"
        assert session.scalar(select(func.count()).select_from(Chunk)) == written
    path.unlink(missing_ok=True)
