"""Deterministic lexical embeddings for offline tests/demos, never experiments."""
import hashlib
import re
import numpy as np


class MockEncoder:
    dimension = 128

    def encode(self, texts):
        if isinstance(texts, str):
            texts = [texts]
        result = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for i, text in enumerate(texts):
            for token in re.findall(r"\w+", text.lower()):
                digest = hashlib.sha256(token.encode()).digest()
                result[i, int.from_bytes(digest[:4], "big") % self.dimension] += 1
        return result
