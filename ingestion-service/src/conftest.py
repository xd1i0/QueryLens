import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def client():
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
