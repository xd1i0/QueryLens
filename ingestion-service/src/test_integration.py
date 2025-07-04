import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, Mock
import json
import io
from elasticsearch import ConnectionError, TransportError

class TestDocumentIndexing:
    def test_index_valid_document(self, client, mock_es, sample_doc):
        """Test indexing a valid document"""
        mock_es.index.return_value = {"result": "created", "_id": "test-doc-1"}

        response = client.post("/docs/", json=sample_doc)

        assert response.status_code == 201
        assert response.json() == {"result": "created", "id": "test-doc-1"}
        mock_es.index.assert_called_once()

    def test_index_document_missing_id(self, client, mock_es):
        """Test indexing document with missing ID"""
        doc_data = {
            "title": "Test Document",
            "content": "Test content"
        }

        response = client.post("/docs/", json=doc_data)

        assert response.status_code == 422  # Pydantic validation error

    def test_index_document_empty_title(self, client, mock_es):
        """Test indexing document with empty title"""
        doc_data = {
            "id": "test-1",
            "title": "",
            "content": "Test content"
        }

        response = client.post("/docs/", json=doc_data)

        assert response.status_code == 400
        assert "Document title is required" in response.json()["error"]["message"]

    def test_index_document_empty_content(self, client, mock_es):
        """Test indexing document with empty content"""
        doc_data = {
            "id": "test-1",
            "title": "Test Document",
            "content": ""
        }

        response = client.post("/docs/", json=doc_data)

        assert response.status_code == 400
        assert "Document content is required" in response.json()["error"]["message"]

    def test_index_document_es_unavailable(self, client, mock_es, sample_doc):
        """Test indexing with ES unavailable"""
        mock_es.index.side_effect = ConnectionError("ES unavailable")

        response = client.post("/docs/", json=sample_doc)

        assert response.status_code == 503
        assert "Elasticsearch service unavailable" in response.json()["error"]["message"]

    def test_index_document_duplicate_id(self, client, mock_es, sample_doc):
        """Test indexing document with duplicate ID"""
        transport_error = TransportError("Document already exists")
        transport_error.status_code = 409
        mock_es.index.side_effect = transport_error

        response = client.post("/docs/", json=sample_doc)

        assert response.status_code == 409
        assert "already exists" in response.json()["error"]["message"]

class TestFileUpload:
    def test_upload_markdown_file(self, client, mock_es):
        """Test uploading a markdown file"""
        mock_es.index.return_value = {"result": "created", "_id": "test-md-1"}

        files = {"file": ("test.md", "# Test\n\nThis is a test.", "text/markdown")}
        data = {"doc_id": "test-md-1", "title": "Test Markdown"}

        response = client.post("/docs/upload/", files=files, data=data)

        assert response.status_code == 200
        assert response.json()["result"] == "created"
        assert response.json()["id"] == "test-md-1"

    def test_upload_file_missing_id(self, client, mock_es):
        """Test uploading file with missing document ID"""
        files = {"file": ("test.md", "# Test", "text/markdown")}
        data = {"title": "Test Markdown"}

        response = client.post("/docs/upload/", files=files, data=data)

        assert response.status_code == 422  # FastAPI validation error

    def test_upload_file_no_filename(self, client, mock_es):
        """Test uploading file without filename"""
        files = {"file": ("", "content", "text/plain")}
        data = {"doc_id": "test-1"}

        response = client.post("/docs/upload/", files=files, data=data)

        assert response.status_code == 422

    def test_upload_file_too_large(self, client, mock_es):
        """Test uploading file that exceeds size limit"""
        large_content = "x" * (11 * 1024 * 1024)  # 11MB
        files = {"file": ("large.txt", large_content, "text/plain")}
        data = {"doc_id": "test-1"}

        response = client.post("/docs/upload/", files=files, data=data)

        assert response.status_code == 413
        assert "File size exceeds 10MB limit" in response.json()["error"]["message"]

    def test_upload_file_with_chunking(self, client, mock_es, large_document_content):
        """Test uploading large file with chunking enabled"""
        mock_es.index.return_value = {"result": "created", "_id": "chunk_0"}

        files = {"file": ("large.md", f"# Large Doc\n\n{large_document_content}", "text/markdown")}
        data = {"doc_id": "large-doc-1", "enable_chunking": "true"}

        response = client.post("/docs/upload/", files=files, data=data)

        assert response.status_code == 200
        assert response.json()["result"] == "created"
        assert response.json()["chunks"] > 1

