from dataclasses import dataclass

from app.config import Settings
from app.rag_core.interfaces import LLMProvider, RetrievedChunk
from app.rag_core.utils import snippet

# A bounded heuristic should not advertise absolute certainty, so the displayed
# confidence is capped below 1.0 (i.e. never "100%").
CONFIDENCE_CEILING = 0.95


@dataclass(frozen=True)
class AnswerResult:
    answer: str
    confidence: float
    status: str
    groundedness: float | None
    # chunk_ids the model actually cited, in claim order; drives which sources are shown.
    cited_chunk_ids: list[str]


def generate_answer(
    question: str,
    chunks: list[RetrievedChunk],
    llm: LLMProvider,
    settings: Settings,
) -> AnswerResult:
    if not chunks or chunks[0].score < settings.retrieval_threshold:
        return AnswerResult(
            "I could not find this information in the knowledge base.",
            0.0,
            "insufficient_context",
            None,
            [],
        )

    context = "\n\n".join(
        f"[{chunk.chunk_id}] {chunk.document} page {chunk.page}\n{chunk.content}" for chunk in chunks
    )
    result = llm.complete_json(
        "You are an enterprise knowledge assistant. Answer ONLY from the supplied context and "
        "cite the chunk ids you used. Synthesize across multiple sources when the answer spans "
        "more than one document. If the context does not actually contain the answer, set "
        "insufficient_context to true and briefly say you could not find it in the knowledge base.",
        f"Question: {question}\n\nContext:\n{context}\n\n"
        "Return JSON: {\"answer\":\"...\", \"insufficient_context\": false, "
        "\"claims\":[{\"text\":\"...\", \"chunk_id\":\"...\"}]}",
        "answer",
    )
    insufficient = bool(result.get("insufficient_context")) if isinstance(result, dict) else False
    answer = result.get("answer") if isinstance(result, dict) else None
    if not answer:
        answer = fallback_answer(question, chunks)

    cited = cited_chunk_ids(result, chunks)
    grounded_ratio = groundedness_ratio(result, chunks) if settings.enable_groundedness_gate else 1.0
    confidence = confidence_score(chunks[0].similarity, grounded_ratio)
    if insufficient:
        return AnswerResult(
            answer.strip(),
            round(min(confidence, 0.3), 2),
            "insufficient_context",
            None,
            [],
        )
    if grounded_ratio < settings.groundedness_threshold:
        return AnswerResult(
            "I could not find enough supported information in the knowledge base.",
            round(min(confidence, 0.3), 2),
            "insufficient_context",
            None,
            [],
        )
    return AnswerResult(answer.strip(), confidence, "answered", round(grounded_ratio, 2), cited)


def fallback_answer(question: str, chunks: list[RetrievedChunk]) -> str:
    lead = chunks[0]
    return (
        f"Based on {lead.document} page {lead.page}, the relevant policy says: "
        f"{snippet(lead.content, 650)}"
    )


def cited_chunk_ids(result: dict, chunks: list[RetrievedChunk]) -> list[str]:
    """The chunk ids the model attributed claims to, restricted to chunks we actually
    retrieved and de-duplicated. Empty when the model returned no usable claims."""
    if not isinstance(result, dict):
        return []
    claims = result.get("claims")
    if not isinstance(claims, list):
        return []
    available = {chunk.chunk_id for chunk in chunks}
    ordered: list[str] = []
    for claim in claims:
        if isinstance(claim, dict):
            chunk_id = claim.get("chunk_id")
            if isinstance(chunk_id, str) and chunk_id in available and chunk_id not in ordered:
                ordered.append(chunk_id)
    return ordered


def groundedness_ratio(result: dict, chunks: list[RetrievedChunk]) -> float:
    if not result:
        return 1.0
    claims = result.get("claims")
    if not isinstance(claims, list) or not claims:
        return 1.0
    available = {chunk.chunk_id for chunk in chunks}
    supported = sum(1 for claim in claims if isinstance(claim, dict) and claim.get("chunk_id") in available)
    return supported / len(claims)


def confidence_score(similarity: float, grounded_ratio: float) -> float:
    """Documented heuristic (NOT a calibrated probability): blend top-evidence semantic
    similarity with the share of answer claims that map back to retrieved chunks.

    text-embedding-3-large cosine for a strong match typically lands ~0.45-0.65, so we
    map that band onto [0, 1] instead of treating 0.6 as perfect, and cap the result at
    CONFIDENCE_CEILING so the UI never reports an unearned 100%."""
    normalized = max(0.0, min(1.0, (similarity - 0.15) / 0.45))
    blended = 0.5 * normalized + 0.5 * grounded_ratio
    return round(min(CONFIDENCE_CEILING, max(0.0, blended)), 2)
