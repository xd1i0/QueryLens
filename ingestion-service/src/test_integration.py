import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

class TestFileUpload:
    def test_upload_markdown_file(self):
        """Test uploading a markdown file"""
        files = {"file": ("test.md", "# Test\n\nThis is a test.", "text/markdown")}
        data = {"doc_id": "test-md-1", "title": "Test Markdown"}
        response = client.post("/docs/upload/", files=files, data=data)
        assert response.status_code == 201
        assert response.json()["result"] in ("single_chunk_produced", "chunks_produced")

    def test_upload_file_missing_id(self):
        """Test uploading file with missing document ID"""
        files = {"file": ("test.md", "# Test", "text/markdown")}
        data = {"title": "Test Markdown"}
        response = client.post("/docs/upload/", files=files, data=data)
        assert response.status_code == 422

    def test_upload_file_no_filename(self):
        """Test uploading file without filename"""
        files = {"file": ("", "content", "text/plain")}
        data = {"doc_id": "test-1"}
        response = client.post("/docs/upload/", files=files, data=data)
        assert response.status_code == 422

    def test_upload_file_with_chunking(self):
        """Test uploading large file with chunking enabled"""
        large_content = " ".join(["word"] * 1000)
        files = {"file": ("large.md", large_content, "text/markdown")}
        data = {"doc_id": "large-doc-1", "enable_chunking": "true"}
        response = client.post("/docs/upload/", files=files, data=data)
        assert response.status_code == 201
        assert response.json()["result"] in ("single_chunk_produced", "chunks_produced")
        assert response.json()["chunks"] >= 1
