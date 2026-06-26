import re

import structlog
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Chunk, Document
from app.rag_core.interfaces import EmbeddingProvider, LLMProvider, RetrievedChunk
from app.rag_core.utils import cosine, loads_embedding, reciprocal_rank_fusion

logger = structlog.get_logger()

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "can",
    "do",
    "does",
    "for",
    "how",
    "i",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
    "when",
    "where",
    "who",
    "why",
}

# (chunk_id -> score) candidate list
Candidates = tuple[dict[str, tuple[Chunk, Document]], list[tuple[str, float]], list[tuple[str, float]]]


def keyword_score(question: str, content: str) -> float:
    terms = {term for term in re.findall(r"[a-z0-9]+", question.lower()) if term not in STOPWORDS}
    words = re.findall(r"[a-z0-9]+", content.lower())
    if not terms or not words:
        return 0.0
    counts = {term: words.count(term) for term in terms}
    return sum(counts.values()) / max(len(words), 1)


def rewrite_query(question: str, conversation: list[str], llm: LLMProvider, enabled: bool) -> str:
    if not enabled or not conversation:
        return question
    rewritten = llm.complete_text(
        "Rewrite follow-up questions as standalone search queries. Return only the rewritten query.",
        f"Conversation:\n{chr(10).join(conversation[-6:])}\n\nQuestion: {question}",
    )
    return rewritten or question


def retrieve(
    session: Session,
    question: str,
    embeddings: EmbeddingProvider,
    llm: LLMProvider,
    top_k: int,
    rerank_top_k: int,
    use_rerank: bool,
    use_hybrid: bool = True,
) -> list[RetrievedChunk]:
    query_vector = embeddings.embed([question])[0]
    semantic_reliable = bool(getattr(embeddings, "semantic_reliable", True))

    candidates: Candidates | None = None
    if session.get_bind().dialect.name == "postgresql":
        try:
            candidates = _postgres_candidates(session, question, query_vector, top_k)
        except Exception as exc:  # pragma: no cover - requires a live Postgres + pgvector
            logger.warning("pgvector_retrieval_failed_fallback", error=str(exc))
            candidates = None
    if candidates is None:
        candidates = _python_candidates(session, question, query_vector, top_k, semantic_reliable)

    by_id, dense, keyword = candidates
    if not by_id:
        return []
    if not semantic_reliable and not any(score > 0 for _, score in keyword):
        return []

    dense_map = dict(dense)
    keyword_map = dict(keyword)
    if use_hybrid:
        fused = reciprocal_rank_fusion([dense, keyword])
        calibrated = {
            chunk_id: score
            + (0.5 * max(0.0, dense_map.get(chunk_id, 0.0)) if semantic_reliable else 0.0)
            + 2.0 * max(0.0, keyword_map.get(chunk_id, 0.0))
            for chunk_id, score in fused.items()
        }
    else:
        # Dense-only (semantic) ranking — used for the retrieval ablation baseline.
        calibrated = {chunk_id: similarity for chunk_id, similarity in dense}
    ranked = sorted(calibrated.items(), key=lambda item: item[1], reverse=True)[: max(top_k * 2, 12)]

    retrieved = [
        _to_retrieved(by_id[chunk_id], score, dense_map.get(chunk_id, 0.0))
        for chunk_id, score in ranked
        if chunk_id in by_id
    ]
    if use_rerank:
        retrieved = llm_rerank(question, retrieved, llm, rerank_top_k)
    return retrieved[:rerank_top_k]


