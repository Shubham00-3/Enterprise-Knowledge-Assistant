# Enterprise Knowledge Assistant

Production-shaped RAG assistant for enterprise documents. It uses FastAPI, React/Vite, OpenAI, and Railway Postgres + pgvector. Docker is intentionally not required.

## Architecture

- Frontend: React/Vite on Vercel.
- Backend: FastAPI on Railway.
- Store: Railway Postgres + pgvector for documents, chunks, embeddings, chat history, feedback, and eval runs.
- Retrieval: semantic search plus keyword search, reciprocal rank fusion, optional LLM rerank, grounded generation, citations, and abstention.

## Local Setup

```powershell
Copy-Item .env.example .env
cd backend
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -e .[dev]
python -m app.cli ingest ..\\data\\sample
uvicorn app.main:app --reload --port 8000
```

In another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

Without `OPENAI_API_KEY`, the backend uses deterministic local embeddings and fallback answer generation so the app remains testable. Add an OpenAI key for the full model-backed RAG flow.

## API

`POST /ask`

```json
{
  "question": "What is the employee leave policy?",
  "conversation_id": null
}
```

Response:

```json
{
  "answer": "Employees are eligible for 24 paid leaves annually.",
  "sources": [{ "document": "HR_Policy_Handbook.md", "page": 1, "snippet": "...", "score": 0.42 }],
  "confidence": 0.91,
  "status": "answered",
  "conversation_id": "...",
  "message_id": "...",
  "latency_ms": 812
}
```

Other endpoints:

- `GET /documents`
- `POST /feedback`
- `GET /healthz`
- `GET /readyz`
- `POST /ingest` with `x-admin-api-key`

## Authentication

Two complementary mechanisms, both off-friendly for the demo:

- **Admin key** (always on): `POST /ingest` requires the `x-admin-api-key` header to match `ADMIN_API_KEY`.
- **Optional bearer auth on `/ask` and `/feedback`**: disabled by default. Set `REQUIRE_AUTH=true` and `API_AUTH_TOKEN=<token>` to require `Authorization: Bearer <token>` on the query endpoints. The frontend sends it automatically when `VITE_API_AUTH_TOKEN` is set at build time.

## Deployment

### Railway

1. Create a Railway project.
2. Add Railway Postgres with pgvector support.
3. Set backend env vars:
   - `DATABASE_URL`
   - `OPENAI_API_KEY`
   - `FRONTEND_ORIGIN`
   - `ADMIN_API_KEY`
   - `GEN_MODEL` (e.g. `gpt-5.5` for best quality, or `gpt-4.1-nano` for lowest cost)
   - `UTILITY_MODEL` (e.g. `gpt-5.4-mini`, or `gpt-4.1-nano`)
   - `EMBED_MODEL=text-embedding-3-large`
   - `EMBED_DIMS=3072`
   - Optional: `REQUIRE_AUTH=true` and `API_AUTH_TOKEN=<token>` to lock down `/ask`
4. Deploy from GitHub using `railway.json`.
5. Run ingestion once against the deployed service or Railway shell.

### Vercel

1. Create a Vercel project with root directory `frontend`.
2. Set `VITE_API_BASE_URL` to the Railway backend URL.
3. Deploy with the default Vite build.

## Evaluation

A self-contained ablation runner exercises the pipeline in-process (no server needed) over a
27-question labelled set (`evals/dataset.jsonl`) spanning direct, keyword, ambiguous,
multi-document, and unanswerable questions. From the repo root with the venv active and
`OPENAI_API_KEY` set:

```powershell
python evals\run_eval.py
```

It ingests into a dedicated eval DB and writes `evals/reports/ablation.json`,
`evals/reports/latest.json`, and `evals/reports/ablation.md`.

**Metrics:** answer accuracy (all expected facts present), document & page Recall@5, MRR,
abstention accuracy (correct "insufficient context" on unanswerable questions), and latency.

**Ablation** (gpt-4.1-nano, text-embedding-3-large, 27 questions):

| Config | Answer acc | Doc Recall@5 | Page Recall@5 | MRR | Abstention | Latency (ms) |
|---|---:|---:|---:|---:|---:|---:|
| dense_only | 0.81 | 1.00 | 1.00 | 0.98 | 1.00 | 2425 |
| +hybrid_rrf | 0.92 | 1.00 | 1.00 | 0.98 | 1.00 | 2605 |
| +rerank | 0.89 | 1.00 | 1.00 | 1.00 | 1.00 | 3096 |

**What this shows (improvements attempted):** hybrid retrieval (dense + keyword fused with RRF) is
the biggest lever, lifting answer accuracy from 0.81 to 0.92. LLM listwise re-ranking pushes MRR to
a perfect 1.00 (ideal ordering) at a small latency cost; on this small corpus its effect on final
answer accuracy is within one-question noise, and it matters more as the candidate pool grows.
Page-accurate citations (Page Recall@5 = 1.00) come from chunking page-by-page so a chunk never
straddles a page boundary. Abstention is perfect on unanswerable and out-of-scope questions.

## Design Decisions

- pgvector keeps deployment simple for the assignment while still supporting production-style semantic retrieval.
- `halfvec(3072)` plus HNSW is used for Postgres deployments to fit `text-embedding-3-large` (the `vector` type caps index dimensions at 2000; `halfvec` allows up to 4000).
- Retrieval is index-backed on Postgres: dense candidates come from the HNSW `halfvec` index (`<=>` cosine) and keyword candidates from a `tsvector` GIN index (`websearch_to_tsquery` + `ts_rank`). The two rankings are fused with reciprocal rank fusion and reranked by the utility model. The local SQLite path computes the same fusion in-process so the app runs with zero external services for development.
- Model routing splits cost: the flagship `GEN_MODEL` writes the grounded answer, while the cheaper `UTILITY_MODEL` handles query rewriting and listwise reranking.
- Chunking is page-by-page so a chunk never straddles a page boundary, which keeps source citations page-accurate (validated at Page Recall@5 = 1.00).
- Confidence is a documented heuristic blending top-evidence semantic similarity with the share of answer claims that map back to retrieved chunks — not a calibrated probability.
- Multi-document reasoning is supported by packing top-ranked chunks from multiple documents into one grounded prompt; the eval set includes cross-document questions to verify it.
- The backend keeps provider boundaries small: LLM, embeddings, and retrieval are isolated without building a plugin framework.
- Single-token bearer auth and an admin-key-protected ingest endpoint are implemented; multi-tenant RBAC, OCR, streaming answers, and async ingestion queues are future improvements.

## Demo Script

1. Show indexed documents in the left panel.
2. Ask: "What is the employee paid leave policy?"
3. Expand the HR source citation.
4. Ask: "What should API clients do for 429 responses?"
5. Ask an unsupported question like "What is the lunch menu tomorrow?" and show abstention.
6. Submit feedback.
7. Briefly explain ingestion, hybrid retrieval, reranking, grounded generation, and deployment.
