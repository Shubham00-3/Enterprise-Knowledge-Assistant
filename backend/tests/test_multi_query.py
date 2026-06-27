"""Multi-query / RAG-Fusion retrieval (Phase 3).

Verifies query expansion, that the fusion path runs retrieval for every variant, and
that owner isolation still holds when multi-query is enabled.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Chunk, Document
from app.rag_core.retrieval.service import expand_queries, retrieve
from app.rag_core.utils import dumps_embedding, stable_embedding

DIMS = 64


class EmptyLLM:
    def complete_json(self, system, user, schema_name, use_utility=False):
        return {}

    def complete_text(self, system, user):
        return ""


class VariantLLM:
    """Returns two alternative phrasings for the multi-query expansion call."""

    def __init__(self):
        self.embedded_batches = []

    def complete_json(self, system, user, schema_name, use_utility=False):
        return {"queries": ["leave entitlement", "time off policy"]}

    def complete_text(self, system, user):
        return ""


class SpyEmbeddings:
    semantic_reliable = True

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [stable_embedding(t, DIMS) for t in texts]


def test_expand_queries_returns_original_plus_variants() -> None:
    queries = expand_queries("paid leave policy", VariantLLM(), count=3)
    assert queries[0] == "paid leave policy"
    assert "leave entitlement" in queries
    assert len(queries) <= 3


def test_expand_queries_degrades_without_llm() -> None:
    # No usable model output -> behaves like single-query retrieval.
    assert expand_queries("paid leave policy", EmptyLLM(), count=3) == ["paid leave policy"]


def _seed_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as session:
        for owner, cid, text in [
            ("alice", "a1", "Employees get 24 paid leave days per year after probation."),
            ("alice", "a2", "Vacation entitlement and time off requests need manager approval."),
            ("bob", "b1", "Bob's private handbook: leave is 30 days and codename Falcon."),
        ]:
            session.add(Document(id=f"doc-{cid}", owner_id=owner, filename=f"{cid}.md", title=cid,
                                 doc_type="md", checksum=f"sum-{cid}", num_pages=1, status="indexed"))
            session.add(Chunk(id=cid, document_id=f"doc-{cid}", owner_id=owner, chunk_index=0,
                              content=text, page_start=1, page_end=1, section_title=None, token_count=8,
                              embedding_json=dumps_embedding(stable_embedding(text, DIMS)), search_text=text))
        session.commit()
    return Session


def test_multi_query_runs_each_variant_and_stays_isolated() -> None:
    Session = _seed_session()
    spy = SpyEmbeddings()
    with Session() as session:
        hits = retrieve(
            session=session, question="leave policy", embeddings=spy, llm=VariantLLM(),
            top_k=8, rerank_top_k=6, use_rerank=False, owner_id="alice",
            use_hybrid=True, use_multi_query=True, multi_query_count=3,
        )
    # The expansion produced 3 queries (original + 2 variants) and all were embedded together.
    assert spy.calls and len(spy.calls[0]) == 3
    ids = {c.chunk_id for c in hits}
    assert ids  # alice gets results
    assert "b1" not in ids  # bob's private chunk never leaks, even with multi-query fan-out


def test_single_query_embeds_once() -> None:
    Session = _seed_session()
    spy = SpyEmbeddings()
    with Session() as session:
        retrieve(
            session=session, question="leave policy", embeddings=spy, llm=EmptyLLM(),
            top_k=8, rerank_top_k=6, use_rerank=False, owner_id="alice",
            use_hybrid=True, use_multi_query=False,
        )
    assert len(spy.calls[0]) == 1
