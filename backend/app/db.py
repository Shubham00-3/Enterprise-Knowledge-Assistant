from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy import event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
connect_args = {"check_same_thread": False} if settings.sqlalchemy_url.startswith("sqlite") else {}
engine = create_engine(settings.sqlalchemy_url, pool_pre_ping=True, connect_args=connect_args)


if settings.is_postgres:
    @event.listens_for(engine, "connect")
    def set_postgres_search_path(dbapi_connection, _connection_record) -> None:
        # Supabase installs extensions such as pgvector in the `extensions`
        # schema. Keeping both schemas in the path lets SQL use `halfvec`
        # and pgvector operators without hard-coding a provider-specific schema.
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET search_path TO public, extensions")


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def init_local_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def readiness_check() -> dict[str, bool]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        vector_available = True
        if settings.is_postgres:
            result = conn.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
            )
            vector_available = bool(result.scalar())
    return {"database": True, "pgvector": vector_available}
