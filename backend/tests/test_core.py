from app.rag_core.generation.service import (
    CONFIDENCE_CEILING,
    confidence_score,
    generate_answer,
)
from app.rag_core.ingestion.chunker import chunk_pages
from app.rag_core.interfaces import LoadedPage, RetrievedChunk
from app.rag_core.pipeline import _select_source_chunks


class EmptyLLM:
    def complete_json(self, system: str, user: str, schema_name: str) -> dict:
        return {}

    def complete_text(self, system: str, user: str) -> str:
        return ""


class CitingLLM:
    """Stub generator that answers and cites a fixed set of chunk ids."""

    def __init__(self, cited_ids: list[str]) -> None:
        self.cited_ids = cited_ids

    def complete_json(self, system: str, user: str, schema_name: str) -> dict:
        return {
            "answer": "Employees receive 24 paid leaves annually.",
            "insufficient_context": False,
            "claims": [{"text": "24 paid leaves", "chunk_id": cid} for cid in self.cited_ids],
        }

    def complete_text(self, system: str, user: str) -> str:
        return ""


class SettingsStub:
    retrieval_threshold = 0.12
    groundedness_threshold = 0.65
    enable_groundedness_gate = True


def test_chunking_preserves_page_metadata() -> None:
    pages = [
        LoadedPage(text="# Policy\nEmployees receive 24 paid leaves annually.", page=1, title="HR"),
        LoadedPage(text="Remote work is allowed up to 3 days per week.", page=2, title="HR"),
    ]
    chunks = chunk_pages(pages, target_tokens=12, overlap_tokens=2)
    assert chunks
    assert chunks[0].page_start == 1
    assert chunks[-1].page_end == 2


def test_confidence_is_clamped() -> None:
    # A bounded heuristic never advertises a perfect 100%.
    assert confidence_score(100, 1.0) == CONFIDENCE_CEILING
    assert confidence_score(100, 1.0) < 1.0
    assert confidence_score(-1, 0.0) == 0.0


def test_generation_abstains_on_weak_evidence() -> None:
    result = generate_answer("Unknown?", [], EmptyLLM(), SettingsStub())
    assert result.status == "insufficient_context"
    assert result.confidence == 0.0
    assert result.groundedness is None
    assert "could not find" in result.answer.lower()
    assert result.cited_chunk_ids == []


def test_generation_falls_back_with_grounded_context() -> None:
    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="d1",
        document="HR.md",
        page=1,
        content="Employees receive 24 paid leaves annually.",
        score=0.2,
        similarity=0.5,
    )
    result = generate_answer("Leave?", [chunk], EmptyLLM(), SettingsStub())
    assert result.status == "answered"
    assert result.confidence > 0
    assert result.groundedness == 1.0
    assert "24 paid leaves" in result.answer


def _chunk(chunk_id: str, document: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id=f"doc-{chunk_id}",
        document=document,
        page=1,
        content=f"content for {document}",
        score=0.3,
        similarity=0.5,
    )


def test_sources_limited_to_cited_chunks() -> None:
    # Reproduces the reported bug: retrieval returns several off-topic chunks, but
    # the answer only cites the HR chunk -> only that source should surface.
    chunks = [
        _chunk("hr1", "HR_Policy_Handbook.md"),
        _chunk("it1", "IT_Access_Management.md"),
        _chunk("bill1", "Customer_FAQ_Billing.md"),
    ]
    result = generate_answer("paid leave?", chunks, CitingLLM(["hr1"]), SettingsStub())
    assert result.status == "answered"
    assert result.groundedness == 1.0
    assert result.cited_chunk_ids == ["hr1"]

    selected = _select_source_chunks(chunks, result)
    assert [c.chunk_id for c in selected] == ["hr1"]


def test_source_selection_drops_sources_when_abstaining() -> None:
    chunks = [_chunk("hr1", "HR_Policy_Handbook.md")]
    result = generate_answer("Unknown?", [], EmptyLLM(), SettingsStub())
    assert _select_source_chunks(chunks, result) == []


def test_source_selection_falls_back_to_top_chunks_without_citations() -> None:
    chunks = [_chunk(str(i), f"Doc{i}.md") for i in range(6)]
    # EmptyLLM returns no claims -> fall back to a bounded top-N, not the full set.
    result = generate_answer("Leave?", chunks, EmptyLLM(), SettingsStub())
    selected = _select_source_chunks(chunks, result)
    assert 0 < len(selected) <= 3


def test_chunks_never_span_pages() -> None:
    pages = [
        LoadedPage(text="# Intro\nalpha beta gamma", page=1, title="t"),
        LoadedPage(text="delta epsilon zeta", page=2, title="t"),
    ]
    chunks = chunk_pages(pages, target_tokens=50, overlap_tokens=5)
    assert all(chunk.page_start == chunk.page_end for chunk in chunks)
    assert {chunk.page_start for chunk in chunks} == {1, 2}


def test_confidence_does_not_saturate() -> None:
    # A mid-strength match should land strictly between the floor and a perfect score.
    assert 0.0 < confidence_score(0.3, 1.0) < 1.0


def test_auth_disabled_returns_seed_owner() -> None:
    from types import SimpleNamespace

    from app.auth import SEED_OWNER_ID, resolve_owner_id

    settings = SimpleNamespace(require_auth=False, supabase_jwt_secret=None, supabase_jwt_audience="authenticated")
    # No header needed when auth is off; everything maps to the shared seed owner.
    assert resolve_owner_id(None, settings) == SEED_OWNER_ID
    assert resolve_owner_id("Bearer anything", settings) == SEED_OWNER_ID


def test_auth_enabled_extracts_subject_from_valid_jwt() -> None:
    from types import SimpleNamespace

    import jwt

    from app.auth import resolve_owner_id

    settings = SimpleNamespace(
        require_auth=True, supabase_jwt_secret="topsecret", supabase_jwt_audience="authenticated"
    )
    token = jwt.encode({"sub": "user-abc", "aud": "authenticated"}, "topsecret", algorithm="HS256")
    assert resolve_owner_id(f"Bearer {token}", settings) == "user-abc"


def test_auth_enabled_rejects_bad_tokens() -> None:
    from types import SimpleNamespace

    import jwt
    from fastapi import HTTPException

    from app.auth import resolve_owner_id

    settings = SimpleNamespace(
        require_auth=True, supabase_jwt_secret="topsecret", supabase_jwt_audience="authenticated"
    )
    wrong_secret = jwt.encode({"sub": "x", "aud": "authenticated"}, "nope", algorithm="HS256")
    wrong_aud = jwt.encode({"sub": "x", "aud": "other"}, "topsecret", algorithm="HS256")
    for bad in (None, "topsecret", "Bearer not-a-jwt", f"Bearer {wrong_secret}", f"Bearer {wrong_aud}"):
        try:
            resolve_owner_id(bad, settings)
        except HTTPException as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError(f"expected 401 for {bad!r}")


def test_auth_enabled_without_secret_is_misconfig() -> None:
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.auth import resolve_owner_id

    settings = SimpleNamespace(require_auth=True, supabase_jwt_secret=None, supabase_jwt_audience="authenticated")
    try:
        resolve_owner_id("Bearer whatever", settings)
    except HTTPException as exc:
        assert exc.status_code == 500
    else:
        raise AssertionError("expected 500 when secret is unset")
