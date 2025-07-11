import nltk
nltk.download('punkt_tab')

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import time
import logging
import redis
import numpy as np
import json
import threading
import asyncio
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from bs4 import BeautifulSoup
import os
from contextlib import asynccontextmanager
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# Import config and Embedder
from config import config  # Make sure config.py exists and is correct
from embedder import Embedder  # Make sure embedder.py exists and is correct

# Handle the import of Doc gracefully
try:
    from ingestion_service.src.models import Doc
except ImportError:
    Doc = None  # Fallback if Doc is not available

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Pydantic models
class EncodeOptions(BaseModel):
    normalize: Optional[bool] = None
    batch_size: Optional[int] = Field(None, ge=1, le=config.max_batch_size)

class EncodeRequest(BaseModel):
    texts: List[str] = Field(..., min_items=1, max_items=config.max_texts_per_request)
    model: str = config.default_model
    options: Optional[EncodeOptions] = None

class EncodeResponse(BaseModel):
    vectors: List[List[float]]
    model: str
    version: str
    shape: List[int]
    timing: Dict[str, float]

class HealthResponse(BaseModel):
    status: str
    model: str
    dimension: int
    pool_size: int

class ModelsResponse(BaseModel):
    supported_models: List[str]
    default_model: str

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", getattr(config, "kafka_bootstrap", "kafka:9092"))
KAFKA_RAW_CHUNKS = "raw-chunks"
KAFKA_EMBEDDINGS = "embeddings"

consumer: AIOKafkaConsumer = None
producer: AIOKafkaProducer = None
worker_task: asyncio.Task = None