def _python_candidates(
    session: Session,
    question: str,
    query_vector: list[float],
    top_k: int,
    semantic_reliable: bool,
) -> Candidates:
    """Brute-force scoring used for the local SQLite path (small corpus)."""
    rows = session.execute(
        select(Chunk, Document).join(Document, Chunk.document_id == Document.id)
    ).all()
    dense: list[tuple[str, float]] = []
    keyword: list[tuple[str, float]] = []
    by_id: dict[str, tuple[Chunk, Document]] = {}
    for chunk, document in rows:
        by_id[chunk.id] = (chunk, document)
        dense.append((chunk.id, cosine(query_vector, loads_embedding(chunk.embedding_json))))
        keyword.append((chunk.id, keyword_score(question, chunk.search_text or chunk.content)))

    dense = sorted(dense, key=lambda item: item[1], reverse=True)[: top_k * 2]
    keyword = sorted(keyword, key=lambda item: item[1], reverse=True)[: top_k * 2]
    return by_id, dense, keyword


def _postgres_candidates(
    session: Session,
    question: str,
    query_vector: list[float],
    top_k: int,
) -> Candidates:
    """Index-backed retrieval: pgvector (halfvec/HNSW cosine) + Postgres full-text search."""
    vector_literal = "[" + ",".join(str(float(value)) for value in query_vector) + "]"
    limit = max(top_k * 2, 12)

    dense_rows = session.execute(
        text(
            "SELECT id, 1 - (embedding <=> CAST(:qvec AS halfvec)) AS sim "
            "FROM chunks WHERE embedding IS NOT NULL "
            "ORDER BY embedding <=> CAST(:qvec AS halfvec) LIMIT :limit"
        ),
        {"qvec": vector_literal, "limit": limit},
    ).all()
    keyword_rows = session.execute(
        text(
            "SELECT id, ts_rank(tsv, websearch_to_tsquery('english', :q)) AS rank "
            "FROM chunks WHERE tsv @@ websearch_to_tsquery('english', :q) "
            "ORDER BY rank DESC LIMIT :limit"
        ),
        {"q": question, "limit": limit},
    ).all()

    dense = [(row.id, float(row.sim)) for row in dense_rows]
    keyword = [(row.id, float(row.rank)) for row in keyword_rows]
    candidate_ids = {chunk_id for chunk_id, _ in dense} | {chunk_id for chunk_id, _ in keyword}
    if not candidate_ids:
        return {}, [], []

    rows = session.execute(
        select(Chunk, Document)
        .join(Document, Chunk.document_id == Document.id)
        .where(Chunk.id.in_(candidate_ids))
    ).all()
    by_id = {chunk.id: (chunk, document) for chunk, document in rows}
    return by_id, dense, keyword


def llm_rerank(
    question: str, chunks: list[RetrievedChunk], llm: LLMProvider, top_k: int
) -> list[RetrievedChunk]:
    if not chunks:
        return []
    prompt = "\n\n".join(
        f"[{i}] {chunk.document} p.{chunk.page}: {chunk.content[:900]}" for i, chunk in enumerate(chunks)
    )
    result = llm.complete_json(
        "Rank chunks by usefulness for answering the question. Return JSON: {\"ranked_ids\":[0,1]}",
        f"Question: {question}\n\nChunks:\n{prompt}",
        "rerank",
        use_utility=True,
    )
    ranked_ids = result.get("ranked_ids") if isinstance(result, dict) else None
    if not isinstance(ranked_ids, list):
        return chunks[:top_k]
    ordered: list[RetrievedChunk] = []
    seen: set[int] = set()
    for raw in ranked_ids:
        if isinstance(raw, int) and 0 <= raw < len(chunks) and raw not in seen:
            ordered.append(chunks[raw])
            seen.add(raw)
    ordered.extend(chunk for i, chunk in enumerate(chunks) if i not in seen)
    return ordered[:top_k]


def _to_retrieved(row: tuple[Chunk, Document], score: float, similarity: float = 0.0) -> RetrievedChunk:
    chunk, document = row
    return RetrievedChunk(
        chunk_id=chunk.id,
        document_id=document.id,
        document=document.filename,
        page=chunk.page_start,
        content=chunk.content,
        score=score,
        section_title=chunk.section_title,
        similarity=max(0.0, similarity),
    )
