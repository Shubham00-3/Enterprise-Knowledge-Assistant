from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_database_url() -> str:
    """Anchor the local SQLite DB to <repo>/backend/.local so the path is the
    same no matter which directory the app or CLI is launched from."""
    repo_root = Path(__file__).resolve().parents[2]
    return f"sqlite:///{(repo_root / 'backend' / '.local' / 'assistant.db').as_posix()}"


class Settings(BaseSettings):
    app_name: str = "Enterprise Knowledge Assistant"
    database_url: str = Field(default_factory=_default_database_url)
    frontend_origin: str = "http://localhost:5173"
    admin_api_key: str = "change-me"
    openai_api_key: str | None = None
    gen_model: str = "gpt-5.5"
    utility_model: str = "gpt-5.4-mini"
    embed_model: str = "text-embedding-3-large"
    embed_dims: int = 3072
    rate_limit: str = "20/minute"
    max_question_chars: int = 1200
    retrieval_top_k: int = 8
    rerank_top_k: int = 6
    retrieval_threshold: float = 0.08
    groundedness_threshold: float = 0.65
    enable_query_rewrite: bool = True
    enable_llm_rerank: bool = True
    enable_hybrid: bool = True
    enable_groundedness_gate: bool = True
    require_auth: bool = False
    api_auth_token: str | None = None
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


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.database_url.startswith("sqlite"):
        db_file = settings.database_url.split("sqlite:///", 1)[-1]
        Path(db_file).parent.mkdir(parents=True, exist_ok=True)
    return settings
