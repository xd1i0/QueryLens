import os
from typing import Optional, List
from fastapi import FastAPI, UploadFile, File, Form, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from models import Doc
from datetime import datetime
import logging
import json
from contextlib import asynccontextmanager

from tika import parser
from aiokafka import AIOKafkaProducer
import asyncio

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")

producer: AIOKafkaProducer = None

def make_json_serializable(obj):
    """Recursively convert bytes to strings in dicts/lists for JSON serialization."""
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    elif isinstance(obj, dict):
        return {k: make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [make_json_serializable(i) for i in obj]
    else:
        return obj

# Placeholder for Kafka producer (replace with actual Kafka logic)
def produce_chunk_to_queue(doc_id: str, chunk_id: str, text: str, metadata: dict):
    # Message schema: { doc_id, chunk_id, text, metadata }
    message = {
        "doc_id": doc_id,
        "chunk_id": chunk_id,
        "text": text,
        "metadata": make_json_serializable(metadata),
    }
    if producer is not None:
        asyncio.create_task(
            producer.send_and_wait("raw-chunks", json.dumps(message).encode())
        )
    else:
        logging.warning("Kafka producer is not initialized. Skipping message send.")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CHUNK_MAX_TOKENS = int(os.getenv("CHUNK_MAX_TOKENS", "250"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))
CHUNK_THRESHOLD_WORDS = int(os.getenv("CHUNK_THRESHOLD_WORDS", "500"))

@asynccontextmanager
async def lifespan(app: FastAPI):
    global producer
    producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP)
    await producer.start()
    try:
        yield
    finally:
        await producer.stop()

app = FastAPI(title="QueryLens Ingestion Service", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def chunk_text(text: str, max_tokens: int = None, overlap: int = None) -> list[str]:
    if not text or not text.strip():
        return []
    max_tokens = max_tokens or CHUNK_MAX_TOKENS
    overlap = overlap or CHUNK_OVERLAP
    tokens = text.split()
    chunks = []
    for i in range(0, len(tokens), max_tokens - overlap):
        chunk_words = tokens[i: i + max_tokens]
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

def extract_text_from_file(file_content: bytes, filename: str) -> str:
    # Use Apache Tika for robust text extraction
    try:
        parsed = parser.from_buffer(file_content)
        text = parsed.get("content", "")
        return text if text else ""
    except Exception as e:
        logging.error(f"Tika extraction failed: {e}")
        return ""

@app.post("/docs/upload/", status_code=201)
async def upload_doc(
    file: UploadFile = File(...),
    doc_id: str = Form(...),
    title: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    author: Optional[str] = Form(None),
    source_system: Optional[str] = Form("ingestion-api"),
    enable_chunking: bool = Form(True),
    request: Request = None
):
    if not doc_id or not doc_id.strip():
        return JSONResponse(status_code=422, content={"detail": "Document ID is required and cannot be empty"})
    if not file.filename:
        return JSONResponse(status_code=422, content={"detail": "File must have a filename"})
    file_content = await file.read()
    file_size = len(file_content)
    extracted_content = extract_text_from_file(file_content, file.filename)
    tags_list = [tag.strip() for tag in tags.split(',')] if tags else []
    doc = Doc(
        id=doc_id,
        title=title or file.filename,
        content=extracted_content,
        tags=tags_list,
        author=author,
        source_system=source_system,
        timestamp=datetime.now()
    )
    base_metadata = {
        "file_type": "text/plain",
        "original_filename": file.filename,
        "author": doc.author,
        "source_system": doc.source_system,
        "file_size_bytes": file_size,
        "timestamp": doc.timestamp.isoformat(),
        "title": doc.title,
        "tags": doc.tags,
    }
    if enable_chunking and should_chunk_document(doc.content):
        chunks = chunk_text(doc.content)
        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc.id}_chunk_{i}"
            metadata = {**base_metadata, "chunk_index": i}
            produce_chunk_to_queue(doc.id, chunk_id, chunk, metadata)
        return {"result": "chunks_produced", "chunks": len(chunks)}
    else:
        chunk_id = f"{doc.id}_chunk_0"
        metadata = {**base_metadata, "chunk_index": 0}
        produce_chunk_to_queue(doc.id, chunk_id, doc.content, metadata)
        return {"result": "single_chunk_produced", "chunks": 1}

@app.post("/docs/webhook/", status_code=201)
async def webhook_doc(
    payload: dict = Body(...),
    request: Request = None
):
    doc = Doc(
        id=payload.get("doc_id"),
        title=payload.get("title", ""),
        content=payload.get("content", ""),
        tags=payload.get("tags", []),
        author=payload.get("author", ""),
        source_system=payload.get("source_system", "webhook"),
        timestamp=datetime.now()
    )
    if not doc.id or not doc.content:
        return JSONResponse(status_code=422, content={"detail": "doc_id and content are required"})
    base_metadata = {
        "file_type": "webhook",
        "original_filename": "",
        "author": doc.author,
        "source_system": doc.source_system,
        "file_size_bytes": len(doc.content.encode("utf-8")),
        "timestamp": doc.timestamp.isoformat(),
        "title": doc.title,
        "tags": doc.tags,
    }
    if should_chunk_document(doc.content):
        chunks = chunk_text(doc.content)
        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc.id}_chunk_{i}"
            metadata = {**base_metadata, "chunk_index": i}
            produce_chunk_to_queue(doc.id, chunk_id, chunk, metadata)
        return {"result": "chunks_produced", "chunks": len(chunks)}
    else:
        chunk_id = f"{doc.id}_chunk_0"
        metadata = {**base_metadata, "chunk_index": 0}
        produce_chunk_to_queue(doc.id, chunk_id, doc.content, metadata)
        return {"result": "single_chunk_produced", "chunks": 1}

@app.post("/docs/repo-poll/", status_code=201)
async def repo_poll_doc(
    payload: dict,
    request: Request = None
):
    doc = Doc(
        id=payload.get("doc_id"),
        title=payload.get("title", ""),
        content=payload.get("content", ""),
        tags=payload.get("tags", []),
        author=payload.get("author", ""),
        source_system=payload.get("source_system", "repo-poll"),
        timestamp=datetime.now()
    )
    if not doc.id or not doc.content:
        return JSONResponse(status_code=422, content={"detail": "doc_id and content are required"})
    base_metadata = {
        "file_type": "repo-poll",
        "original_filename": "",
        "author": doc.author,
        "source_system": doc.source_system,
        "file_size_bytes": len(doc.content.encode("utf-8")),
        "timestamp": doc.timestamp.isoformat(),
        "title": doc.title,
        "tags": doc.tags,
    }
    if should_chunk_document(doc.content):
        chunks = chunk_text(doc.content)
        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc.id}_chunk_{i}"
            metadata = {**base_metadata, "chunk_index": i}
            produce_chunk_to_queue(doc.id, chunk_id, chunk, metadata)
        return {"result": "chunks_produced", "chunks": len(chunks)}
    else:
        chunk_id = f"{doc.id}_chunk_0"
        metadata = {**base_metadata, "chunk_index": 0}
        produce_chunk_to_queue(doc.id, chunk_id, doc.content, metadata)
        return {"result": "single_chunk_produced", "chunks": 1}

@app.get("/")
def root():
    return {"status": "ok", "service": "ingestion", "mode": "lightweight", "queue": "raw-chunks"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
