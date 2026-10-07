"""Jev-Mem embeddings using the existing OpenAI/Azure v1 configuration."""

import os
import numpy as np
from openai import OpenAI


class OpenAIVectorEncoder:
    def __init__(self, model_name="text-embedding-3-small", client=None, dimension=None):
        self.model_name = os.getenv("OPENAI_EMBEDDING_MODEL", model_name)
        self.dimension = int(dimension or os.getenv("OPENAI_EMBEDDING_DIMENSIONS", "1536"))
        if self.dimension <= 0:
            raise ValueError("Embedding dimension must be positive")
        self.client = client if client is not None else OpenAI(base_url=os.getenv("OPENAI_BASE_URL") or None)

    def encode(self, texts, timeout_seconds=None):
        return self.encode_batch([texts] if isinstance(texts, str) else texts, timeout_seconds=timeout_seconds)

    def encode_batch(self, texts, batch_size=100, timeout_seconds=None):
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)
        vectors = []
        client = self.client
        if timeout_seconds is not None:
            client = client.with_options(timeout=max(0.001, timeout_seconds / 4), max_retries=0)
        for offset in range(0, len(texts), batch_size):
            batch = texts[offset:offset + batch_size]
            response = client.embeddings.create(model=self.model_name, input=batch, dimensions=self.dimension)
            data = sorted(response.data, key=lambda item: item.index)
            if [item.index for item in data] != list(range(len(batch))):
                raise ValueError("Embedding response does not match input batch")
            vectors.extend(item.embedding for item in data)
        result = np.asarray(vectors, dtype=np.float32)
        if result.shape != (len(texts), self.dimension) or not np.isfinite(result).all():
            raise ValueError("Unexpected embedding dimensions or non-finite values")
        return result
