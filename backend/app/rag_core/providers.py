import json

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import Settings
from app.rag_core.interfaces import EmbeddingProvider, LLMProvider
from app.rag_core.utils import stable_embedding


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None
        self.semantic_reliable = self.client is not None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.client:
            return [stable_embedding(text, self.settings.embed_dims) for text in texts]
        response = self.client.embeddings.create(
            model=self.settings.embed_model,
            input=texts,
            dimensions=self.settings.embed_dims,
        )
        return [item.embedding for item in response.data]


class OpenAILLMProvider(LLMProvider):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=4))
    def complete_text(self, system: str, user: str) -> str:
        if not self.client:
            return ""
        response = self.client.responses.create(
            model=self.settings.utility_model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.output_text.strip()

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=4))
    def complete_json(self, system: str, user: str, schema_name: str, use_utility: bool = False) -> dict:
        if not self.client:
            return {}
        model = self.settings.utility_model if use_utility else self.settings.gen_model
        response = self.client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            text={"format": {"type": "json_object"}},
        )
        try:
            data = json.loads(response.output_text)
        except json.JSONDecodeError:
            return {"answer": response.output_text, "claims": []}
        return data if isinstance(data, dict) else {"answer": str(data), "claims": []}
