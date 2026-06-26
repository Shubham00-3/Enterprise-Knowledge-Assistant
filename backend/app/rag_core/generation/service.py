from app.config import Settings
from app.rag_core.interfaces import LLMProvider, RetrievedChunk
from app.rag_core.utils import snippet


def generate_answer(
    question: str,
    chunks: list[RetrievedChunk],
    llm: LLMProvider,
    settings: Settings,
) -> tuple[str, float, str]:
    if not chunks or chunks[0].score < settings.retrieval_threshold:
        return ("I could not find this information in the knowledge base.", 0.0, "insufficient_context")

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

    grounded_ratio = groundedness_ratio(result, chunks) if settings.enable_groundedness_gate else 1.0
    confidence = confidence_score(chunks[0].similarity, grounded_ratio)
    if insufficient:
        return (answer.strip(), round(min(confidence, 0.3), 2), "insufficient_context")
    if grounded_ratio < settings.groundedness_threshold:
        return (
            "I could not find enough supported information in the knowledge base.",
            round(min(confidence, 0.3), 2),
            "insufficient_context",
        )
    return (answer.strip(), confidence, "answered")


def fallback_answer(question: str, chunks: list[RetrievedChunk]) -> str:
    lead = chunks[0]
    return (
        f"Based on {lead.document} page {lead.page}, the relevant policy says: "
        f"{snippet(lead.content, 650)}"
    )


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
    """Documented heuristic: blend top-evidence semantic similarity with the share of
    answer claims that map back to retrieved chunks. Not a calibrated probability."""
    normalized = max(0.0, min(1.0, similarity / 0.6))
    return round(max(0.0, min(1.0, 0.5 * normalized + 0.5 * grounded_ratio)), 2)