# Prometheus metrics
REQUEST_COUNT = Counter(
    "http_requests_total", "Total HTTP requests", ["method", "endpoint", "status_code"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency", ["endpoint"]
)

ELASTICSEARCH_OPERATIONS_TOTAL = Counter(
    "elasticsearch_operations_total", "Elasticsearch operations", ["operation", "status"]
)
DOCUMENTS_INDEXED_TOTAL = Counter(
    "documents_indexed_total", "Total documents indexed"
)
ELASTICSEARCH_CONNECTION_STATUS = Gauge(
    "elasticsearch_connection_status", "Elasticsearch connection status (1=connected, 0=disconnected)"
)
FILE_UPLOADS_TOTAL = Counter(
    "file_uploads_total", "File uploads", ["file_type", "status"]
)
SEARCH_REQUESTS_TOTAL = Counter(
    "search_requests_total", "Search requests", ["grouped"]
)
ELASTICSEARCH_ERRORS_TOTAL = Counter(
    "elasticsearch_errors_total", "Elasticsearch errors", ["error_type", "operation"]
)
FAILED_REQUESTS_TOTAL = Counter(
    "failed_requests_total", "Total failed HTTP requests", ["method", "endpoint", "status_code"]
)
CACHE_HITS_TOTAL = Counter(
    "cache_hits_total", "Total cache hits"
)
CACHE_MISSES_TOTAL = Counter(
    "cache_misses_total", "Total cache misses"
)
ACTIVE_WORKERS = Gauge(
    "active_workers", "Number of active worker tasks"
)

def clean_and_split(text: str) -> list:
    """Remove boilerplate (HTML, scripts), normalize, and split into sentences."""
    soup = BeautifulSoup(text, "html.parser")
    for script in soup(["script", "style"]):
        script.decompose()
    cleaned = soup.get_text(separator=" ")
    cleaned = " ".join(cleaned.split())
    # Specify language to avoid punkt_tab lookup
    sentences = nltk.sent_tokenize(cleaned, language="english")
    return [s for s in sentences if s.strip()]

def decode_bytes(obj):
    """Recursively decode bytes to strings in dicts/lists."""
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    elif isinstance(obj, dict):
        return {decode_bytes(k): decode_bytes(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [decode_bytes(i) for i in obj]
    else:
        return obj

def ensure_str(obj):
    if isinstance(obj, bytes):
        return obj.decode("utf-8", "ignore")
    elif isinstance(obj, dict):
        return {ensure_str(k): ensure_str(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [ensure_str(i) for i in obj]
    else:
        return obj

async def embedding_kafka_worker():
    global consumer, producer
    batch = []
    batch_meta = []
    batch_size = getattr(config, "default_batch_size", 32)
    try:
        logger.info("Embedding Kafka worker started, consuming from 'raw-chunks'")
        while True:
            try:
                async for msg in consumer:
                    data = msg.value  # Already a dict due to value_deserializer
                    doc_id = data.get("doc_id")
                    chunk_id = data.get("chunk_id")
                    text = data.get("text", "")
                    metadata = data.get("metadata", {})
                    # Only use Doc if available
                    if Doc:
                        try:
                            doc_meta = Doc(**metadata)
                            metadata = doc_meta.dict()
                        except Exception:
                            pass

                    sentences = clean_and_split(text)
                    if not sentences:
                        continue
                    for s in sentences:
                        batch.append(s)
                        batch_meta.append({
                            "doc_id": doc_id,
                            "chunk_id": chunk_id,
                            "metadata": metadata
                        })
                    if len(batch) >= batch_size:
                        vectors, timing_info = embedder.encode(
                            texts=batch,
                            batch_size=batch_size,
                            normalize=True
                        )
                        for i, meta in enumerate(batch_meta):
                            out = {
                                "doc_id": meta["doc_id"],
                                "chunk_id": meta["chunk_id"],
                                "vector": vectors[i].tolist(),
                                "metadata": meta["metadata"]
                            }
                            out = ensure_str(out)
                            await producer.send_and_wait(KAFKA_EMBEDDINGS, out)
                        logger.info(f"Produced {len(batch)} embeddings to '{KAFKA_EMBEDDINGS}'")
                        batch = []
                        batch_meta = []
            except Exception as e:
                logger.error(f"Kafka worker error: {e}")
                await asyncio.sleep(5)
            # Process remaining batch
            if batch:
                vectors, timing_info = embedder.encode(
                    texts=batch,
                    batch_size=batch_size,
                    normalize=True
                )
                for i, meta in enumerate(batch_meta):
                    out = {
                        "doc_id": meta["doc_id"],
                        "chunk_id": meta["chunk_id"],
                        "vector": vectors[i].tolist(),
                        "metadata": meta["metadata"]
                    }
                    out = ensure_str(out)
                    await producer.send_and_wait(KAFKA_EMBEDDINGS, out)
                logger.info(f"Produced {len(batch)} embeddings to '{KAFKA_EMBEDDINGS}'")
                batch = []
                batch_meta = []
    finally:
        logger.info("Embedding Kafka worker stopped.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global consumer, producer, worker_task
    consumer = AIOKafkaConsumer(
        KAFKA_RAW_CHUNKS,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id="embedding-service",
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    await consumer.start()
    await producer.start()
    worker_task = asyncio.create_task(embedding_kafka_worker())
    try:
        yield
    finally:
        logger.info("Shutting down embedding Kafka worker and clients...")
        if worker_task:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
        await consumer.stop()
        await producer.stop()

# Initialize FastAPI app
app = FastAPI(
    title="QueryLens Embedding Service",
    description="High-performance text embedding service with model pooling",
    version="1.0.0",
    lifespan=lifespan
)

# Initialize embedder and Redis
embedder = Embedder(config.default_model)
try:
    redis_client = redis.Redis(
        host=config.redis_host,
        port=config.redis_port,
        db=config.redis_db,
        decode_responses=False  # Store as bytes for numpy
    )
    redis_client.ping()
    logger.info("Connected to Redis")
except Exception as e:
    logger.warning(f"Redis connection failed: {e}")
    redis_client = None

@app.middleware("http")
async def prometheus_metrics_middleware(request: Request, call_next):
    import time
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    endpoint = request.url.path
    REQUEST_COUNT.labels(request.method, endpoint, response.status_code).inc()
    REQUEST_LATENCY.labels(endpoint).observe(process_time)
    if response.status_code >= 400:
        FAILED_REQUESTS_TOTAL.labels(request.method, endpoint, response.status_code).inc()
    # Example: increment search_requests_total for /encode endpoint as a placeholder
    if endpoint == "/encode":
        SEARCH_REQUESTS_TOTAL.labels(grouped="false").inc()
    return response

@app.get("/metrics")
async def metrics():
    # Example: set elasticsearch_connection_status (simulate always connected)
    ELASTICSEARCH_CONNECTION_STATUS.set(1)
    # Set active_workers gauge (simulate 1 worker)
    ACTIVE_WORKERS.set(1 if worker_task and not worker_task.done() else 0)
    return JSONResponse(
        content=generate_latest().decode("utf-8"),
        media_type=CONTENT_TYPE_LATEST,
    )

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return HealthResponse(
        status="healthy",
        model=embedder.model_name,
        dimension=embedder.dim,
        pool_size=embedder.pool.size
    )

@app.post("/encode", response_model=EncodeResponse)
async def encode_texts(request: EncodeRequest):
    """
    Encode texts to vectors

    - **texts**: List of text strings to encode (1-1000 items)
    - **model**: Model name (currently only supports default)
    - **options**: Encoding options (normalize, batch_size)
    """
    try:
        model_name = request.model
        if model_name == "default":
            model_name = config.default_model

        # Validate model
        if model_name not in config.supported_models:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported model: {request.model}. "
                    f"Supported: {', '.join(config.supported_models.keys())}"
                )
            )

        # Extract options
        options = request.options or EncodeOptions()

        # Simulate cache check
        cache_key = f"emb:{hash(str(request.texts))}"
        cache_hit = False
        if redis_client:
            try:
                if redis_client.exists(cache_key):
                    CACHE_HITS_TOTAL.inc()
                    cache_hit = True
                else:
                    CACHE_MISSES_TOTAL.inc()
            except Exception as e:
                logger.warning(f"Redis cache check failed: {e}")

        # Encode texts
        encode_start = time.time()
        vectors, timing_info = embedder.encode(
            texts=request.texts,
            batch_size=options.batch_size,
            normalize=options.normalize
        )
        encode_time = time.time() - encode_start

        # Simulate Elasticsearch operation and document indexing
        ELASTICSEARCH_OPERATIONS_TOTAL.labels(operation="index", status="success").inc()
        DOCUMENTS_INDEXED_TOTAL.inc()

        # Store in Redis cache if available
        if redis_client:
            try:
                cache_key = f"emb:{hash(str(request.texts))}"
                # Store as bytes using numpy for efficiency
                redis_client.setex(cache_key, 3600, vectors.tobytes())  # 1 hour TTL
            except Exception as e:
                logger.warning(f"Redis cache failed: {e}")

        return EncodeResponse(
            vectors=vectors.tolist(),
            model=request.model,
            version="1.0.0",
            shape=list(vectors.shape),
            timing=timing_info
        )

    except HTTPException as e:
        # Simulate Elasticsearch error
        ELASTICSEARCH_ERRORS_TOTAL.labels(error_type="http_exception", operation="index").inc()
        # Re-raise HTTPExceptions so FastAPI handles them correctly
        raise
    except ValueError as e:
        ELASTICSEARCH_ERRORS_TOTAL.labels(error_type="value_error", operation="index").inc()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        ELASTICSEARCH_ERRORS_TOTAL.labels(error_type="internal_error", operation="index").inc()
        logger.error(f"Encoding failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/models", response_model=ModelsResponse)
async def list_models():
    return ModelsResponse(
        supported_models=list(config.supported_models.keys()),
        default_model=config.default_model
    )

# Download NLTK punkt if not present
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "embedding-service.main:app",
        host=config.api_host,
        port=config.api_port,
        workers=1,  # Single worker due to model pooling
        log_level="info"
    )