class TestDocumentRetrieval:
    def test_get_existing_document(self, client, mock_es, sample_doc):
        """Test retrieving existing document"""
        mock_es.get.return_value = {"_source": sample_doc}

        response = client.get("/docs/test-doc-1")

        assert response.status_code == 200
        assert response.json() == sample_doc

    def test_get_nonexistent_document(self, client, mock_es):
        """Test retrieving non-existent document"""
        mock_es.get.side_effect = Exception("not_found")

        response = client.get("/docs/nonexistent")

        assert response.status_code == 404
        assert "not found" in response.json()["error"]["message"]

    def test_get_document_es_unavailable(self, client, mock_es):
        """Test retrieving document with ES unavailable"""
        mock_es.get.side_effect = ConnectionError("ES unavailable")

        response = client.get("/docs/test-doc-1")

        assert response.status_code == 503
        assert "Elasticsearch service unavailable" in response.json()["error"]["message"]

class TestSearchFunctionality:
    def test_search_no_results(self, client, mock_es):
        """Test search with no results"""
        mock_es.search.return_value = {
            "hits": {
                "total": {"value": 0},
                "hits": []
            }
        }

        response = client.get("/search/?q=nonexistent")

        assert response.status_code == 200
        assert response.json()["total"] == 0
        assert response.json()["results"] == []

    def test_search_single_result(self, client, mock_es, sample_doc):
        """Test search with single result"""
        mock_es.search.return_value = {
            "hits": {
                "total": {"value": 1},
                "hits": [{"_id": "test-doc-1", "_source": sample_doc, "_score": 1.5}]
            }
        }

        response = client.get("/search/?q=test")

        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert len(response.json()["results"]) == 1
        assert response.json()["results"][0]["id"] == "test-doc-1"
        assert response.json()["results"][0]["score"] == 1.5

    def test_search_multiple_results(self, client, mock_es):
        """Test search with multiple results"""
        mock_results = [
            {"_id": "doc-1", "_source": {"title": "Doc 1", "content": "Content 1"}, "_score": 2.0},
            {"_id": "doc-2", "_source": {"title": "Doc 2", "content": "Content 2"}, "_score": 1.5},
            {"_id": "doc-3", "_source": {"title": "Doc 3", "content": "Content 3"}, "_score": 1.0}
        ]

        mock_es.search.return_value = {
            "hits": {
                "total": {"value": 3},
                "hits": mock_results
            }
        }

        response = client.get("/search/?q=test&size=5")

        assert response.status_code == 200
        assert response.json()["total"] == 3
        assert len(response.json()["results"]) == 3

    def test_search_empty_query(self, client, mock_es):
        """Test search with empty query"""
        response = client.get("/search/?q=")

        assert response.status_code == 400
        assert "Search query is required" in response.json()["error"]["message"]

    def test_search_invalid_size(self, client, mock_es):
        """Test search with invalid size parameter"""
        response = client.get("/search/?q=test&size=0")

        assert response.status_code == 400
        assert "Size must be between 1 and 100" in response.json()["error"]["message"]

    def test_search_size_too_large(self, client, mock_es):
        """Test search with size parameter too large"""
        response = client.get("/search/?q=test&size=101")

        assert response.status_code == 400
        assert "Size must be between 1 and 100" in response.json()["error"]["message"]

    def test_search_es_unavailable(self, client, mock_es):
        """Test search with ES unavailable"""
        mock_es.search.side_effect = ConnectionError("ES unavailable")

        response = client.get("/search/?q=test")

        assert response.status_code == 503
        assert "Elasticsearch service unavailable" in response.json()["error"]["message"]

    def test_search_with_chunked_results(self, client, mock_es):
        """Test search with chunked document results"""
        mock_results = [
            {"_id": "doc-1_chunk_0", "_source": {"title": "Doc 1 (Part 1)", "content": "Chunk 1", "tags": ["chunk", "parent:doc-1"]}, "_score": 2.0},
            {"_id": "doc-1_chunk_1", "_source": {"title": "Doc 1 (Part 2)", "content": "Chunk 2", "tags": ["chunk", "parent:doc-1"]}, "_score": 1.8}
        ]

        mock_es.search.return_value = {
            "hits": {
                "total": {"value": 2},
                "hits": mock_results
            }
        }

        response = client.get("/search/?q=test&group_chunks=true")

        assert response.status_code == 200
        assert response.json()["total"] == 2
        # Should be grouped into single result
        assert len(response.json()["results"]) == 1
        assert response.json()["results"][0]["id"] == "doc-1"

class TestHealthCheck:
    def test_root_endpoint_healthy(self, client, mock_es):
        """Test root endpoint when ES is healthy"""
        mock_es.ping.return_value = True

        response = client.get("/")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["elasticsearch_status"] == "connected"

    def test_root_endpoint_es_down(self, client, mock_es):
        """Test root endpoint when ES is down"""
        mock_es.ping.return_value = False

        response = client.get("/")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["elasticsearch_status"] == "disconnected"

    def test_root_endpoint_es_error(self, client, mock_es):
        """Test root endpoint when ES has error"""
        mock_es.ping.side_effect = Exception("ES error")

        response = client.get("/")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["elasticsearch_status"] == "error"
