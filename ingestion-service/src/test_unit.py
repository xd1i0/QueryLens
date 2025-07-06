import pytest
import ssl
import os
from unittest.mock import Mock, patch, MagicMock
from fastapi import HTTPException
from main import (
    extract_text_from_file, chunk_text, should_chunk_document,
    create_index_mapping, es_operation_with_retry, wait_for_es,
    create_ssl_context
)
from models import Doc
import tempfile

class TestSecurityConfiguration:
    @patch('main.ES_HOST', 'http://localhost:9200')
    def test_create_ssl_context_http(self):
        """Test SSL context creation for HTTP connection"""
        context = create_ssl_context()
        assert context is None

    @patch('main.ES_HOST', 'https://localhost:9200')
    @patch('main.ES_DISABLE_SSL_VERIFICATION', True)
    def test_create_ssl_context_disabled_verification(self):
        """Test SSL context with disabled verification"""
        context = create_ssl_context()
        assert context is not None
        assert context.check_hostname is False
        assert context.verify_mode == ssl.CERT_NONE

    @patch('main.ES_HOST', 'https://localhost:9200')
    @patch('main.ES_VERIFY_CERTS', False)
    @patch('main.ES_DISABLE_SSL_VERIFICATION', False)
    def test_create_ssl_context_no_verification(self):
        """Test SSL context with certificate verification disabled"""
        context = create_ssl_context()
        assert context is not None
        assert context.check_hostname is False
        assert context.verify_mode == ssl.CERT_NONE

    @patch('main.ES_HOST', 'https://localhost:9200')
    @patch('main.ES_VERIFY_CERTS', True)
    @patch('main.ES_DISABLE_SSL_VERIFICATION', False)
    def test_create_ssl_context_with_ca_file(self, test_ca_cert):
        """Test SSL context with CA certificate file"""
        with patch('main.ES_CA_PATH', test_ca_cert):
            with patch('ssl.SSLContext.load_verify_locations') as mock_load:
                context = create_ssl_context()
                assert context is not None
                mock_load.assert_called_once_with(test_ca_cert)

    @patch('main.ES_HOST', 'https://localhost:9200')
    @patch('main.ES_VERIFY_CERTS', True)
    @patch('main.ES_CA_PATH', '/nonexistent/ca.crt')
    @patch('main.ES_DISABLE_SSL_VERIFICATION', False)
    def test_create_ssl_context_missing_ca_file(self):
        """Test SSL context with missing CA certificate file"""
        with patch('ssl.create_default_context') as mock_create_context:
            mock_context = Mock()
            mock_create_context.return_value = mock_context

            context = create_ssl_context()

            assert context is not None
            # Verify that load_default_certs was called when CA file is missing
            mock_context.load_default_certs.assert_called_once()

    @patch('main.ES_HOST', 'https://localhost:9200')
    @patch('main.ES_VERIFY_CERTS', True)
    @patch('main.ES_CA_PATH', '/nonexistent/ca.crt')
    @patch('main.ES_DISABLE_SSL_VERIFICATION', False)
    def test_create_ssl_context_fallback_to_disabled(self):
        """Test SSL context fallback to disabled verification"""
        with patch('ssl.create_default_context') as mock_create_context:
            mock_context = Mock()
            mock_context.load_default_certs.side_effect = ssl.SSLError("Failed to load default certs")
            mock_create_context.return_value = mock_context

            context = create_ssl_context()

            assert context is not None
            # Verify that the mock context was modified correctly
            assert mock_context.check_hostname is False
            assert mock_context.verify_mode == ssl.CERT_NONE
            # Verify that load_default_certs was called and failed
            mock_context.load_default_certs.assert_called_once()

class TestTextExtraction:
    def test_extract_text_from_markdown(self):
        """Test markdown text extraction"""
        content = b"# Test\n\nThis is a test."
        filename = "test.md"

        result_content, file_type = extract_text_from_file(content, filename)

        assert result_content == "# Test\n\nThis is a test."
        assert file_type == "text/markdown"

    def test_extract_text_invalid_markdown(self):
        """Test invalid UTF-8 markdown"""
        content = b"\xff\xfe\x00\x00"  # Invalid UTF-8
        filename = "test.md"

        with pytest.raises(HTTPException) as exc_info:
            extract_text_from_file(content, filename)

        assert exc_info.value.status_code == 400
        assert "Invalid UTF-8 encoding" in str(exc_info.value.detail)

    def test_extract_text_empty_content(self):
        """Test empty file content"""
        with pytest.raises(HTTPException) as exc_info:
            extract_text_from_file(b"", "test.txt")

        assert exc_info.value.status_code == 400
        assert "File content is empty" in str(exc_info.value.detail)

    def test_extract_text_no_filename(self):
        """Test missing filename"""
        with pytest.raises(HTTPException) as exc_info:
            extract_text_from_file(b"content", "")

        assert exc_info.value.status_code == 400
        assert "Filename is required" in str(exc_info.value.detail)

    @patch('main.textract')
    @patch('main.magic')
    def test_extract_text_from_pdf(self, mock_magic, mock_textract):
        """Test PDF text extraction"""
        mock_magic.from_file.return_value = "application/pdf"
        mock_textract.process.return_value = b"Extracted PDF content"

        content = b"fake pdf content"
        filename = "test.pdf"

        result_content, file_type = extract_text_from_file(content, filename)

        assert result_content == "Extracted PDF content"
        assert file_type == "application/pdf"

    @patch('main.textract')
    def test_extract_text_unsupported_format(self, mock_textract):
        """Test unsupported file format"""
        mock_textract.process.side_effect = Exception("Unsupported format")

        content = b"unsupported content"
        filename = "test.xyz"

        with pytest.raises(HTTPException) as exc_info:
            extract_text_from_file(content, filename)

        assert exc_info.value.status_code == 422
        assert "Unsupported file format" in str(exc_info.value.detail)

