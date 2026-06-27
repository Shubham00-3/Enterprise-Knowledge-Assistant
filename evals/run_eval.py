"""Self-contained RAG evaluation + ablation runner.

Runs the pipeline in-process (no HTTP server needed) against the labelled
dataset, across three retrieval configurations, and reports retrieval and
answer-quality metrics plus an ablation table.

Usage (from the repo root, with the backend venv active and OPENAI_API_KEY set):
    python evals/run_eval.py

Outputs:
    evals/reports/ablation.json   full per-config metrics
    evals/reports/latest.json     metrics for the production config (+rerank)
    evals/reports/ablation.md     markdown ablation table (paste-ready)
"""

import json
import os
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# Use a dedicated SQLite DB so evaluation never touches the app's working DB.
os.environ.setdefault(
    "DATABASE_URL", f"sqlite:///{(REPO / 'backend' / '.local' / 'eval.db').as_posix()}"
)

from sqlalchemy import func, select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_local_db  # noqa: E402
from app.models import Chunk  # noqa: E402
from app.rag_core.generation.service import generate_answer  # noqa: E402
from app.rag_core.ingestion.service import ingest_path  # noqa: E402
from app.rag_core.providers import OpenAIEmbeddingProvider, OpenAILLMProvider  # noqa: E402
from app.rag_core.retrieval.service import retrieve  # noqa: E402

DATASET = Path(__file__).with_name("dataset.jsonl")
REPORTS = Path(__file__).with_name("reports")

# Ablation: each step adds one retrieval improvement on top of the previous.
CONFIGS = {
    "dense_only": {"use_hybrid": False, "use_rerank": False},
    "+hybrid_rrf": {"use_hybrid": True, "use_rerank": False},
    "+rerank": {"use_hybrid": True, "use_rerank": True},
}


def load_cases() -> list[dict]:
    return [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]


def ensure_ingested(embeddings: OpenAIEmbeddingProvider) -> None:
    init_local_db()
    with SessionLocal() as session:
        count = session.scalar(select(func.count()).select_from(Chunk)) or 0
        if count == 0:
            print("Ingesting sample corpus into the eval DB...")
            print(ingest_path(session, REPO / "data" / "sample", embeddings))


def mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def evaluate(cases: list[dict], settings, embeddings, llm, cfg: dict) -> dict:
    answer_ok: list[bool] = []
    doc_hit: list[bool] = []
    page_hit: list[bool] = []
    reciprocal_rank: list[float] = []
    abstention_ok: list[bool] = []
    latencies: list[float] = []

    with SessionLocal() as session:
        for case in cases:
            start = time.perf_counter()
            chunks = retrieve(
                session=session,
                question=case["question"],
                embeddings=embeddings,
                llm=llm,
                top_k=settings.retrieval_top_k,
                rerank_top_k=settings.rerank_top_k,
                use_rerank=cfg["use_rerank"],
                use_hybrid=cfg["use_hybrid"],
            )
            generated = generate_answer(case["question"], chunks, llm, settings)
            answer, status = generated.answer, generated.status
            latencies.append((time.perf_counter() - start) * 1000)

            if case["type"] == "unanswerable":
                abstention_ok.append(status == "insufficient_context")
                continue

            gold_docs = {g["document"] for g in case["gold_sources"]}
            gold_doc_pages = {(g["document"], g["page"]) for g in case["gold_sources"]}
            top5 = chunks[:5]
            answer_lower = answer.lower()

            answer_ok.append(all(sub.lower() in answer_lower for sub in case["answer_contains"]))
            doc_hit.append(bool(gold_docs & {c.document for c in top5}))
            page_hit.append(bool(gold_doc_pages & {(c.document, c.page) for c in top5}))

            rank = 0.0
            for position, chunk in enumerate(chunks, start=1):
                if chunk.document in gold_docs:
                    rank = 1.0 / position
                    break
            reciprocal_rank.append(rank)

    return {
        "answer_accuracy": mean(answer_ok),
        "doc_recall@5": mean(doc_hit),
        "page_recall@5": mean(page_hit),
        "mrr": mean(reciprocal_rank),
        "abstention_accuracy": mean(abstention_ok),
        "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
        "n_cases": len(cases),
    }


def render_markdown(ablation: dict[str, dict]) -> str:
    header = "| Config | Answer acc | Doc Recall@5 | Page Recall@5 | MRR | Abstention | Latency (ms) |"
    divider = "|---|---:|---:|---:|---:|---:|---:|"
    rows = [header, divider]
    for name, m in ablation.items():
        rows.append(
            f"| {name} | {m['answer_accuracy']:.2f} | {m['doc_recall@5']:.2f} | "
            f"{m['page_recall@5']:.2f} | {m['mrr']:.2f} | {m['abstention_accuracy']:.2f} | "
            f"{m['avg_latency_ms']} |"
        )
    return "\n".join(rows)


def main() -> None:
    settings = get_settings()
    embeddings = OpenAIEmbeddingProvider(settings)
    llm = OpenAILLMProvider(settings)
    print(f"Models: gen={settings.gen_model} utility={settings.utility_model} embed={settings.embed_model}")
    if not settings.openai_api_key:
        print("WARNING: OPENAI_API_KEY not set - running in deterministic fallback mode.")

    ensure_ingested(embeddings)
    cases = load_cases()

    ablation: dict[str, dict] = {}
    for name, cfg in CONFIGS.items():
        print(f"Running config: {name} ...")
        ablation[name] = evaluate(cases, settings, embeddings, llm, cfg)
        print(f"  {ablation[name]}")

    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "ablation.json").write_text(json.dumps(ablation, indent=2), encoding="utf-8")
    (REPORTS / "latest.json").write_text(
        json.dumps({"config": "+rerank", "metrics": ablation["+rerank"]}, indent=2), encoding="utf-8"
    )
    table = render_markdown(ablation)
    (REPORTS / "ablation.md").write_text(table + "\n", encoding="utf-8")
    print("\n" + table)


if __name__ == "__main__":
    main()
