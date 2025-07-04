import pytest
import os
from fastapi.testclient import TestClient
from elasticsearch import Elasticsearch
from unittest.mock import Mock, patch
import tempfile
import json

# Test environment variables
os.environ["ELASTICSEARCH_HOST"] = "http://localhost:9200"
os.environ["ELASTICSEARCH_INDEX"] = "test_docs"
os.environ["ELASTICSEARCH_VERIFY_CERTS"] = "false"

@pytest.fixture
def mock_es():
    """Mock Elasticsearch client"""
    with patch('main.es') as mock:
        mock.ping.return_value = True
        mock.indices.exists.return_value = False
        mock.indices.create.return_value = {"acknowledged": True}
        yield mock

@pytest.fixture
def client(mock_es):
    """FastAPI test client"""
    from main import app
    return TestClient(app)

@pytest.fixture
def sample_doc():
    """Sample document for testing"""
    return {
        "id": "test-doc-1",
        "title": "Test Document",
        "content": "This is a test document with some content for testing purposes.",
        "tags": ["test", "sample"],
        "author": "Test Author"
    }

@pytest.fixture
def sample_pdf_content():
    """Sample PDF content as bytes"""
    return b"Sample PDF content for testing"

@pytest.fixture
def sample_markdown_content():
    """Sample Markdown content"""
    return "# Test Document\n\nThis is a test markdown document."

@pytest.fixture
def large_document_content():
    """Large document content for chunking tests"""
    return " ".join([f"This is sentence {i} in a large document." for i in range(1000)])