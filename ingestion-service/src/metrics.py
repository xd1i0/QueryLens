import time
from typing import Optional
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
import logging

logger = logging.getLogger(__name__)

# Request metrics
REQUEST_COUNT = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status_code']
)

REQUEST_DURATION = Histogram(
    'http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['method', 'endpoint'],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

# Application metrics
DOCUMENTS_INDEXED = Counter(
    'documents_indexed_total',
    'Total documents indexed',
    ['source_type']
)

DOCUMENT_CHUNKS_CREATED = Counter(
    'document_chunks_created_total',
    'Total document chunks created'
)

SEARCH_REQUESTS = Counter(
    'search_requests_total',
    'Total search requests',
    ['grouped']
)

SEARCH_RESULTS = Histogram(
    'search_results_count',
    'Number of search results returned',
    buckets=[0, 1, 5, 10, 25, 50, 100]
)

# Elasticsearch metrics
ES_OPERATIONS = Counter(
    'elasticsearch_operations_total',
    'Total Elasticsearch operations',
    ['operation', 'status']
)

ES_OPERATION_DURATION = Histogram(
    'elasticsearch_operation_duration_seconds',
    'Elasticsearch operation duration in seconds',
    ['operation'],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

ES_ERRORS = Counter(
    'elasticsearch_errors_total',
    'Total Elasticsearch errors',
    ['error_type', 'operation']
)

ES_CONNECTION_STATUS = Gauge(
    'elasticsearch_connection_status',
    'Elasticsearch connection status (1=connected, 0=disconnected)'
)

# File upload metrics
FILE_UPLOADS = Counter(
    'file_uploads_total',
    'Total file uploads',
    ['file_type', 'status']
)

FILE_UPLOAD_SIZE = Histogram(
    'file_upload_size_bytes',
    'File upload size in bytes',
    buckets=[1024, 10240, 102400, 1048576, 10485760]
)

TEXT_EXTRACTION_DURATION = Histogram(
    'text_extraction_duration_seconds',
    'Text extraction duration in seconds',
    ['file_type'],
    buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0]
)

class PrometheusMiddleware(BaseHTTPMiddleware):
    """Middleware to collect HTTP request metrics"""

    async def dispatch(self, request: Request, call_next):
        # Skip metrics endpoint to avoid recursion
        if request.url.path == "/metrics":
            return await call_next(request)

        start_time = time.time()
        endpoint = self._get_endpoint_pattern(request)
        method = request.method

        try:
            response = await call_next(request)
            duration = time.time() - start_time

            REQUEST_COUNT.labels(
                method=method,
                endpoint=endpoint,
                status_code=response.status_code
            ).inc()

            REQUEST_DURATION.labels(
                method=method,
                endpoint=endpoint
            ).observe(duration)

            return response

        except Exception as e:
            duration = time.time() - start_time
            REQUEST_COUNT.labels(
                method=method,
                endpoint=endpoint,
                status_code=500
            ).inc()

            REQUEST_DURATION.labels(
                method=method,
                endpoint=endpoint
            ).observe(duration)

            raise e

    def _get_endpoint_pattern(self, request: Request) -> str:
        """Extract endpoint pattern from request path"""
        path = request.url.path

        if path.startswith("/docs/") and len(path.split("/")) == 3:
            return "/docs/{doc_id}"
        elif path == "/docs/upload/":
            return "/docs/upload/"
        elif path == "/search/":
            return "/search/"
        elif path == "/":
            return "/"
        elif path == "/metrics":
            return "/metrics"
        else:
            return path

def record_document_indexed(source_type: str):
    """Record when a document is indexed"""
    DOCUMENTS_INDEXED.labels(source_type=source_type).inc()

def record_document_chunks(chunk_count: int):
    """Record document chunks created"""
    DOCUMENT_CHUNKS_CREATED.inc(chunk_count)

def record_search_request(grouped: bool, result_count: int):
    """Record search request metrics"""
    SEARCH_REQUESTS.labels(grouped=str(grouped).lower()).inc()
    SEARCH_RESULTS.observe(result_count)

def record_file_upload(file_type: str, file_size: int, status: str):
    """Record file upload metrics"""
    FILE_UPLOADS.labels(file_type=file_type, status=status).inc()
    if status == "success":
        FILE_UPLOAD_SIZE.observe(file_size)

def record_text_extraction(file_type: str, duration: float):
    """Record text extraction metrics"""
    TEXT_EXTRACTION_DURATION.labels(file_type=file_type).observe(duration)

def record_es_operation(operation: str, duration: float, status: str):
    """Record Elasticsearch operation metrics"""
    ES_OPERATIONS.labels(operation=operation, status=status).inc()
    if status == "success":
        ES_OPERATION_DURATION.labels(operation=operation).observe(duration)

def record_es_error(error_type: str, operation: str):
    """Record Elasticsearch error"""
    ES_ERRORS.labels(error_type=error_type, operation=operation).inc()

def update_es_connection_status(connected: bool):
    """Update Elasticsearch connection status"""
    ES_CONNECTION_STATUS.set(1 if connected else 0)