"""Per-user data isolation (Phase 1).

Uses an in-memory SQLite DB and deterministic fallback embeddings to prove that
retrieval and the full /ask pipeline only ever see the asking user's own chunks.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import SEED_OWNER_ID, Settings
from app.db import Base
from app.models import Chunk, Document
from app.rag_core.pipeline import answer_question
from app.rag_core.retrieval.service import retrieve
from app.rag_core.utils import dumps_embedding, stable_embedding

DIMS = 64


class FakeEmbeddings:
    semantic_reliable = True

    def embed(self, texts):
        return [stable_embedding(t, DIMS) for t in texts]


class CitingLLM:
    def complete_json(self, system, user, schema_name, use_utility=False):
        return {
            "answer": "Paid leave is 24 days per year.",
            "insufficient_context": False,
            "claims": [{"text": "24 days", "chunk_id": "alice-chunk"}],
        }

    def complete_text(self, system, user):
        return ""


def _make_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as session:
        _add_chunk(session, owner="alice", chunk_id="alice-chunk", doc="alice-doc",
                   text="Paid leave is 24 days per year for all employees.")
        _add_chunk(session, owner="bob", chunk_id="bob-chunk", doc="bob-doc",
                   text="Paid leave is 30 days per year and the secret project is codename Falcon.")
        _add_chunk(session, owner=SEED_OWNER_ID, chunk_id="seed-429", doc="seed-api",
                   text="API clients should retry 429 responses with exponential backoff.")
        session.commit()
    return Session


def _add_chunk(session, owner, chunk_id, doc, text):
    session.add(Document(id=doc, owner_id=owner, filename=f"{doc}.md", title=doc,
                         doc_type="md", checksum=f"sum-{chunk_id}", num_pages=1, status="indexed"))
    session.add(Chunk(id=chunk_id, document_id=doc, owner_id=owner, chunk_index=0, content=text,
                      page_start=1, page_end=1, section_title=None, token_count=5,
                      embedding_json=dumps_embedding(stable_embedding(text, DIMS)), search_text=text))


def _settings():
    return Settings(
        database_url="sqlite://", embed_dims=DIMS, openai_api_key=None,
        enable_query_rewrite=False, enable_llm_rerank=False, enable_hybrid=True,
        enable_groundedness_gate=True, retrieval_threshold=0.0,
    )


def test_retrieval_is_scoped_to_owner() -> None:
    Session = _make_db()
    with Session() as session:
        alice_hits = retrieve(
            session=session, question="paid leave policy", embeddings=FakeEmbeddings(),
            llm=CitingLLM(), top_k=8, rerank_top_k=6, use_rerank=False, owner_id="alice",
        )
    owners = {c.chunk_id for c in alice_hits}
    assert owners  # alice sees her own chunk
    assert "bob-chunk" not in owners  # and never bob's


def test_retrieval_includes_shared_seed_corpus_for_authenticated_users() -> None:
    Session = _make_db()
    with Session() as session:
        alice_hits = retrieve(
            session=session, question="what should api clients do for 429 responses",
            embeddings=FakeEmbeddings(), llm=CitingLLM(), top_k=8, rerank_top_k=6,
            use_rerank=False, owner_id="alice",
        )
    ids = {c.chunk_id for c in alice_hits}
    assert "seed-429" in ids
    assert "bob-chunk" not in ids


def test_ask_pipeline_never_leaks_other_users_sources() -> None:
    Session = _make_db()
    with Session() as session:
        resp = answer_question(
            session=session, question="What is the paid leave policy?", conversation_id=None,
            settings=_settings(), embeddings=FakeEmbeddings(), llm=CitingLLM(), owner_id="alice",
        )
    docs = {s.document for s in resp.sources}
    assert docs == {"alice-doc.md"}
    assert "bob-doc.md" not in docs
    assert resp.sources[0].document_id == "alice-doc"
    assert resp.sources[0].chunk_id == "alice-chunk"
    assert resp.metrics.citation_count == 1
    assert resp.metrics.groundedness == 1.0
    assert resp.metrics.status == resp.status
    assert resp.metrics.latency_ms == resp.latency_ms
    assert set(resp.metrics.model_dump()) == {
        "confidence",
        "groundedness",
        "citation_count",
        "status",
        "latency_ms",
    }


def test_insufficient_context_returns_no_sources_and_valid_metrics() -> None:
    Session = _make_db()
    with Session() as session:
        resp = answer_question(
            session=session, question="What is the private roadmap codename?", conversation_id=None,
            settings=_settings(), embeddings=FakeEmbeddings(), llm=CitingLLM(), owner_id="carol",
        )
    assert resp.status == "insufficient_context"
    assert resp.sources == []
    assert resp.metrics.confidence == 0.0
    assert resp.metrics.groundedness is None
    assert resp.metrics.citation_count == 0
    assert resp.metrics.status == resp.status
    assert resp.metrics.latency_ms == resp.latency_ms
    assert set(resp.metrics.model_dump()) == {
        "confidence",
        "groundedness",
        "citation_count",
        "status",
        "latency_ms",
    }


def test_bob_query_cannot_see_alice_data() -> None:
    # Bob asks the same question; he can see his private chunk and the shared corpus,
    # but must never see Alice's private chunk.
    Session = _make_db()
    with Session() as session:
        bob_hits = retrieve(
            session=session, question="paid leave policy", embeddings=FakeEmbeddings(),
            llm=CitingLLM(), top_k=8, rerank_top_k=6, use_rerank=False, owner_id="bob",
        )
    ids = {c.chunk_id for c in bob_hits}
    assert "bob-chunk" in ids
    assert "alice-chunk" not in ids
