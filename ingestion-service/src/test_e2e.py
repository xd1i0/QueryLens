import pytest
import requests
import time
import os
from elasticsearch import Elasticsearch

# Real Elasticsearch for E2E tests
ES_HOST = os.getenv("ELASTICSEARCH_HOST", "http://localhost:9200")
ES_INDEX = os.getenv("ELASTICSEARCH_INDEX", "test_docs_e2e")
API_BASE_URL = "http://localhost:8000"


@pytest.fixture(scope="session")
def es_client():
    """Real Elasticsearch client for E2E tests"""
    es = Elasticsearch([ES_HOST])

    # Wait for ES to be ready
    for _ in range(30):
        try:
            if es.ping():
                break
            time.sleep(1)
        except:
            time.sleep(1)

    try:
        es.options(ignore_status=[400, 404]).indices.delete(index=ES_INDEX)
    except:
        pass

    yield es

    try:
        es.options(ignore_status=[400, 404]).indices.delete(index=ES_INDEX)
    except:
        pass


@pytest.fixture(scope="session")
def api_client():
    """API client for E2E tests"""
    # Wait for API to be ready
    for _ in range(30):
        try:
            response = requests.get(f"{API_BASE_URL}/")
            if response.status_code == 200:
                break
            time.sleep(1)
        except:
            time.sleep(1)

    return requests.Session()


class TestEndToEnd:
    def test_full_document_lifecycle(self, api_client, es_client):
        """Test complete document lifecycle: index, search, retrieve"""

        # 1. Index a document
        doc_data = {
            "id": "e2e-test-1",
            "title": "End-to-End Test Document",
            "content": "This is a comprehensive test document for end-to-end testing of the ingestion service.",
            "tags": ["e2e", "test", "integration"]
        }

        response = api_client.post(f"{API_BASE_URL}/docs/", json=doc_data)
        assert response.status_code == 201

        # Wait for indexing
        time.sleep(2)

        # 2. Search for the document
        response = api_client.get(f"{API_BASE_URL}/search/?q=comprehensive")
        assert response.status_code == 200

        search_results = response.json()
        assert search_results["total"] >= 1

        # 3. Retrieve the document directly
        response = api_client.get(f"{API_BASE_URL}/docs/e2e-test-1")
        assert response.status_code == 200

        retrieved_doc = response.json()
        assert retrieved_doc["title"] == doc_data["title"]
        assert retrieved_doc["content"] == doc_data["content"]

    def test_file_upload_and_search(self, api_client, es_client):
        """Test file upload and subsequent search"""

        # Create test file
        test_content = "# E2E Test File\n\nThis is a test markdown file for end-to-end testing."

        files = {"file": ("e2e_test.md", test_content, "text/markdown")}
        data = {
            "doc_id": "e2e-file-1",
            "title": "E2E Test File",
            "tags": "e2e,file,markdown"
        }

        response = api_client.post(f"{API_BASE_URL}/docs/upload/", files=files, data=data)
        assert response.status_code == 200

        # Wait for indexing
        time.sleep(2)

        # Search for the uploaded file
        response = api_client.get(f"{API_BASE_URL}/search/?q=markdown")
        assert response.status_code == 200

        search_results = response.json()
        assert search_results["total"] >= 1

    def test_chunked_document_processing(self, api_client, es_client):
        """Test chunked document processing"""

        # Create large document
        large_content = "# Large Document\n\n" + "\n\n".join([
            f"This is paragraph {i} of a large document that should be chunked."
            for i in range(200)
        ])

        files = {"file": ("large_doc.md", large_content, "text/markdown")}
        data = {
            "doc_id": "e2e-large-1",
            "title": "Large E2E Document",
            "enable_chunking": "true"
        }

        response = api_client.post(f"{API_BASE_URL}/docs/upload/", files=files, data=data)
        assert response.status_code == 200

        result = response.json()
        assert result["chunks"] > 1

        # Wait for indexing
        time.sleep(2)

        # Search should return grouped results
        response = api_client.get(f"{API_BASE_URL}/search/?q=paragraph&group_chunks=true")
        assert response.status_code == 200

        search_results = response.json()
        assert search_results["total"] >= 1

    def test_error_handling(self, api_client):
        """Test error handling in real scenarios"""

        # Test invalid document
        response = api_client.post(f"{API_BASE_URL}/docs/", json={"invalid": "data"})
        assert response.status_code == 422

        # Test non-existent document retrieval (should return 404)!!!!!
        response = api_client.get(f"{API_BASE_URL}/docs/non-existent")
        assert response.status_code == 404

        # Test invalid search parameters
        response = api_client.get(f"{API_BASE_URL}/search/?q=&size=0")
        assert response.status_code == 400
