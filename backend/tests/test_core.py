from app.rag_core.generation.service import confidence_score, generate_answer
from app.rag_core.ingestion.chunker import chunk_pages
from app.rag_core.interfaces import LoadedPage, RetrievedChunk


class EmptyLLM:
    def complete_json(self, system: str, user: str, schema_name: str) -> dict:
        return {}

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
    assert confidence_score(100, 1.0) == 1.0
    assert confidence_score(-1, 0.0) == 0.0


def test_generation_abstains_on_weak_evidence() -> None:
    answer, confidence, status = generate_answer("Unknown?", [], EmptyLLM(), SettingsStub())
    assert status == "insufficient_context"
    assert confidence == 0.0
    assert "could not find" in answer.lower()


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
    answer, confidence, status = generate_answer("Leave?", [chunk], EmptyLLM(), SettingsStub())
    assert status == "answered"
    assert confidence > 0
    assert "24 paid leaves" in answer


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


def test_bearer_auth_gate() -> None:
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.main import verify_bearer

    # Disabled -> no-op regardless of header.
    verify_bearer(None, SimpleNamespace(require_auth=False, api_auth_token=None))

    enabled = SimpleNamespace(require_auth=True, api_auth_token="secret")
    verify_bearer("Bearer secret", enabled)  # valid token passes
    for bad in (None, "secret", "Bearer wrong"):
        try:
            verify_bearer(bad, enabled)
        except HTTPException as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError(f"expected 401 for {bad!r}")
