from typing import Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1200)
    conversation_id: str | None = None


class Source(BaseModel):
    document: str
    page: int
    snippet: str
    score: float


class AskResponse(BaseModel):
    answer: str
    sources: list[Source]
    confidence: float
    status: Literal["answered", "insufficient_context"]
    conversation_id: str
    message_id: str
    latency_ms: int


class DocumentStatus(BaseModel):
    id: str
    document: str
    doc_type: str
    num_pages: int
    status: str


class FeedbackRequest(BaseModel):
    message_id: str
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackResponse(BaseModel):
    ok: bool
