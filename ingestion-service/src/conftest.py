import pytest
import os
import ssl
from fastapi.testclient import TestClient
from elasticsearch import Elasticsearch
from unittest.mock import Mock, patch
import tempfile
import json

# Test environment variables with security configuration
os.environ["ELASTICSEARCH_HOST"] = "http://localhost:9200"
os.environ["ELASTICSEARCH_INDEX"] = "test_docs"
os.environ["ELASTICSEARCH_VERIFY_CERTS"] = "false"
os.environ["ELASTICSEARCH_DISABLE_SSL_VERIFICATION"] = "true"  # For testing
os.environ["ELASTICSEARCH_USERNAME"] = "elastic"
os.environ["ELASTICSEARCH_PASSWORD"] = "test123"
os.environ["ELASTICSEARCH_CA_PATH"] = "/tmp/test_ca.crt"

@pytest.fixture
def mock_es():
    """Mock Elasticsearch client with security configuration"""
    with patch('main.es') as mock:
        mock.ping.return_value = True
        mock.indices.exists.return_value = False
        mock.indices.create.return_value = {"acknowledged": True}
        mock.cluster.health.return_value = {"status": "green"}
        yield mock

@pytest.fixture
def secure_mock_es():
    """Mock Elasticsearch client configured for HTTPS"""
    with patch('main.es') as mock:
        mock.ping.return_value = True
        mock.indices.exists.return_value = False
        mock.indices.create.return_value = {"acknowledged": True}
        mock.cluster.health.return_value = {"status": "green"}
        # Mock SSL context creation
        with patch('main.create_ssl_context') as ssl_mock:
            ssl_mock.return_value = ssl.create_default_context()
            yield mock

@pytest.fixture
def client(mock_es):
    """FastAPI test client with mocked ES"""
    from main import app
    return TestClient(app)

@pytest.fixture
def secure_client(secure_mock_es):
    """FastAPI test client with secure ES configuration"""
    # Temporarily set HTTPS host for secure tests
    original_host = os.environ.get("ELASTICSEARCH_HOST")
    os.environ["ELASTICSEARCH_HOST"] = "https://localhost:9200"

    try:
        # Re-import main with new environment variable
        import importlib
        import main
        importlib.reload(main)
        yield TestClient(main.app)
    finally:
        # Restore original host
        if original_host:
            os.environ["ELASTICSEARCH_HOST"] = original_host
        else:
            os.environ.pop("ELASTICSEARCH_HOST", None)
        # Reload main again to restore original state
        importlib.reload(main)

@pytest.fixture
def ssl_disabled_client():
    """FastAPI test client with SSL verification disabled"""
    original_disable = os.environ.get("ELASTICSEARCH_DISABLE_SSL_VERIFICATION")
    original_host = os.environ.get("ELASTICSEARCH_HOST")

    os.environ["ELASTICSEARCH_DISABLE_SSL_VERIFICATION"] = "true"
    os.environ["ELASTICSEARCH_HOST"] = "https://localhost:9200"

    try:
        with patch('main.es') as mock:
            mock.ping.return_value = True
            mock.indices.exists.return_value = False
            mock.indices.create.return_value = {"acknowledged": True}
            mock.cluster.health.return_value = {"status": "green"}

            # Re-import main with new environment variables
            import importlib
            import main
            importlib.reload(main)
            yield TestClient(main.app)
    finally:
        # Restore original values
        if original_disable:
            os.environ["ELASTICSEARCH_DISABLE_SSL_VERIFICATION"] = original_disable
        else:
            os.environ.pop("ELASTICSEARCH_DISABLE_SSL_VERIFICATION", None)

        if original_host:
            os.environ["ELASTICSEARCH_HOST"] = original_host
        else:
            os.environ.pop("ELASTICSEARCH_HOST", None)

        # Reload main again to restore original state
        importlib.reload(main)

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

@pytest.fixture
def test_ca_cert():
    """Create temporary CA certificate for testing"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.crt', delete=False) as f:
        # Mock CA certificate content
        f.write("""-----BEGIN CERTIFICATE-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA7VXvKfZJMFdGlcNF5ZzC
-----END CERTIFICATE-----""")
        f.flush()
        yield f.name
    os.unlink(f.name)

@pytest.fixture
def create_es_exception():
    """Helper to create proper Elasticsearch exceptions"""
    def _create_exception(exception_class, message, status_code=500):
        from elasticsearch import NotFoundError, TransportError

        if exception_class == NotFoundError:
            # NotFoundError requires meta and body parameters
            meta = Mock()
            meta.status = status_code
            body = {"error": {"type": "test_exception", "reason": message}}
            return exception_class(message, meta=meta, body=body)
        elif exception_class == TransportError:
            # TransportError uses a different constructor
            error = TransportError(message)
            error.status_code = status_code
            return error
        else:
            # For other exceptions, use standard constructor
            return exception_class(message)
    return _create_exception
