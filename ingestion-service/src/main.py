import os
import time
from dotenv import load_dotenv
from elasticsearch import Elasticsearch, ConnectionError, TransportError
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from models import Doc
import tempfile
from typing import Optional, List
import magic
import textract
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import logging

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ES_HOST = os.getenv("ELASTICSEARCH_HOST", "http://localhost:9200")
ES_USERERNAME = os.getenv("ELASTICSEARCH_USERNAME")
ES_PASSWORD = os.getenv("ELASTICSEARCH_PASSWORD")
ES_VERIFY_CERTS = os.getenv("ELASTICSEARCH_VERIFY_CERTS", "false").lower() in ["true", "1"]
INDEX = os.getenv("ELASTICSEARCH_INDEX", "docs")

APP_TITLE = os.getenv("APP_TITLE", "QueryLens Ingestion & Search")
CHUNK_MAX_TOKENS = int(os.getenv("CHUNK_MAX_TOKENS", "250"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))
CHUNK_THRESHOLD_WORDS = int(os.getenv("CHUNK_THRESHOLD_WORDS", "500"))

es_config = {
    "hosts": [ES_HOST],
    "verify_certs": ES_VERIFY_CERTS,
    "timeout": 30,
    "max_retries": 3,
    "retry_on_timeout": True,
}

if ES_USERERNAME and ES_PASSWORD:
    es_config["basic_auth"] = (ES_USERERNAME, ES_PASSWORD)

es = Elasticsearch(**es_config)

app = FastAPI(title=APP_TITLE)

# Allow all origins for local testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "type": "http_error",
                "status_code": exc.status_code,
                "message": exc.detail,
                "path": str(request.url.path)
            }
        }
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "type": "internal_server_error",
                "status_code": 500,
                "message": "An internal server error occurred",
                "path": str(request.url.path)
            }
        }
    )

# Retry decorator for ES operations
@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type((ConnectionError, TransportError)),
    reraise=True
)
def es_operation_with_retry(operation, *args, **kwargs):
    """Execute Elasticsearch operation with retry logic"""
    try:
        return operation(*args, **kwargs)
    except Exception as e:
        logger.warning(f"ES operation failed, will retry: {str(e)}")
        raise

def wait_for_es(max_retries=30, delay=2):
    """Wait for Elasticsearch to be ready"""
    for i in range(max_retries):
        try:
            if es.ping():
                logger.info(f"Connected to Elasticsearch at {ES_HOST}")
                return True
        except Exception as e:
            logger.info(f"Attempt {i+1}: Waiting for Elasticsearch... ({e})")
            time.sleep(delay)
    return False

def extract_text_from_file(file_content: bytes, filename: str) -> tuple[str, str]:
    """Extract text from a file using textract"""
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required")
    if not file_content:
        raise HTTPException(status_code=400, detail="File content is empty")

    try:
        if filename.lower().endswith('.md'):
            try:
                content = file_content.decode("utf-8")
                return content, "text/markdown"
            except UnicodeDecodeError:
                raise HTTPException(status_code=400, detail="Invalid UTF-8 encoding in markdown file")

        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}") as temp_file:
            temp_file.write(file_content)
            temp_file.flush()

            try:
                file_type = magic.from_file(temp_file.name, mime=True)
            except Exception:
                file_type = "application/octet-stream"

            try:
                content = textract.process(temp_file.name).decode("utf-8")
                content = content.strip()
            except Exception as e:
                raise HTTPException(status_code=422, detail=f"Unsupported file format or corrupted file: {str(e)}")
            finally:
                os.unlink(temp_file.name)

            if not content:
                raise HTTPException(status_code=422, detail="No text content could be extracted from the file")

            return content, file_type
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in text extraction: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to process file")

def chunk_text(text: str, max_tokens: int = None, overlap: int = None) -> list[str]:
    """Chunk text into smaller pieces for better indexing"""
    if not text or not text.strip():
        return []

    max_tokens = max_tokens or CHUNK_MAX_TOKENS
    overlap = overlap or CHUNK_OVERLAP

    tokens = text.split()
    chunks = []

    for i in range(0, len(tokens), max_tokens - overlap):
        chunk_words = tokens[i : i + max_tokens]
        chunk = " ".join(chunk_words)
        if chunk.strip():
            chunks.append(chunk.strip())

    return chunks

