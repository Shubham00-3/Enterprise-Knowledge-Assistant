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
    owner_id: str,
    use_hybrid: bool = True,
    use_multi_query: bool = False,
    multi_query_count: int = 3,
) -> list[RetrievedChunk]:
    semantic_reliable = bool(getattr(embeddings, "semantic_reliable", True))

    # Multi-query / RAG-Fusion: expand the question into several phrasings, retrieve for
    # each, and fuse all rankings together. With the flag off this is exactly one query.
    queries = [question]
    if use_multi_query:
        queries = expand_queries(question, llm, multi_query_count)
    query_vectors = embeddings.embed(queries)

    is_postgres = session.get_bind().dialect.name == "postgresql"
    by_id: dict[str, tuple[Chunk, Document]] = {}
    dense_rankings: list[list[tuple[str, float]]] = []
    keyword_rankings: list[list[tuple[str, float]]] = []
    for query, vector in zip(queries, query_vectors, strict=True):
        candidates: Candidates | None = None
        if is_postgres:
            try:
                candidates = _postgres_candidates(session, query, vector, top_k, owner_id)
            except Exception as exc:  # pragma: no cover - requires a live Postgres + pgvector
                logger.warning("pgvector_retrieval_failed_fallback", error=str(exc))
                candidates = None
        if candidates is None:
            candidates = _python_candidates(session, query, vector, top_k, semantic_reliable, owner_id)
        q_by_id, dense, keyword = candidates
        by_id.update(q_by_id)
        dense_rankings.append(dense)
        keyword_rankings.append(keyword)

    if not by_id:
        return []
    any_keyword = any(score > 0 for ranking in keyword_rankings for _, score in ranking)
    if not semantic_reliable and not any_keyword:
        return []

    # Best signal per chunk across all query variants, used for calibration + confidence.
    dense_map = _best_scores(dense_rankings)
    keyword_map = _best_scores(keyword_rankings)
    if use_hybrid:
        fused = reciprocal_rank_fusion([*dense_rankings, *keyword_rankings])
        calibrated = {
            chunk_id: score
            + (0.5 * max(0.0, dense_map.get(chunk_id, 0.0)) if semantic_reliable else 0.0)
            + 2.0 * max(0.0, keyword_map.get(chunk_id, 0.0))
            for chunk_id, score in fused.items()
        }
    else:
        # Dense-only (semantic) ranking — used for the retrieval ablation baseline.
        calibrated = dict(dense_map)
    ranked = sorted(calibrated.items(), key=lambda item: item[1], reverse=True)[: max(top_k * 2, 12)]

    retrieved = [
        _to_retrieved(by_id[chunk_id], score, dense_map.get(chunk_id, 0.0))
        for chunk_id, score in ranked
        if chunk_id in by_id
    ]
    if use_rerank:
        retrieved = llm_rerank(question, retrieved, llm, rerank_top_k)
    return retrieved[:rerank_top_k]


def expand_queries(question: str, llm: LLMProvider, count: int = 3) -> list[str]:
    """Return the original question plus LLM-generated alternative phrasings (RAG-Fusion).

    Degrades to just [question] when the model is unavailable or returns nothing, so the
    feature is safe to enable without a key (it simply behaves like single-query retrieval).
    """
    queries = [question]
    if count <= 1:
        return queries
    result = llm.complete_json(
        "Rewrite the user's question as alternative search queries capturing different phrasings "
        "and sub-aspects. Keep each concise and standalone. Return JSON {\"queries\": [\"...\"]}.",
        f"Question: {question}\nReturn up to {count - 1} alternative queries.",
        "multi_query",
        use_utility=True,
    )
    variants = result.get("queries") if isinstance(result, dict) else None
    if isinstance(variants, list):
        for variant in variants:
            if isinstance(variant, str) and variant.strip() and variant.strip() not in queries:
                queries.append(variant.strip())
    return queries[:count]


def _best_scores(rankings: list[list[tuple[str, float]]]) -> dict[str, float]:
    """Collapse several (id, score) rankings into the best score seen per id."""
    best: dict[str, float] = {}
    for ranking in rankings:
        for chunk_id, score in ranking:
            if chunk_id not in best or score > best[chunk_id]:
                best[chunk_id] = score
    return best


def _python_candidates(
    session: Session,
    question: str,
    query_vector: list[float],
    top_k: int,
    semantic_reliable: bool,
    owner_id: str,
) -> Candidates:
    """Brute-force scoring used for the local SQLite path (small corpus)."""
    rows = session.execute(
        select(Chunk, Document)
        .join(Document, Chunk.document_id == Document.id)
        .where(Chunk.owner_id == owner_id)
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
    owner_id: str,
) -> Candidates:
    """Index-backed retrieval: pgvector (halfvec/HNSW cosine) + Postgres full-text search.

    Both retrievers are scoped to owner_id so one user's query can never surface
    another user's chunks.
    """
    vector_literal = "[" + ",".join(str(float(value)) for value in query_vector) + "]"
    limit = max(top_k * 2, 12)

    dense_rows = session.execute(
        text(
            "SELECT id, 1 - (embedding <=> CAST(:qvec AS halfvec)) AS sim "
            "FROM chunks WHERE embedding IS NOT NULL AND owner_id = :owner "
            "ORDER BY embedding <=> CAST(:qvec AS halfvec) LIMIT :limit"
        ),
        {"qvec": vector_literal, "limit": limit, "owner": owner_id},
    ).all()
    keyword_rows = session.execute(
        text(
            "SELECT id, ts_rank(tsv, websearch_to_tsquery('english', :q)) AS rank "
            "FROM chunks WHERE owner_id = :owner AND tsv @@ websearch_to_tsquery('english', :q) "
            "ORDER BY rank DESC LIMIT :limit"
        ),
        {"q": question, "limit": limit, "owner": owner_id},
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
