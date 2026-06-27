from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import SEED_OWNER_ID, get_settings
from app.models import Chunk, Document
from app.rag_core.ingestion.chunker import chunk_pages
from app.rag_core.ingestion.loaders import SUPPORTED_EXTENSIONS, load_document
from app.rag_core.interfaces import EmbeddingProvider
from app.rag_core.utils import dumps_embedding, file_checksum


def ingest_path(
    session: Session,
    input_path: Path,
    embeddings: EmbeddingProvider,
    owner_id: str = SEED_OWNER_ID,
) -> dict[str, int]:
    """Bulk-ingest every supported file under a directory (CLI / admin seed path)."""
    paths = sorted(
        p for p in input_path.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    indexed = 0
    skipped = 0
    chunks_written = 0
    for path in paths:
        written = ingest_file(session, path, embeddings, owner_id)
        if written is None:
            skipped += 1
        else:
            indexed += 1
            chunks_written += written
    session.commit()
    return {"documents_indexed": indexed, "documents_skipped": skipped, "chunks_written": chunks_written}


def ingest_file(
    session: Session,
    path: Path,
    embeddings: EmbeddingProvider,
    owner_id: str = SEED_OWNER_ID,
    original_name: str | None = None,
) -> int | None:
    """Index one file for an owner. Returns chunks written, or None if it was a duplicate.

    Does not commit — the caller owns the transaction boundary.
    """
    name = original_name or path.name
    checksum = file_checksum(path)
    existing = (
        session.query(Document)
        .filter(Document.checksum == checksum, Document.owner_id == owner_id)
        .one_or_none()
    )
    if existing:
        return None

    document = Document(
        owner_id=owner_id,
        filename=name,
        title=Path(name).stem.replace("_", " ").replace("-", " "),
        doc_type=Path(name).suffix.lstrip(".").lower(),
        checksum=checksum,
        num_pages=1,
        status="processing",
    )
    session.add(document)
    session.flush()
    return index_document_file(session, document, path, embeddings)


def index_document_file(
    session: Session, document: Document, path: Path, embeddings: EmbeddingProvider
) -> int:
    """Fill an already-created document with chunks + embeddings and mark it indexed.

    Used by both the bulk path and the async upload task (which pre-creates the
    document row as 'processing' so it shows up in the UI immediately).
    """
    settings = get_settings()
    pages = load_document(path)
    chunks = chunk_pages(pages)
    vectors = embeddings.embed([chunk.content for chunk in chunks]) if chunks else []
    written = 0
    for chunk, vector in zip(chunks, vectors, strict=True):
        db_chunk = Chunk(
            document_id=document.id,
            owner_id=document.owner_id,
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
        written += 1

    document.num_pages = max((page.page for page in pages), default=1)
    document.status = "indexed"
    return written
