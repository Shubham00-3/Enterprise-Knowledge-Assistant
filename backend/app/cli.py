import argparse
from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal, init_local_db
from app.rag_core.ingestion.service import ingest_path
from app.rag_core.providers import OpenAIEmbeddingProvider


def main() -> None:
    parser = argparse.ArgumentParser(description="Enterprise Knowledge Assistant CLI")
    subcommands = parser.add_subparsers(dest="command", required=True)
    ingest = subcommands.add_parser("ingest", help="Index documents")
    ingest.add_argument("input", nargs="?", default="data/sample")
    args = parser.parse_args()

    settings = get_settings()
    init_local_db()
    if args.command == "ingest":
        with SessionLocal() as session:
            result = ingest_path(session, Path(args.input), OpenAIEmbeddingProvider(settings))
        print(result)


if __name__ == "__main__":
    main()
