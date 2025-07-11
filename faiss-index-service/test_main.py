import unittest
from unittest.mock import patch, MagicMock
import numpy as np
import main
import json
from fastapi.testclient import TestClient  # Fix NameError by importing TestClient


class TestHelperFunctions(unittest.TestCase):
    def test_id_hash_consistency(self):
        h1 = main.id_hash("doc1", "chunk1")
        h2 = main.id_hash("doc1", "chunk1")
        self.assertEqual(h1, h2)

    def test_id_hash_uniqueness(self):
        h1 = main.id_hash("doc1", "chunk1")
        h2 = main.id_hash("doc2", "chunk1")
        self.assertNotEqual(h1, h2)

    def test_normalize_unit_vector(self):
        vec = np.array([3.0, 4.0], dtype='float32')
        norm_vec = main.normalize(vec)
        self.assertAlmostEqual(np.linalg.norm(norm_vec), 1.0, places=5)

    def test_normalize_zero_vector(self):
        vec = np.zeros(10, dtype='float32')
        norm_vec = main.normalize(vec)
        self.assertTrue(np.allclose(norm_vec, vec))

    def test_store_and_get_metadata(self):
        with patch("main.redis_client") as mock_redis:
            vec_id = 123
            doc_id = "doc"
            chunk_id = "chunk"
            metadata = {"foo": "bar"}
            mock_redis.hset = MagicMock()
            mock_redis.hgetall.return_value = {
                "metadata": json.dumps({"doc_id": doc_id, "chunk_id": chunk_id, "foo": "bar", "timestamp": 1234567890})
            }
            main.store_metadata(vec_id, doc_id, chunk_id, metadata)
            result = main.get_metadata(vec_id)
            self.assertEqual(result["doc_id"], doc_id)
            self.assertEqual(result["chunk_id"], chunk_id)
            self.assertEqual(result["foo"], "bar")
            self.assertIn("timestamp", result)


class TestVectorIngestion(unittest.TestCase):
    @patch("main.redis_client")
    def test_upsert_vector_existing(self, mock_redis):
        mock_redis.scan_iter.return_value = ["faiss_row:0"]
        mock_redis.get.return_value = str(main.id_hash("test-doc", "1"))
        doc_id = "test-doc"
        chunk_id = "1"
        vector = [0.2] * main.INDEX_DIM
        metadata = {"source": "unit-test"}
        vec_id = main.upsert_vector(doc_id, chunk_id, vector, metadata)
        self.assertIsInstance(vec_id, int)

    @patch("main.redis_client")
    def test_upsert_vector_new(self, mock_redis):
        mock_redis.scan_iter.return_value = []
        mock_redis.get.return_value = None
        doc_id = "new-doc"
        chunk_id = "1"
        vector = [0.1] * main.INDEX_DIM
        metadata = {"source": "unit-test"}
        vec_id = main.upsert_vector(doc_id, chunk_id, vector, metadata)
        self.assertIsInstance(vec_id, int)

    @patch("main.redis_client")
    def test_upsert_vector_dimension_mismatch(self, mock_redis):
        mock_redis.scan_iter.return_value = []
        mock_redis.get.return_value = None
        doc_id = "dim-doc"
        chunk_id = "1"
        vector = [0.1] * (main.INDEX_DIM + 1)  # Mismatched dimension
        metadata = {"source": "unit-test"}
        vec_id = main.upsert_vector(doc_id, chunk_id, vector, metadata)
        # Verify that the vector was inserted after index recreation
        self.assertIsInstance(vec_id, int)
        self.assertEqual(vec_id, main.id_hash(doc_id, chunk_id))


class TestEndToEndBatch(unittest.IsolatedAsyncioTestCase):
    @patch("main.redis_client")
    async def test_process_faiss_batch_deduplication(self, mock_redis):
        mock_redis.scan_iter.return_value = []
        mock_redis.get.return_value = None
        doc_id = "e2e-doc"
        vector = [0.3] * main.INDEX_DIM
        metadata = {"meta": "test"}
        batch = [
            {"doc_id": doc_id, "chunk_id": "1", "vector": vector, "metadata": metadata},
            {"doc_id": doc_id, "chunk_id": "1", "vector": vector, "metadata": metadata},  # Duplicate
            {"doc_id": doc_id, "chunk_id": "2", "vector": vector, "metadata": metadata},
        ]
        await main.process_faiss_batch(batch)

    @patch("main.redis_client")
    async def test_process_faiss_batch_empty(self, mock_redis):
        mock_redis.scan_iter.return_value = []
        mock_redis.get.return_value = None
        batch = []
        await main.process_faiss_batch(batch)  # Should handle empty batch gracefully

    @patch("main.redis_client")
    async def test_process_faiss_batch_invalid_vector(self, mock_redis):
        mock_redis.scan_iter.return_value = []
        mock_redis.get.return_value = None
        batch = [
            {"doc_id": "invalid-doc", "chunk_id": "1", "vector": "not-a-list", "metadata": {}}
        ]
        await main.process_faiss_batch(batch)  # Should skip invalid vector


class TestSearchEndpoint(unittest.TestCase):
    @patch("main.faiss_index")
    @patch("main.redis_client")
    def test_search_faiss_success(self, mock_redis, mock_faiss_index):
        mock_faiss_index.d = 384
        mock_faiss_index.search.return_value = (np.array([[0.9, 0.8]]), np.array([[0, 1]]))
        mock_redis.get.side_effect = lambda key: "vec_id_1" if key == "faiss_row:0" else "vec_id_2"

        client = TestClient(main.app)
        response = client.post("/search", json={"vector": [0.1] * 384, "top_k": 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ids": ["vec_id_1", "vec_id_2"], "scores": [0.9, 0.8]})

    @patch("main.faiss_index")
    def test_search_faiss_dimension_mismatch(self, mock_faiss_index):
        mock_faiss_index.d = 384

        client = TestClient(main.app)
        response = client.post("/search", json={"vector": [0.1] * 385, "top_k": 2})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Vector dimension", response.json()["detail"])

    @patch("main.faiss_index")
    def test_search_faiss_no_results(self, mock_faiss_index):
        mock_faiss_index.d = 384
        mock_faiss_index.search.return_value = (np.array([[]]), np.array([[-1]]))

        client = TestClient(main.app)
        response = client.post("/search", json={"vector": [0.1] * 384, "top_k": 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ids": [], "scores": []})


if __name__ == "__main__":
    unittest.main()