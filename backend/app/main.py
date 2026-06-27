import hashlib
import logging
import os
import tempfile
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import structlog
from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from app.auth import SEED_OWNER_ID, current_owner_id
from app.config import Settings, get_settings
from app.db import SessionLocal, get_session, init_local_db, readiness_check
from app.models import Chunk, Document, Feedback, Message
from app.rag_core.ingestion.loaders import SUPPORTED_EXTENSIONS
from app.rag_core.ingestion.service import index_document_file, ingest_path
from app.rag_core.pipeline import answer_question
from app.rag_core.providers import OpenAIEmbeddingProvider, OpenAILLMProvider
from app.schemas import (
    AskRequest,
    AskResponse,
    ArtifactChunk,
    DocumentArtifact,
    DocumentStatus,
    FeedbackRequest,
    FeedbackResponse,
    UploadResponse,
)

settings = get_settings()
# Only /ask is rate limited (see decorator below); health checks, /documents and
# /feedback must stay unthrottled so Railway health probes never get a 429.
limiter = Limiter(key_func=get_remote_address)

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)
logger = structlog.get_logger()

app = FastAPI(title=settings.app_name, version="0.1.0")
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
def rate_limit_handler(_: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(status_code=429, content={"detail": f"Rate limit exceeded: {exc.detail}"})


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_origin_regex=settings.cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = request.headers.get("x-request-id", str(uuid4()))
    structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
    start = perf_counter()
    response = await call_next(request)
    logger.info(
        "request_complete",
        method=request.method,
        status_code=response.status_code,
        latency_ms=int((perf_counter() - start) * 1000),
    )
    response.headers["x-request-id"] = request_id
    structlog.contextvars.clear_contextvars()
    return response


@app.on_event("startup")
def startup() -> None:
    # On Postgres, Alembic owns the schema (incl. the halfvec/tsv columns), so we
    # skip create_all there. For the local SQLite path we bootstrap the tables.
    if not settings.is_postgres:
        init_local_db()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz(session: Session = Depends(get_session)) -> dict[str, object]:
    checks = readiness_check()
    chunk_count = session.scalar(select(func.count()).select_from(Chunk)) or 0
    return {
        "status": "ok" if all(checks.values()) else "degraded",
        "checks": checks,
        "chunks": chunk_count,
        "models": {
            "generation": settings.gen_model,
            "utility": settings.utility_model,
            "embedding": settings.embed_model,
            "embedding_dims": settings.embed_dims,
        },
    }


@app.get("/documents", response_model=list[DocumentStatus])
def documents(
    session: Session = Depends(get_session),
    owner_id: str = Depends(current_owner_id),
) -> list[DocumentStatus]:
    rows = session.scalars(
        select(Document).where(Document.owner_id == owner_id).order_by(Document.created_at.desc())
    ).all()
    return [
        DocumentStatus(
            id=row.id,
            document=row.filename,
            doc_type=row.doc_type,
            num_pages=row.num_pages,
            status=row.status,
        )
        for row in rows
    ]


# Cap how many chunks one artifact response returns so a very large document
# can't dump its entire body into a single payload (and the DOM) at once.
ARTIFACT_MAX_CHUNKS = 300


@app.get("/documents/{document_id}/artifact", response_model=DocumentArtifact)
def document_artifact(
    document_id: str,
    session: Session = Depends(get_session),
    owner_id: str = Depends(current_owner_id),
) -> DocumentArtifact:
    document = session.scalar(
        select(Document).where(Document.id == document_id, Document.owner_id == owner_id)
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    total_chunks = session.scalar(
        select(func.count())
        .select_from(Chunk)
        .where(Chunk.document_id == document.id, Chunk.owner_id == owner_id)
    ) or 0
    chunks = session.scalars(
        select(Chunk)
        .where(Chunk.document_id == document.id, Chunk.owner_id == owner_id)
        .order_by(Chunk.chunk_index.asc())
        .limit(ARTIFACT_MAX_CHUNKS)
    ).all()
    return DocumentArtifact(
        id=document.id,
        document=document.filename,
        title=document.title,
        doc_type=document.doc_type,
        num_pages=document.num_pages,
        status=document.status,
        truncated=total_chunks > len(chunks),
        chunks=[
            ArtifactChunk(
                chunk_id=chunk.id,
                chunk_index=chunk.chunk_index,
                page=chunk.page_start,
                section_title=chunk.section_title,
                content=chunk.content,
            )
            for chunk in chunks
        ],
    )


@app.post("/ask", response_model=AskResponse)
@limiter.limit(settings.rate_limit)
def ask(
    request: Request,
    payload: AskRequest,
    session: Session = Depends(get_session),
    app_settings: Settings = Depends(get_settings),
    owner_id: str = Depends(current_owner_id),
) -> AskResponse:
    if len(payload.question) > app_settings.max_question_chars:
        raise HTTPException(status_code=422, detail="Question is too long")
    return answer_question(
        session=session,
        question=payload.question,
        conversation_id=payload.conversation_id,
        settings=app_settings,
        embeddings=OpenAIEmbeddingProvider(app_settings),
        llm=OpenAILLMProvider(app_settings),
        owner_id=owner_id,
    )


@app.post("/feedback", response_model=FeedbackResponse)
def feedback(payload: FeedbackRequest, session: Session = Depends(get_session)) -> FeedbackResponse:
    message = session.get(Message, payload.message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    session.add(Feedback(message_id=message.id, rating=payload.rating, comment=payload.comment))
    session.commit()
    return FeedbackResponse(ok=True)


def process_upload(document_id: str, raw_path: str, settings: Settings) -> None:
    """Background task: embed an uploaded file and flip its document to 'indexed'.

    Runs after the HTTP response so large files don't block the request. Uses its own
    DB session because the request-scoped session is already closed.
    """
    try:
        with SessionLocal() as session:
            document = session.get(Document, document_id)
            if document is None:
                return
            index_document_file(session, document, Path(raw_path), OpenAIEmbeddingProvider(settings))
            session.commit()
            logger.info("upload_indexed", document_id=document_id, owner_id=document.owner_id)
    except Exception as exc:  # noqa: BLE001 - mark the doc failed instead of crashing silently
        logger.error("upload_indexing_failed", document_id=document_id, error=str(exc))
        with SessionLocal() as session:
            document = session.get(Document, document_id)
            if document is not None:
                document.status = "failed"
                session.commit()
    finally:
        try:
            os.remove(raw_path)
        except OSError:
            pass


@app.post("/upload", response_model=UploadResponse)
def upload(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    app_settings: Settings = Depends(get_settings),
    owner_id: str = Depends(current_owner_id),
) -> UploadResponse:
    extension = Path(file.filename or "").suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )
    raw = file.file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(raw) > app_settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {app_settings.max_upload_mb} MB limit")

    checksum = hashlib.sha256(raw).hexdigest()
    existing = (
        session.query(Document)
        .filter(Document.checksum == checksum, Document.owner_id == owner_id)
        .one_or_none()
    )
    if existing:
        return UploadResponse(document_id=existing.id, filename=existing.filename, status=existing.status)

    fd, raw_path = tempfile.mkstemp(suffix=extension)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)

    document = Document(
        owner_id=owner_id,
        filename=file.filename,
        title=Path(file.filename).stem.replace("_", " ").replace("-", " "),
        doc_type=extension.lstrip("."),
        checksum=checksum,
        num_pages=0,
        status="processing",
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    background.add_task(process_upload, document.id, raw_path, app_settings)
    return UploadResponse(document_id=document.id, filename=document.filename, status="processing")


@app.post("/ingest")
def ingest(
    x_admin_api_key: str | None = Header(default=None),
    input_path: str = "data/sample",
    session: Session = Depends(get_session),
    app_settings: Settings = Depends(get_settings),
) -> dict[str, int]:
    if x_admin_api_key != app_settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    path = Path(input_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Input path not found")
    # Admin bulk-load seeds the shared sample corpus under the seed owner. Per-user
    # uploads (Phase 2) will own their documents via the authenticated /upload route.
    return ingest_path(session, path, OpenAIEmbeddingProvider(app_settings), owner_id=SEED_OWNER_ID)


@app.post("/ask/stream")
def ask_stream() -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content={"detail": "Streaming is planned as P1. Use POST /ask for the MVP."},
    )
