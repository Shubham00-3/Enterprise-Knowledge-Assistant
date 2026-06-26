import re

from app.rag_core.interfaces import LoadedPage, TextChunk


def estimate_tokens(text: str) -> int:
    return max(1, len(re.findall(r"\S+", text)))


def _make_chunk(lines: list[str], index: int, page: int, section_title: str | None) -> TextChunk:
    content = "\n".join(lines).strip()
    return TextChunk(
        content=content,
        chunk_index=index,
        page_start=page,
        page_end=page,
        section_title=section_title,
        token_count=estimate_tokens(content),
    )


def chunk_pages(pages: list[LoadedPage], target_tokens: int = 450, overlap_tokens: int = 70) -> list[TextChunk]:
    """Chunk page-by-page so every chunk belongs to exactly one page. This keeps page
    citations accurate (a chunk never straddles a page boundary), while still splitting
    long pages into token-sized, slightly overlapping windows."""
    chunks: list[TextChunk] = []
    section_title: str | None = None

    for page in pages:
        lines = [line.strip() for line in page.text.splitlines() if line.strip()]
        buffer: list[str] = []
        for line in lines:
            if line.startswith("#"):
                section_title = line.lstrip("#").strip()
            buffer.append(line)
            if estimate_tokens("\n".join(buffer)) >= target_tokens:
                chunks.append(_make_chunk(buffer, len(chunks), page.page, section_title))
                words = " ".join(buffer).split()
                buffer = (
                    [" ".join(words[-overlap_tokens:])]
                    if overlap_tokens and len(words) > overlap_tokens
                    else []
                )
        if "\n".join(buffer).strip():
            chunks.append(_make_chunk(buffer, len(chunks), page.page, section_title))

    return chunks