class TestTextChunking:
    def test_chunk_text_basic(self):
        """Test basic text chunking"""
        text = "This is a test document with multiple words for chunking."
        chunks = chunk_text(text, max_tokens=5, overlap=2)

        assert len(chunks) > 1
        assert all(len(chunk.split()) <= 5 for chunk in chunks)

    def test_chunk_text_empty(self):
        """Test chunking empty text"""
        chunks = chunk_text("")
        assert chunks == []

    def test_chunk_text_short(self):
        """Test chunking short text"""
        text = "Short text"
        chunks = chunk_text(text, max_tokens=10, overlap=2)

        assert len(chunks) == 1
        assert chunks[0] == text

    def test_should_chunk_document_true(self):
        """Test document should be chunked"""
        content = " ".join(["word"] * 600)  # 600 words
        assert should_chunk_document(content, threshold_words=500) == True

    def test_should_chunk_document_false(self):
        """Test document should not be chunked"""
        content = " ".join(["word"] * 400)  # 400 words
        assert should_chunk_document(content, threshold_words=500) == False

    def test_should_chunk_document_empty(self):
        """Test empty document chunking"""
        assert should_chunk_document("") == False

class TestElasticsearchOperations:
    def test_es_operation_with_retry_success(self):
        """Test successful ES operation"""
        mock_operation = Mock(return_value="success")

        result = es_operation_with_retry(mock_operation, "arg1", kwarg1="value1")

        assert result == "success"
        mock_operation.assert_called_once_with("arg1", kwarg1="value1")

    def test_es_operation_with_retry_failure(self):
        """Test ES operation with connection error"""
        from elasticsearch import ConnectionError
        mock_operation = Mock(side_effect=ConnectionError("Connection failed"))

        with pytest.raises(ConnectionError):
            es_operation_with_retry(mock_operation)

    def test_es_operation_with_retry_not_found(self):
        """Test ES operation with NotFoundError"""
        from elasticsearch import NotFoundError

        # Create proper NotFoundError with required arguments
        meta = Mock()
        meta.status = 404
        body = {"error": {"type": "document_missing_exception", "reason": "Document not found"}}

        mock_operation = Mock(side_effect=NotFoundError("Document not found", meta=meta, body=body))

        with pytest.raises(NotFoundError):
            es_operation_with_retry(mock_operation)

    def test_es_operation_with_retry_transport_error(self):
        """Test ES operation with TransportError"""
        from elasticsearch import TransportError

        # Create proper TransportError
        transport_error = TransportError("Transport failed")
        transport_error.status_code = 500

        mock_operation = Mock(side_effect=transport_error)

        with pytest.raises(TransportError):
            es_operation_with_retry(mock_operation)

    @patch('main.es')
    def test_wait_for_es_success(self, mock_es):
        """Test successful ES connection"""
        mock_es.ping.return_value = True

        result = wait_for_es(max_retries=1, delay=0.1)

        assert result == True

    @patch('main.es')
    def test_wait_for_es_failure(self, mock_es):
        """Test ES connection failure"""
        mock_es.ping.return_value = False

        result = wait_for_es(max_retries=1, delay=0.1)

        assert result == False

    @patch('main.es')
    def test_wait_for_es_ssl_error(self, mock_es):
        """Test ES connection with SSL certificate error"""
        mock_es.ping.side_effect = Exception("certificate verify failed")

        result = wait_for_es(max_retries=1, delay=0.1)

        assert result == False

    @patch('main.es')
    def test_create_index_mapping_new_index(self, mock_es):
        """Test creating new index mapping"""
        mock_es.indices.exists.return_value = False
        mock_es.indices.create.return_value = {"acknowledged": True}

        create_index_mapping()

        mock_es.indices.exists.assert_called_once()
        mock_es.indices.create.assert_called_once()

    @patch('main.es')
    def test_create_index_mapping_existing_index(self, mock_es):
        """Test with existing index"""
        mock_es.indices.exists.return_value = True

        create_index_mapping()

        mock_es.indices.exists.assert_called_once()
        mock_es.indices.create.assert_not_called()

class TestDocumentValidation:
    def test_doc_model_valid(self):
        """Test valid document model"""
        doc_data = {
            "id": "test-1",
            "title": "Test Document",
            "content": "Test content",
            "tags": ["test"]
        }

        doc = Doc(**doc_data)

        assert doc.id == "test-1"
        assert doc.title == "Test Document"
        assert doc.content == "Test content"
        assert doc.tags == ["test"]

    def test_doc_model_required_fields(self):
        """Test document model with missing required fields"""
        with pytest.raises(ValueError):
            Doc(title="Test", content="Test content")  # Missing id

    def test_doc_model_defaults(self):
        """Test document model with default values"""
        doc = Doc(id="test-1", title="Test", content="Test content")

        assert doc.tags == []
        assert doc.source_system == "ingestion-api"
        assert doc.timestamp is not None
