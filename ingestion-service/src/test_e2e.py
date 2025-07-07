import pytest
import time

@pytest.mark.usefixtures("client")
class TestIngestionE2E:
    def test_upload_markdown_file(self, client, sample_markdown_content):
        files = {"file": ("test.md", sample_markdown_content, "text/markdown")}
        data = {"doc_id": "e2e-md-1", "title": "E2E Markdown"}
        response = client.post("/docs/upload/", files=files, data=data)
        assert response.status_code == 201
        assert response.json()["result"] in ("single_chunk_produced", "chunks_produced")

    def test_upload_large_file_chunking(self, client, large_document_content):
        files = {"file": ("large.md", large_document_content, "text/markdown")}
        data = {"doc_id": "e2e-large-1", "enable_chunking": "true"}
        response = client.post("/docs/upload/", files=files, data=data)
        assert response.status_code == 201
        assert response.json()["chunks"] > 1

    def test_upload_file_missing_id(self, client, sample_markdown_content):
        files = {"file": ("test.md", sample_markdown_content, "text/markdown")}
        data = {"title": "No ID"}
        response = client.post("/docs/upload/", files=files, data=data)
        assert response.status_code == 422

    def test_upload_file_no_filename(self, client):
        files = {"file": ("", "content", "text/plain")}
        data = {"doc_id": "no-filename"}
        response = client.post("/docs/upload/", files=files, data=data)
        assert response.status_code == 422
