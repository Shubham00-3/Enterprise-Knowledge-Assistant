from typing import Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1200)
    conversation_id: str | None = None


class Source(BaseModel):
    document_id: str
    chunk_id: str
    document: str
    page: int
    section_title: str | None = None
    snippet: str
    score: float


class AnswerMetrics(BaseModel):
    confidence: float
    groundedness: float | None = None
    citation_count: int
    status: Literal["answered", "insufficient_context"]
    latency_ms: int


class AskResponse(BaseModel):
    answer: str
    sources: list[Source]
    confidence: float
    status: Literal["answered", "insufficient_context"]
    conversation_id: str
    message_id: str
    latency_ms: int
    metrics: AnswerMetrics


class DocumentStatus(BaseModel):
    id: str
    document: str
    doc_type: str
    num_pages: int
    status: str


class ArtifactChunk(BaseModel):
    chunk_id: str
    chunk_index: int
    page: int
    section_title: str | None = None
    content: str


class DocumentArtifact(BaseModel):
    id: str
    document: str
    title: str
    doc_type: str
    num_pages: int
    status: str
    truncated: bool = False
    chunks: list[ArtifactChunk]


class UploadResponse(BaseModel):
    document_id: str
    filename: str
    status: str


class FeedbackRequest(BaseModel):
    message_id: str
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackResponse(BaseModel):
    ok: bool
