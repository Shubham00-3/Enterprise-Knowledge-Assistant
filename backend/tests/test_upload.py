"""End-to-end test of the authenticated /upload flow (Phase 2).

Drives the real FastAPI endpoint with TestClient (which runs the background indexing
task before the response returns), against an in-memory DB and a no-key embedding
provider, and asserts the uploaded file is indexed and scoped to the caller.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.main as main_module  # noqa: E402
from app.auth import current_owner_id  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.db import Base, get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Chunk, Document  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(engine)
TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _get_session():
    with TestSession() as session:
        yield session


def _settings():
    return Settings(database_url="sqlite://", embed_dims=64, openai_api_key=None, max_upload_mb=10)


app.dependency_overrides[get_session] = _get_session
app.dependency_overrides[get_settings] = _settings
app.dependency_overrides[current_owner_id] = lambda: "alice"
main_module.SessionLocal = TestSession  # the background task opens its own session

client = TestClient(app)


def test_upload_indexes_under_caller() -> None:
    resp = client.post(
        "/upload",
        files={"file": ("notes.md", b"# Notes\nPaid leave is 24 days per year.", "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["filename"] == "notes.md"
    assert body["status"] in ("processing", "indexed")

    # The TestClient ran the background task; the document should now be indexed and owned by alice.
    with TestSession() as session:
        doc = session.scalars(select(Document).where(Document.owner_id == "alice")).one()
        assert doc.status == "indexed"
        chunks = session.scalars(select(Chunk)).all()
        assert chunks
        assert all(c.owner_id == "alice" for c in chunks)


def test_upload_rejects_unsupported_extension() -> None:
    resp = client.post(
        "/upload",
        files={"file": ("malware.exe", b"not a document", "application/octet-stream")},
    )
    assert resp.status_code == 400