def should_chunk_document(content: str, threshold_words: int = None) -> bool:
    if not content:
        return False
    threshold_words = threshold_words or CHUNK_THRESHOLD_WORDS
    word_count = len(content.split())
    return word_count >= threshold_words

def create_index_mapping():
    """Create index with explicit mapping for metadata fields"""
    mapping = {
        "mappings": {
            "properties": {
                "id": {"type": "keyword"},
                "title": {"type": "text", "analyzer": "standard"},
                "content": {"type": "text", "analyzer": "standard"},
                "tags": {"type": "keyword"},
                "file_type": {"type": "keyword"},
                "original_filename": {"type": "keyword"},
                "author": {"type": "keyword"},
                "timestamp": {"type": "date"},
                "source_system": {"type": "keyword"},
                "file_size_bytes": {"type": "long"},
                "chunk_index": {"type": "integer"},
                "parent_document_id": {"type": "keyword"}
            }
        }
    }

    try:
        if not es_operation_with_retry(es.indices.exists, index=INDEX):
            es_operation_with_retry(es.indices.create, index=INDEX, body=mapping)
            logger.info(f"Created index '{INDEX}' with mapping")
    except Exception as e:
        logger.error(f"Error creating index mapping: {e}")
        raise

@app.on_event("startup")
async def startup():
    logger.info("Starting up application...")

    if not wait_for_es():
        logger.warning(f"Could not connect to Elasticsearch at {ES_HOST}")
        raise RuntimeError("Elasticsearch connection failed")
    else:
        create_index_mapping()
    logger.info("Application startup complete")

@app.get("/")
def root():
    try:
        es_status = "connected" if es.ping() else "disconnected"
    except Exception:
        es_status = "error"
    return {"status": "ok", "elasticsearch": ES_HOST, "elasticsearch_status": es_status}

@app.post("/docs/", status_code=201)
async def index_doc(doc: Doc):
    """Index a document with structured data"""
    if not doc.id or not doc.id.strip():
        raise HTTPException(status_code=400, detail="Document ID is required and cannot be empty")

    if not doc.title or not doc.title.strip():
        raise HTTPException(status_code=400, detail="Document title is required and cannot be empty")

    if not doc.content or not doc.content.strip():
        raise HTTPException(status_code=400, detail="Document content is required and cannot be empty")

    try:
        resp = es_operation_with_retry(es.index, index=INDEX, id=doc.id, document=doc.dict())
        return {"result": resp["result"], "id": resp["_id"]}
    except ConnectionError:
        raise HTTPException(status_code=503, detail="Elasticsearch service unavailable")
    except TransportError as e:
        if e.status_code == 409:
            raise HTTPException(status_code=409, detail=f"Document with ID '{doc.id}' already exists")
        raise HTTPException(status_code=500, detail="Failed to index document due to storage error")
    except Exception as e:
        logger.error(f"Failed to index document: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to index document")

