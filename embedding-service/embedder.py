import time
import numpy as np
from typing import List, Optional, Tuple, Dict, Any
import threading

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    raise ImportError("Please install sentence-transformers: pip install sentence-transformers")

from config import config

class ModelPool:
    """Simple thread-safe pool for model instances."""
    def __init__(self, model_name: str, size: int = 1):
        self.model_name = model_name
        self.size = size
        self._pool = [SentenceTransformer(model_name) for _ in range(size)]
        self._lock = threading.Lock()
        self._idx = 0

    def get(self):
        with self._lock:
            model = self._pool[self._idx % self.size]
            self._idx += 1
            return model

class Embedder:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self.pool = ModelPool(model_name, size=1)
        self.dim = config.supported_models[model_name]["dim"]

    def encode(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
        normalize: Optional[bool] = None
    ) -> Tuple[np.ndarray, Dict[str, float]]:
        model = self.pool.get()
        batch_size = batch_size or config.default_batch_size
        normalize = normalize if normalize is not None else True

        timing = {}
        t0 = time.time()
        vectors = model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=normalize,
            show_progress_bar=False
        )
        timing["encode"] = time.time() - t0

        if not isinstance(vectors, np.ndarray):
            vectors = np.array(vectors)
        return vectors, timing
