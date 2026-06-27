import hashlib
import json
import math
import re
from pathlib import Path
from typing import Iterable


def file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_embedding(text: str, dims: int) -> list[float]:
    """Deterministic fallback embedding for local tests when no OpenAI key exists."""
    vec = [0.0] * dims
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dims
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    limit = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(limit))
    na = math.sqrt(sum(v * v for v in a[:limit])) or 1.0
    nb = math.sqrt(sum(v * v for v in b[:limit])) or 1.0
    return dot / (na * nb)


def reciprocal_rank_fusion(rankings: Iterable[list[tuple[str, float]]], k: int = 60) -> dict[str, float]:
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, (chunk_id, _) in enumerate(ranking, start=1):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return fused


def dumps_embedding(vector: list[float]) -> str:
    return json.dumps([round(v, 8) for v in vector], separators=(",", ":"))


def loads_embedding(value: str | None) -> list[float]:
    if not value:
        return []
    return [float(v) for v in json.loads(value)]


def snippet(text: str, max_chars: int = 420) -> str:
    # Drop leading markdown heading markers (#, ##, ...) so source previews read as
    # prose instead of "# HR Policy Handbook ## Paid Leave ...".
    without_headings = re.sub(r"(?m)^[ \t]*#{1,6}[ \t]+", "", text)
    compact = re.sub(r"\s+", " ", without_headings).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."
