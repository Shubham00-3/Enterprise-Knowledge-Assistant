import json
import time

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Conversation, Message
from app.rag_core.generation.service import generate_answer
from app.rag_core.interfaces import EmbeddingProvider, LLMProvider
from app.rag_core.retrieval.service import retrieve, rewrite_query
from app.rag_core.utils import snippet
from app.schemas import AskResponse, Source


def answer_question(
    session: Session,
    question: str,
    conversation_id: str | None,
    settings: Settings,
    embeddings: EmbeddingProvider,
    llm: LLMProvider,
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
        use_hybrid=settings.enable_hybrid,
    )
    answer, confidence, status = generate_answer(question, chunks, llm, settings)
    sources = [
        Source(
            document=chunk.document,
            page=chunk.page,
            snippet=snippet(chunk.content),
            score=round(chunk.score, 4),
        )
        for chunk in chunks[: settings.rerank_top_k]
    ]
    latency_ms = int((time.perf_counter() - started) * 1000)

    session.add(Message(conversation_id=conversation.id, role="user", content=question))
    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=answer,
        sources_json=json.dumps([source.model_dump() for source in sources]),
        confidence=confidence,
        status=status,
        latency_ms=latency_ms,
    )
    session.add(assistant_message)
    session.commit()
    session.refresh(assistant_message)

    return AskResponse(
        answer=answer,
        sources=sources,
        confidence=confidence,
        status=status,
        conversation_id=conversation.id,
        message_id=assistant_message.id,
        latency_ms=latency_ms,
    )


def _get_or_create_conversation(session: Session, conversation_id: str | None) -> Conversation:
    if conversation_id:
        existing = session.get(Conversation, conversation_id)
        if existing:
            return existing
    conversation = Conversation()
    session.add(conversation)
    session.flush()
    return conversation
