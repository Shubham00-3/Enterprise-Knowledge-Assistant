from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Owner of the bundled sample corpus and of all data created while auth is disabled.
SEED_OWNER_ID = "public-seed"


def _default_database_url() -> str:
    """Anchor the local SQLite DB to <repo>/backend/.local so the path is the
    same no matter which directory the app or CLI is launched from."""
    repo_root = Path(__file__).resolve().parents[2]
    return f"sqlite:///{(repo_root / 'backend' / '.local' / 'assistant.db').as_posix()}"


class Settings(BaseSettings):
    app_name: str = "Enterprise Knowledge Assistant"
    database_url: str = Field(default_factory=_default_database_url)
    frontend_origin: str = "http://localhost:5173"
    # Extra comma-separated origins for deployed frontends. Vercel creates immutable
    # preview URLs on every deploy, so we also allow this project's Vercel URL pattern.
    frontend_origins: str | None = None
    frontend_origin_regex: str | None = r"https://enterprise-knowledge-assis[a-z0-9-]*\.vercel\.app"
    admin_api_key: str = "change-me"
    openai_api_key: str | None = None
    gen_model: str = "gpt-5.5"
    utility_model: str = "gpt-5.4-mini"
    embed_model: str = "text-embedding-3-large"
    embed_dims: int = 3072
    rate_limit: str = "20/minute"
    max_question_chars: int = 1200
    max_upload_mb: int = 10
    retrieval_top_k: int = 8
    rerank_top_k: int = 6
    retrieval_threshold: float = 0.08
    groundedness_threshold: float = 0.65
    enable_query_rewrite: bool = True
    enable_llm_rerank: bool = True
    enable_hybrid: bool = True
    enable_groundedness_gate: bool = True
    # Multi-query / RAG-Fusion. Off by default: it adds an LLM call + extra embeddings per
    # question, which only pays off once a user's corpus is large. See docs/system-design.md.
    enable_multi_query: bool = False
    multi_query_count: int = 3
    # Per-user auth (Phase 1). When require_auth is True, /ask, /documents, /feedback and
    # /ingest demand a valid Supabase JWT and scope all data to that user. When False, the
    # app behaves as the single-pool MVP did, owned by the seed user.
    require_auth: bool = False
    # Modern Supabase projects sign tokens with asymmetric keys (ES256) served from a
    # JWKS endpoint; legacy projects use a shared HS256 secret. Set either SUPABASE_URL
    # (jwks url is derived) / SUPABASE_JWKS_URL, or SUPABASE_JWT_SECRET for HS256.
    supabase_url: str | None = None
    supabase_jwks_url: str | None = None
    supabase_jwt_secret: str | None = None
    supabase_jwt_audience: str = "authenticated"
    data_dir: Path = Field(default=Path("data/sample"))

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith(("postgresql://", "postgresql+psycopg://"))

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return self.database_url

    @property
    def cors_allow_origins(self) -> list[str]:
        origins = [
            self.frontend_origin,
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
        if self.frontend_origins:
            origins.extend(origin.strip() for origin in self.frontend_origins.split(","))

        seen: set[str] = set()
        normalized: list[str] = []
        for origin in origins:
            origin = origin.strip().rstrip("/")
            if origin and origin not in seen:
                seen.add(origin)
                normalized.append(origin)
        return normalized

    @property
    def cors_allow_origin_regex(self) -> str | None:
        if not self.frontend_origin_regex:
            return None
        value = self.frontend_origin_regex.strip()
        return value or None


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.database_url.startswith("sqlite"):
        db_file = settings.database_url.split("sqlite:///", 1)[-1]
        Path(db_file).parent.mkdir(parents=True, exist_ok=True)
    return settings
