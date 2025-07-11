import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import numpy as np
import sys
import types

# Patch config and embedder before importing main
mock_config = types.SimpleNamespace(
    max_batch_size=32,
    max_texts_per_request=10,
    default_model="test-model",
    supported_models={"test-model": {}},
    redis_host="localhost",
    redis_port=6379,
    redis_db=0,
    api_host="127.0.0.1",
    api_port=8000,
    kafka_bootstrap="kafka:9092",
    default_batch_size=2
)
mock_embedder = MagicMock()
mock_embedder.model_name = "test-model"
mock_embedder.dim = 3
mock_embedder.pool = types.SimpleNamespace(size=1)
mock_embedder.encode.return_value = (np.array([[1.0, 2.0, 3.0]]), {"encode": 0.01})

sys.modules["config"] = types.SimpleNamespace(config=mock_config)
sys.modules["embedder"] = types.SimpleNamespace(Embedder=lambda model: mock_embedder)

with patch("redis.Redis") as mock_redis:
    mock_redis.return_value.ping.return_value = True
    mock_redis.return_value.setex.return_value = True
    import main as embedding_main

client = TestClient(embedding_main.app)

def test_health_check():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["model"] == "test-model"
    assert data["dimension"] == 3
    assert data["pool_size"] == 1

def test_encode_texts_success():
    payload = {
        "texts": ["hello world"],
        "model": "test-model"
    }
    resp = client.post("/encode", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "vectors" in data
    assert data["model"] == "test-model"
    assert data["version"] == "1.0.0"
    assert data["shape"] == [1, 3]
    assert "timing" in data
    assert isinstance(data["vectors"], list)
    assert isinstance(data["timing"], dict)

def test_encode_texts_with_options():
    payload = {
        "texts": ["hello world"],
        "model": "test-model",
        "options": {"normalize": True, "batch_size": 2}
    }
    resp = client.post("/encode", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "test-model"
    assert data["shape"] == [1, 3]

def test_encode_texts_invalid_model():
    payload = {
        "texts": ["hello world"],
        "model": "invalid-model"
    }
    resp = client.post("/encode", json=payload)
    assert resp.status_code == 400
    assert "Unsupported model" in resp.json()["detail"]

def test_encode_texts_empty_texts():
    payload = {
        "texts": [],
        "model": "test-model"
    }
    resp = client.post("/encode", json=payload)
    assert resp.status_code == 422  # Fails pydantic validation

def test_encode_texts_too_many_texts():
    payload = {
        "texts": ["a"] * (mock_config.max_texts_per_request + 1),
        "model": "test-model"
    }
    resp = client.post("/encode", json=payload)
    assert resp.status_code == 422

def test_encode_texts_redis_failure(monkeypatch):
    # Simulate redis_client.setex raising an exception
    with patch.object(embedding_main, "redis_client", create=True) as mock_redis:
        mock_redis.setex.side_effect = Exception("Redis down")
        payload = {
            "texts": ["hello world"],
            "model": "test-model"
        }
        resp = client.post("/encode", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == "test-model"

def test_encode_texts_embedder_failure(monkeypatch):
    # Simulate embedder.encode raising an exception
    with patch.object(embedding_main.embedder, "encode", side_effect=Exception("Embedder error")):
        payload = {
            "texts": ["hello world"],
            "model": "test-model"
        }
        resp = client.post("/encode", json=payload)
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Internal server error"

def test_models_endpoint():
    resp = client.get("/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "supported_models" in data
    assert data["default_model"] == "test-model"
    assert isinstance(data["supported_models"], list)
    assert "test-model" in data["supported_models"]