@app.post("/docs/upload/")
async def upload_doc(
        file: UploadFile = File(...),
        doc_id: str = Form(...),
        title: Optional[str] = Form(None),
        tags: Optional[str] = Form(None),
        author: Optional[str] = Form(None),
        source_system: Optional[str] = Form("ingestion-api"),
        enable_chunking: bool = Form(False)
):
    """Upload and index a document from a file (PDF, Word, Markdown, etc.)"""
    # Input validation
    if not doc_id or not doc_id.strip():
        raise HTTPException(status_code=400, detail="Document ID is required and cannot be empty")

    if not file.filename:
        raise HTTPException(status_code=400, detail="File must have a filename")

    # File size validation (10MB limit)
    file_content = await file.read()
    if len(file_content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File size exceeds 10MB limit")

    try:
        extracted_content, file_type = extract_text_from_file(file_content, file.filename)

        # Parse tags if provided
        tags_list = []
        if tags:
            try:
                tags_list = [tag.strip() for tag in tags.split(",") if tag.strip()]
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid tags format. Use comma-separated values.")

        base_metadata = {
            "file_type": file_type,
            "original_filename": file.filename,
            "author": author,
            "source_system": source_system,
            "file_size_bytes": len(file_content),
        }

        if enable_chunking and should_chunk_document(extracted_content):
            return await index_chunked_document(
                doc_id,
                title or file.filename,
                extracted_content,
                tags_list,
                base_metadata
            )
        else:
            doc = Doc(
                id=doc_id,
                title=title or file.filename or "Untitled",
                content=extracted_content,
                tags=tags_list,
                **base_metadata
            )

            response = es_operation_with_retry(es.index, index=INDEX, id=doc.id, document=doc.dict())

            return {
                "result": response["result"],
                "id": response["_id"],
                "extracted_chars": len(extracted_content),
                "chunks": 1,
                **base_metadata,
            }

    except HTTPException:
        raise
    except ConnectionError:
        raise HTTPException(status_code=503, detail="Elasticsearch service unavailable")
    except Exception as e:
        logger.error(f"Failed to upload document: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to process and index document")

async def index_chunked_document(doc_id: str, title: str, content: str, tags: List[str], metadata: dict):
    """Index a document in chunks for better searchability"""
    chunks = chunk_text(content)
    if not chunks:
        raise HTTPException(status_code=422, detail="Document content could not be chunked")

    indexed_chunks = []

    try:
        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc_id}_chunk_{i}"
            doc = Doc(
                id=chunk_id,
                title=f"{title} (Part {i + 1})",
                content=chunk,
                tags=tags + ["chunk", f"parent:{doc_id}"],
                chunk_index=i,
                parent_document_id=doc_id,
                **metadata
            )

            response = es_operation_with_retry(es.index, index=INDEX, id=chunk_id, document=doc.dict())
            indexed_chunks.append(chunk_id)

        return {
            "result": "created",
            "parent_id": doc_id,
            "chunks": len(indexed_chunks),
            "chunk_ids": indexed_chunks,
        }
    except ConnectionError:
        raise HTTPException(status_code=503, detail="Elasticsearch service unavailable during chunked indexing")
    except Exception as e:
        logger.error(f"Failed to index chunked document: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to index chunked document")

@app.get("/docs/{doc_id}")
async def get_doc(doc_id: str):
    if not doc_id or not doc_id.strip():
        raise HTTPException(status_code=400, detail="Document ID is required")

    try:
        resp = es_operation_with_retry(es.get, index=INDEX, id=doc_id)
        return resp["_source"]
    except ConnectionError:
        raise HTTPException(status_code=503, detail="Elasticsearch service unavailable")
    except Exception as e:
        if "not_found" in str(e).lower():
            raise HTTPException(status_code=404, detail=f"Document with ID '{doc_id}' not found")
        logger.error(f"Failed to retrieve document: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve document")

@app.get("/search/")
async def search(q: str, size: int = 10, group_chunks: bool = True):
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Search query is required and cannot be empty")

    if size < 1 or size > 100:
        raise HTTPException(status_code=400, detail="Size must be between 1 and 100")

    try:
        body = {
            "query": {
                "multi_match": {
                    "query": q,
                    "fields": ["title^2", "content"]
                }
            },
            "size": size * 3 if group_chunks else size
        }

        resp = es_operation_with_retry(es.search, index=INDEX, body=body)

        hits = [
            {"id": hit["_id"], **hit["_source"], "score": hit["_score"]}
            for hit in resp["hits"]["hits"]
        ]

        if group_chunks:
            hits = group_chunk_results(hits, size)

        return {"total": resp["hits"]["total"]["value"], "results": hits}
    except ConnectionError:
        raise HTTPException(status_code=503, detail="Elasticsearch service unavailable")
    except Exception as e:
        logger.error(f"Search failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Search operation failed")

def group_chunk_results(hits: List[dict], limit: int) -> List[dict]:
    """Group chunks from same document"""
    grouped = {}

    for hit in hits:
        if "chunk" in hit.get("tags", []):
            parent_tag = next((tag for tag in hit["tags"] if tag.startswith("parent:")), None)
            if parent_tag:
                parent_id = parent_tag.split(":", 1)[1]
                if parent_id not in grouped:
                    grouped[parent_id] = {
                        "id": parent_id,
                        "title": hit["title"].split(" (Part ")[0],
                        "content": hit["content"][:200] + "...",
                        "chunks": [hit],
                        "score": hit["score"],
                        "tags": [tag for tag in hit["tags"] if not tag.startswith("parent:")]
                    }
                else:
                    grouped[parent_id]["chunks"].append(hit)
                    grouped[parent_id]["score"] = max(grouped[parent_id]["score"], hit["score"])
            else:
                grouped[hit["id"]] = hit
        else:
            grouped[hit["id"]] = hit

    return sorted(grouped.values(), key=lambda x: x["score"], reverse=True)[:limit]

# For local testing (not used in Docker)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)