from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LoadedPage:
    text: str
    page: int
    title: str


@dataclass(frozen=True)
class TextChunk:
    content: str
    chunk_index: int
    page_start: int
    page_end: int
    section_title: str | None
    token_count: int


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document: str
    page: int
    content: str
    score: float
    section_title: str | None = None
    similarity: float = 0.0  # raw dense cosine similarity, used for confidence


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class LLMProvider(Protocol):
    def complete_json(self, system: str, user: str, schema_name: str, use_utility: bool = False) -> dict:
        ...

    def complete_text(self, system: str, user: str) -> str:
        ...
