from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Chunk, Document
from app.rag_core.ingestion.chunker import chunk_pages
from app.rag_core.ingestion.loaders import SUPPORTED_EXTENSIONS, load_document
from app.rag_core.interfaces import EmbeddingProvider
from app.rag_core.utils import dumps_embedding, file_checksum


def ingest_path(session: Session, input_path: Path, embeddings: EmbeddingProvider) -> dict[str, int]:
    settings = get_settings()
    paths = sorted(
        p for p in input_path.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    indexed = 0
    skipped = 0
    chunks_written = 0
    for path in paths:
        checksum = file_checksum(path)
        existing = session.query(Document).filter(Document.checksum == checksum).one_or_none()
        if existing:
            skipped += 1
            continue

        pages = load_document(path)
        chunks = chunk_pages(pages)
        document = Document(
            filename=path.name,
            title=path.stem.replace("_", " ").replace("-", " "),
            doc_type=path.suffix.lstrip(".").lower(),
            checksum=checksum,
            num_pages=max((page.page for page in pages), default=1),
            status="indexed",
        )
        session.add(document)
        session.flush()

        vectors = embeddings.embed([chunk.content for chunk in chunks])
        for chunk, vector in zip(chunks, vectors, strict=True):
            db_chunk = Chunk(
                document_id=document.id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section_title=chunk.section_title,
                token_count=chunk.token_count,
                embedding_json=dumps_embedding(vector),
                search_text=f"{document.title} {chunk.section_title or ''} {chunk.content}",
            )
            session.add(db_chunk)
            session.flush()
            if settings.is_postgres:
                vector_literal = "[" + ",".join(str(float(value)) for value in vector) + "]"
                session.execute(
                    text("UPDATE chunks SET embedding = CAST(:embedding AS halfvec) WHERE id = :id"),
                    {"embedding": vector_literal, "id": db_chunk.id},
                )
            chunks_written += 1
        indexed += 1
    session.commit()
    return {"documents_indexed": indexed, "documents_skipped": skipped, "chunks_written": chunks_written}
