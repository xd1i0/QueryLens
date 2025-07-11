import pytest
from main import (
    extract_text_from_file, chunk_text, should_chunk_document
)
from models import Doc

class TestTextExtraction:
    def test_extract_text_from_markdown(self):
        """Test markdown text extraction"""
        content = b"# Test\n\nThis is a test."
        filename = "test.md"
        result_content = extract_text_from_file(content, filename)
        assert "# Test" in result_content

    def test_extract_text_empty_content(self):
        """Test empty file content"""
        result_content = extract_text_from_file(b"", "test.txt")
        assert result_content == ""

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
