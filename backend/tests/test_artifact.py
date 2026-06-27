"""Document artifact endpoint for lightweight citations."""

import os
from collections.abc import Generator

os.environ.setdefault("DATABASE_URL", "sqlite://")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.auth import current_owner_id  # noqa: E402
from app.db import Base, get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Chunk, Document  # noqa: E402


def _make_session() -> sessionmaker[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with TestSession() as session:
        session.add(
            Document(
                id="alice-doc",
                owner_id="alice",
                filename="HR.md",
                title="HR",
                doc_type="md",
                checksum="alice-sum",
                num_pages=2,
                status="indexed",
            )
        )
        session.add(
            Chunk(
                id="alice-c1",
                document_id="alice-doc",
                owner_id="alice",
                chunk_index=0,
                content="Paid leave is 24 days per year.",
                page_start=1,
                page_end=1,
                section_title="Paid Leave",
                token_count=8,
                embedding_json="[]",
                search_text="Paid leave is 24 days per year.",
            )
        )
        session.add(
            Chunk(
                id="alice-c2",
                document_id="alice-doc",
                owner_id="alice",
                chunk_index=1,
                content="Remote work is allowed up to 3 days per week.",
                page_start=2,
                page_end=2,
                section_title="Remote Work",
                token_count=10,
                embedding_json="[]",
                search_text="Remote work is allowed up to 3 days per week.",
            )
        )
        session.add(
            Document(
                id="bob-doc",
                owner_id="bob",
                filename="Private.md",
                title="Private",
                doc_type="md",
                checksum="bob-sum",
                num_pages=1,
                status="indexed",
            )
        )
        session.commit()
    return TestSession


def test_artifact_returns_ordered_chunks_for_current_owner() -> None:
    TestSession = _make_session()
    previous = dict(app.dependency_overrides)

    def _get_session() -> Generator[Session, None, None]:
        with TestSession() as session:
            yield session

    try:
        app.dependency_overrides[get_session] = _get_session
        app.dependency_overrides[current_owner_id] = lambda: "alice"
        response = TestClient(app).get("/documents/alice-doc/artifact")
    finally:
        app.dependency_overrides = previous

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == "alice-doc"
    assert body["document"] == "HR.md"
    assert [chunk["chunk_id"] for chunk in body["chunks"]] == ["alice-c1", "alice-c2"]
    assert body["chunks"][0]["section_title"] == "Paid Leave"


def test_artifact_rejects_documents_outside_owner_scope() -> None:
    TestSession = _make_session()
    previous = dict(app.dependency_overrides)

    def _get_session() -> Generator[Session, None, None]:
        with TestSession() as session:
            yield session

    try:
        app.dependency_overrides[get_session] = _get_session
        app.dependency_overrides[current_owner_id] = lambda: "alice"
        response = TestClient(app).get("/documents/bob-doc/artifact")
    finally:
        app.dependency_overrides = previous

    assert response.status_code == 404

