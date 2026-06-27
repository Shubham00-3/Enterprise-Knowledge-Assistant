import json
import time

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Conversation, Message
from app.rag_core.generation.service import AnswerResult, generate_answer
from app.rag_core.interfaces import EmbeddingProvider, LLMProvider, RetrievedChunk
from app.rag_core.retrieval.service import retrieve, rewrite_query
from app.rag_core.utils import snippet
from app.schemas import AskResponse, Source

# When the model returns no usable citations, show at most this many top-ranked
# chunks rather than the full retrieval set (keeps off-topic candidates out of the UI).
SOURCE_FALLBACK_LIMIT = 3


def answer_question(
    session: Session,
    question: str,
    conversation_id: str | None,
    settings: Settings,
    embeddings: EmbeddingProvider,
    llm: LLMProvider,
    owner_id: str,
) -> AskResponse:
    started = time.perf_counter()
    conversation = _get_or_create_conversation(session, conversation_id)
    history = [message.content for message in conversation.messages[-6:]]
    standalone_question = rewrite_query(question, history, llm, settings.enable_query_rewrite)
    chunks = retrieve(
        session=session,
        question=standalone_question,
        embeddings=embeddings,
        llm=llm,
        top_k=settings.retrieval_top_k,
        rerank_top_k=settings.rerank_top_k,
        use_rerank=settings.enable_llm_rerank,
        owner_id=owner_id,
        use_hybrid=settings.enable_hybrid,
        use_multi_query=settings.enable_multi_query,
        multi_query_count=settings.multi_query_count,
    )
    result = generate_answer(question, chunks, llm, settings)
    sources = [
        Source(
            document=chunk.document,
            page=chunk.page,
            snippet=snippet(chunk.content),
            score=round(chunk.score, 4),
        )
        for chunk in _select_source_chunks(chunks, result)
    ]
    latency_ms = int((time.perf_counter() - started) * 1000)

    session.add(Message(conversation_id=conversation.id, role="user", content=question))
    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=result.answer,
        sources_json=json.dumps([source.model_dump() for source in sources]),
        confidence=result.confidence,
        status=result.status,
        latency_ms=latency_ms,
    )
    session.add(assistant_message)
    session.commit()
    session.refresh(assistant_message)

    return AskResponse(
        answer=result.answer,
        sources=sources,
        confidence=result.confidence,
        status=result.status,
        conversation_id=conversation.id,
        message_id=assistant_message.id,
        latency_ms=latency_ms,
    )


def _select_source_chunks(
    chunks: list[RetrievedChunk], result: AnswerResult
) -> list[RetrievedChunk]:
    """Show only the chunks the answer is actually built on.

    - Abstained answers cite nothing, so show no sources.
    - When the model cited specific chunks, show exactly those (in retrieval-rank order).
    - Otherwise fall back to the top few retrieved chunks instead of the full candidate set.
    """
    if result.status == "insufficient_context":
        return []
    if result.cited_chunk_ids:
        cited = set(result.cited_chunk_ids)
        selected = [chunk for chunk in chunks if chunk.chunk_id in cited]
        if selected:
            return selected
    return chunks[:SOURCE_FALLBACK_LIMIT]


def _get_or_create_conversation(session: Session, conversation_id: str | None) -> Conversation:
    if conversation_id:
        existing = session.get(Conversation, conversation_id)
        if existing:
            return existing
    conversation = Conversation()
    session.add(conversation)
    session.flush()
    return conversation
