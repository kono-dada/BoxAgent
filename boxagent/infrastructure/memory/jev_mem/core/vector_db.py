"""
Vector Database Interface and Implementation for Jev-Mem

This module provides vector storage and similarity search capabilities,
with support for both in-memory and persistent storage using FAISS.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Tuple, Union
from dataclasses import dataclass, field
from enum import Enum
import numpy as np
import json
import pickle
import os
from pathlib import Path
import logging
from datetime import datetime
from .local_device import local_model_device

logger = logging.getLogger(__name__)

try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    logging.warning("FAISS not available. Using NumPy-based vector search.")

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMER_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMER_AVAILABLE = False
    logging.warning("SentenceTransformer not available. Vector encoding disabled.")

class IndexType(Enum):
    """Types of vector indexes"""
    HOT = "HOT"      # Frequently accessed, kept in memory
    WARM = "WARM"    # Occasionally accessed, can be swapped
    COLD = "COLD"    # Rarely accessed, stored on disk

@dataclass
class VectorEntry:
    """Represents a vector entry in the database"""
    vector_id: str
    vector: np.ndarray
    metadata: Dict[str, Any] = field(default_factory=dict)
    index_type: IndexType = IndexType.HOT
    timestamp: datetime = field(default_factory=datetime.now)
    access_count: int = 0
    last_accessed: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """Convert entry to dictionary"""
        return {
            "vector_id": self.vector_id,
            "vector": self.vector.tolist(),
            "metadata": self.metadata,
            "index_type": self.index_type.value,
            "timestamp": self.timestamp.isoformat(),
            "access_count": self.access_count,
            "last_accessed": self.last_accessed.isoformat()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'VectorEntry':
        """Create entry from dictionary"""
        entry = cls(
            vector_id=data["vector_id"],
            vector=np.array(data["vector"], dtype=np.float32),
            metadata=data.get("metadata", {}),
            access_count=data.get("access_count", 0)
        )
        if "index_type" in data:
            entry.index_type = IndexType(data["index_type"])
        if "timestamp" in data:
            entry.timestamp = datetime.fromisoformat(data["timestamp"])
        if "last_accessed" in data:
            entry.last_accessed = datetime.fromisoformat(data["last_accessed"])
        return entry

class VectorDBInterface(ABC):
    """Abstract interface for vector database operations"""

    @abstractmethod
    def add_vector(self, vector_id: str, vector: np.ndarray,
                  metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Add a vector to the database"""
        pass

    @abstractmethod
    def add_vectors(self, vectors: List[Tuple[str, np.ndarray, Dict[str, Any]]]) -> int:
        """Batch add vectors to the database"""
        pass

    @abstractmethod
    def search(self, query_vector: np.ndarray, k: int = 10,
              filter_metadata: Optional[Dict[str, Any]] = None) -> List[Tuple[str, float, Dict[str, Any]]]:
        """Search for similar vectors"""
        pass

    @abstractmethod
    def get_vector(self, vector_id: str) -> Optional[VectorEntry]:
        """Retrieve a vector by ID"""
        pass

    @abstractmethod
    def update_vector(self, vector_id: str, vector: np.ndarray,
                     metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Update an existing vector"""
        pass

    @abstractmethod
    def delete_vector(self, vector_id: str) -> bool:
        """Delete a vector from the database"""
        pass

    @abstractmethod
    def exists(self, vector_id: str) -> bool:
        """Check if a vector exists"""
        pass

    @abstractmethod
    def size(self) -> int:
        """Get the number of vectors in the database"""
        pass

    @abstractmethod
    def clear(self) -> bool:
        """Clear all vectors from the database"""
        pass

class FAISSVectorDB(VectorDBInterface):
    """FAISS-based vector database implementation"""

    def __init__(self, dimension: int = 384, index_type: str = "flat",
                persist_path: Optional[str] = None):
        """
        Initialize FAISS vector database

        Args:
            dimension: Vector dimension
            index_type: Type of FAISS index ("flat", "ivf", "hnsw")
            persist_path: Path to persist the index
        """
        self.dimension = dimension
        self.persist_path = persist_path
        self.entries: Dict[str, VectorEntry] = {}
        self.id_to_index: Dict[str, int] = {}
        self.index_to_id: Dict[int, str] = {}
        self.next_index = 0

        if not FAISS_AVAILABLE:
            raise ImportError("FAISS is not installed. Install it with: pip install faiss-cpu")

        # Create FAISS index based on type
        if index_type == "flat":
            self.index = faiss.IndexFlatL2(dimension)
        elif index_type == "ivf":
            quantizer = faiss.IndexFlatL2(dimension)
            self.index = faiss.IndexIVFFlat(quantizer, dimension, 100)
            self.index.nprobe = 10
        elif index_type == "hnsw":
            self.index = faiss.IndexHNSWFlat(dimension, 32)
        else:
            self.index = faiss.IndexFlatL2(dimension)

        # Load persisted index if exists
        if persist_path and os.path.exists(persist_path):
            self.load()

    def add_vector(self, vector_id: str, vector: np.ndarray,
                  metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Add a vector to the database"""
        if vector_id in self.entries:
            return False

        if vector.shape[0] != self.dimension:
            raise ValueError(f"Vector dimension {vector.shape[0]} doesn't match index dimension {self.dimension}")

        entry = VectorEntry(
            vector_id=vector_id,
            vector=vector.astype(np.float32),
            metadata=metadata or {}
        )

        self.index.add(vector.reshape(1, -1).astype(np.float32))

        self.entries[vector_id] = entry
        self.id_to_index[vector_id] = self.next_index
        self.index_to_id[self.next_index] = vector_id
        self.next_index += 1

        return True

    def add_vectors(self, vectors: List[Tuple[str, np.ndarray, Dict[str, Any]]]) -> int:
        """Batch add vectors to the database"""
        added_count = 0
        vectors_to_add = []
        entries_to_add = []

        for vector_id, vector, metadata in vectors:
            if vector_id in self.entries:
                continue

            if vector.shape[0] != self.dimension:
                logging.warning(f"Skipping vector {vector_id}: dimension mismatch")
                continue

            entry = VectorEntry(
                vector_id=vector_id,
                vector=vector.astype(np.float32),
                metadata=metadata or {}
            )

            vectors_to_add.append(vector.astype(np.float32))
            entries_to_add.append(entry)

        if vectors_to_add:
            # Batch add to FAISS
            vectors_array = np.vstack(vectors_to_add)
            self.index.add(vectors_array)

            # Update mappings
            for entry in entries_to_add:
                self.entries[entry.vector_id] = entry
                self.id_to_index[entry.vector_id] = self.next_index
                self.index_to_id[self.next_index] = entry.vector_id
                self.next_index += 1
                added_count += 1

        return added_count

    def search(self, query_vector: np.ndarray, k: int = 10,
              filter_metadata: Optional[Dict[str, Any]] = None) -> List[Tuple[str, float, Dict[str, Any]]]:
        """Search for similar vectors"""
        if self.index.ntotal == 0:
            return []

        query_vector = query_vector.reshape(1, -1).astype(np.float32)

        k = min(k, self.index.ntotal)
        distances, indices = self.index.search(query_vector, k)

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx == -1:
                continue

            vector_id = self.index_to_id.get(idx)
            if vector_id and vector_id in self.entries:
                entry = self.entries[vector_id]

                if filter_metadata:
                    match = all(
                        entry.metadata.get(key) == value
                        for key, value in filter_metadata.items()
                    )
                    if not match:
                        continue

                entry.access_count += 1
                entry.last_accessed = datetime.now()

                similarity = 1.0 / (1.0 + float(dist))
                results.append((vector_id, similarity, entry.metadata))

        return results

    def get_vector(self, vector_id: str) -> Optional[VectorEntry]:
        """Retrieve a vector by ID"""
        entry = self.entries.get(vector_id)
        if entry:
            entry.access_count += 1
            entry.last_accessed = datetime.now()
        return entry

    def update_vector(self, vector_id: str, vector: np.ndarray,
                     metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Update an existing vector"""
        if vector_id not in self.entries:
            return False

        logging.warning("Vector update requires index rebuild in current implementation")

        entry = self.entries[vector_id]
        entry.vector = vector.astype(np.float32)
        if metadata is not None:
            entry.metadata = metadata

        self._rebuild_index()
        return True

    def delete_vector(self, vector_id: str) -> bool:
        """Delete a vector from the database"""
        if vector_id not in self.entries:
            return False

        del self.entries[vector_id]

        # Rebuild index after deletion
        self._rebuild_index()
        return True

    def exists(self, vector_id: str) -> bool:
        """Check if a vector exists"""
        return vector_id in self.entries

    def size(self) -> int:
        """Get the number of vectors in the database"""
        return len(self.entries)

    def clear(self) -> bool:
        """Clear all vectors from the database"""
        self.entries.clear()
        self.id_to_index.clear()
        self.index_to_id.clear()
        self.next_index = 0
        self.index.reset()
        return True

    def _rebuild_index(self):
        """Rebuild the FAISS index from entries"""
        self.index.reset()
        self.id_to_index.clear()
        self.index_to_id.clear()
        self.next_index = 0

        if self.entries:
            vectors = []
            for vector_id, entry in self.entries.items():
                vectors.append(entry.vector)
                self.id_to_index[vector_id] = self.next_index
                self.index_to_id[self.next_index] = vector_id
                self.next_index += 1

            vectors_array = np.vstack(vectors).astype(np.float32)
            self.index.add(vectors_array)

    def save(self, path: Optional[str] = None):
        """Save the vector database to disk"""
        save_path = path or self.persist_path
        if not save_path:
            raise ValueError("No save path provided")

        save_dir = Path(save_path)
        save_dir.mkdir(parents=True, exist_ok=True)

        faiss.write_index(self.index, str(save_dir / "index.faiss"))

        metadata = {
            "entries": {vid: e.to_dict() for vid, e in self.entries.items()},
            "id_to_index": self.id_to_index,
            "index_to_id": {str(k): v for k, v in self.index_to_id.items()},
            "next_index": self.next_index,
            "dimension": self.dimension
        }

        with open(save_dir / "metadata.json", 'w') as f:
            json.dump(metadata, f, indent=2, default=str)

    def load(self, path: Optional[str] = None):
        """Load the vector database from disk"""
        load_path = path or self.persist_path
        if not load_path:
            raise ValueError("No load path provided")

        load_dir = Path(load_path)

        # Load FAISS index
        index_path = load_dir / "index.faiss"
        if index_path.exists():
            self.index = faiss.read_index(str(index_path))

        # Load metadata
        metadata_path = load_dir / "metadata.json"
        if metadata_path.exists():
            with open(metadata_path, 'r') as f:
                metadata = json.load(f)

            self.entries = {
                vid: VectorEntry.from_dict(e)
                for vid, e in metadata["entries"].items()
            }
            self.id_to_index = metadata["id_to_index"]
            self.index_to_id = {int(k): v for k, v in metadata["index_to_id"].items()}
            self.next_index = metadata["next_index"]
            self.dimension = metadata["dimension"]

class NumpyVectorDB(VectorDBInterface):
    """Simple NumPy-based vector database for when FAISS is not available"""

    def __init__(self, dimension: int = 384, persist_path: Optional[str] = None):
        """Initialize NumPy vector database"""
        self.dimension = dimension
        self.persist_path = persist_path
        self.entries: Dict[str, VectorEntry] = {}

        # Load persisted data if exists
        if persist_path and os.path.exists(persist_path):
            self.load()

    def add_vector(self, vector_id: str, vector: np.ndarray,
                  metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Add a vector to the database"""
        if vector_id in self.entries:
            return False

        if vector.shape[0] != self.dimension:
            raise ValueError(f"Vector dimension {vector.shape[0]} doesn't match expected {self.dimension}")

        entry = VectorEntry(
            vector_id=vector_id,
            vector=vector.astype(np.float32),
            metadata=metadata or {}
        )
        self.entries[vector_id] = entry
        return True

    def add_vectors(self, vectors: List[Tuple[str, np.ndarray, Dict[str, Any]]]) -> int:
        """Batch add vectors to the database"""
        added_count = 0
        for vector_id, vector, metadata in vectors:
            if self.add_vector(vector_id, vector, metadata):
                added_count += 1
        return added_count

    def search(self, query_vector: np.ndarray, k: int = 10,
              filter_metadata: Optional[Dict[str, Any]] = None) -> List[Tuple[str, float, Dict[str, Any]]]:
        """Search for similar vectors using cosine similarity"""
        if not self.entries:
            return []

        query_vector = query_vector.astype(np.float32)
        query_norm = np.linalg.norm(query_vector)
        if query_norm == 0:
            return []

        similarities = []
        for vector_id, entry in self.entries.items():
            if filter_metadata:
                match = all(
                    entry.metadata.get(key) == value
                    for key, value in filter_metadata.items()
                )
                if not match:
                    continue

            vector_norm = np.linalg.norm(entry.vector)
            if vector_norm == 0:
                similarity = 0.0
            else:
                similarity = np.dot(query_vector, entry.vector) / (query_norm * vector_norm)

            entry.access_count += 1
            entry.last_accessed = datetime.now()

            similarities.append((vector_id, float(similarity), entry.metadata))

        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:k]

    def get_vector(self, vector_id: str) -> Optional[VectorEntry]:
        """Retrieve a vector by ID"""
        entry = self.entries.get(vector_id)
        if entry:
            entry.access_count += 1
            entry.last_accessed = datetime.now()
        return entry

    def update_vector(self, vector_id: str, vector: np.ndarray,
                     metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Update an existing vector"""
        if vector_id not in self.entries:
            return False

        entry = self.entries[vector_id]
        entry.vector = vector.astype(np.float32)
        if metadata is not None:
            entry.metadata = metadata
        return True

    def delete_vector(self, vector_id: str) -> bool:
        """Delete a vector from the database"""
        if vector_id not in self.entries:
            return False
        del self.entries[vector_id]
        return True

    def exists(self, vector_id: str) -> bool:
        """Check if a vector exists"""
        return vector_id in self.entries

    def size(self) -> int:
        """Get the number of vectors in the database"""
        return len(self.entries)

    def clear(self) -> bool:
        """Clear all vectors from the database"""
        self.entries.clear()
        return True

    def save(self, path: Optional[str] = None):
        """Save the vector database to disk"""
        save_path = path or self.persist_path
        if not save_path:
            raise ValueError("No save path provided")

        save_data = {
            "entries": {vid: e.to_dict() for vid, e in self.entries.items()},
            "dimension": self.dimension
        }

        with open(save_path, 'w') as f:
            json.dump(save_data, f, indent=2, default=str)

    def load(self, path: Optional[str] = None):
        """Load the vector database from disk"""
        load_path = path or self.persist_path
        if not load_path:
            raise ValueError("No load path provided")

        if os.path.exists(load_path):
            with open(load_path, 'r') as f:
                save_data = json.load(f)

            self.entries = {
                vid: VectorEntry.from_dict(e)
                for vid, e in save_data["entries"].items()
            }
            self.dimension = save_data["dimension"]

class VectorEncoder:
    """Helper class for encoding text to vectors"""

    def __init__(self, model_name: str = 'text-embedding-3-small', use_openai: bool = True):
        """Initialize the vector encoder

        Args:
            model_name: Model name (OpenAI or sentence-transformers)
            use_openai: Whether to use OpenAI embeddings (default: True)
        """
        self.use_openai = use_openai

        if use_openai:
            # Use OpenAI encoder
            try:
                from .openai_encoder import OpenAIVectorEncoder
                self.encoder = OpenAIVectorEncoder(model_name)
                self.dimension = self.encoder.dimension
                logger.info(f"Using OpenAI embeddings ({model_name}, {self.dimension} dims)")
            except Exception as e:
                logger.warning(f"Failed to initialize OpenAI encoder: {e}")
                logger.info("Falling back to sentence-transformers")
                self.use_openai = False

        if not self.use_openai:
            # Use sentence-transformers
            if not SENTENCE_TRANSFORMER_AVAILABLE:
                raise ImportError("SentenceTransformer not available. Install with: pip install sentence-transformers")

            # Revert to default model if OpenAI model name was passed
            if model_name.startswith('text-embedding'):
                model_name = 'all-MiniLM-L6-v2'

            self.model = SentenceTransformer(model_name, device=local_model_device())
            self.dimension = self.model.get_sentence_embedding_dimension()
            logger.info(f"Using sentence-transformers ({model_name}, {self.dimension} dims)")

    def encode(self, texts: Union[str, List[str]]) -> np.ndarray:
        """Encode text(s) to vectors"""
        if self.use_openai:
            return self.encoder.encode(texts)
        else:
            if isinstance(texts, str):
                texts = [texts]
            embeddings = self.model.encode(texts, convert_to_numpy=True)
            return embeddings.astype(np.float32)

    def encode_batch(self, texts: List[str], batch_size: int = 32) -> np.ndarray:
        """Encode texts in batches for efficiency"""
        if self.use_openai:
            # OpenAI encoder handles batching internally
            return self.encoder.encode_batch(texts, batch_size=min(batch_size, 100))
        else:
            embeddings = self.model.encode(
                texts,
                batch_size=batch_size,
                convert_to_numpy=True,
                show_progress_bar=len(texts) > 100
            )
            return embeddings.astype(np.float32)

    def encode_with_timeout(self, texts, timeout_seconds):
        """Bound remote embedding requests during Jev-Mem retrieval."""
        if self.use_openai:
            return self.encoder.encode(texts, timeout_seconds=timeout_seconds)
        return self.encode(texts)

def create_vector_db(backend: str = "auto", dimension: int = 1536,
                    persist_path: Optional[str] = None) -> VectorDBInterface:
    """
    Factory function to create appropriate vector database

    Args:
        backend: "faiss", "numpy", or "auto" (auto-detect)
        dimension: Vector dimension
        persist_path: Path to persist the database

    Returns:
        VectorDBInterface implementation
    """
    if backend == "auto":
        backend = "faiss" if FAISS_AVAILABLE else "numpy"

    if backend == "faiss":
        if not FAISS_AVAILABLE:
            logging.warning("FAISS requested but not available, falling back to NumPy")
            return NumpyVectorDB(dimension, persist_path)
        return FAISSVectorDB(dimension, persist_path=persist_path)
    elif backend == "numpy":
        return NumpyVectorDB(dimension, persist_path)
    else:
        raise ValueError(f"Unknown backend: {backend}")
