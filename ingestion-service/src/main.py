import os
import sys
import time
import ssl
from typing import Optional, List
import magic
import textract
import re

from dotenv import load_dotenv
from elasticsearch import Elasticsearch, ConnectionError, TransportError, NotFoundError
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Depends, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.security import OAuth2PasswordRequestForm
from models import Doc
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from datetime import datetime
import logging

# Enhanced import path handling for auth module
def setup_auth_imports():
    """Setup import paths for auth module to work in both local and Docker environments"""
    current_file = os.path.abspath(__file__)
    current_dir = os.path.dirname(current_file)

    # Debug information
    print(f"Current file: {current_file}")
    print(f"Current directory: {current_dir}")
    print(f"Working directory: {os.getcwd()}")

    # List contents of current directory and parent
    try:
        current_contents = os.listdir(current_dir)
        print(f"Current directory contents: {current_contents}")
    except Exception as e:
        print(f"Cannot list current directory: {e}")

    try:
        parent_dir = os.path.dirname(current_dir)
        parent_contents = os.listdir(parent_dir)
        print(f"Parent directory: {parent_dir}")
        print(f"Parent directory contents: {parent_contents}")
    except Exception as e:
        print(f"Cannot list parent directory: {e}")

    # Try multiple possible locations for the auth module
    possible_auth_locations = [
        # Docker container structure: /app/auth (same level as main.py)
        os.path.join(current_dir, 'auth'),
        # Local development: parent of src directory
        os.path.join(os.path.dirname(current_dir), 'auth'),
        # Alternative: check if we're in a subdirectory
        os.path.join(os.path.dirname(current_dir), 'auth'),
    ]

    auth_path = None
    for path in possible_auth_locations:
        print(f"Checking auth path: {path}")
        if os.path.exists(path) and os.path.isdir(path):
            auth_path = path
            parent_path = os.path.dirname(path)
            if parent_path not in sys.path:
                sys.path.insert(0, parent_path)
            print(f"Found auth module at: {path}")
            print(f"Added to Python path: {parent_path}")
            return True

    # If auth module is not found, provide detailed error information
    print("=" * 50)
    print("AUTH MODULE NOT FOUND")
    print("=" * 50)
    print("This error indicates that the auth module is missing from the Docker container.")
    print("This typically happens when the Docker build process doesn't copy the auth directory.")
    print("")
    print("To fix this, ensure your Dockerfile includes:")
    print("COPY auth/ /app/auth/")
    print("or")
    print("COPY . /app/")
    print("")
    print("Searched locations:")
    for path in possible_auth_locations:
        print(f"  - {path} (exists: {os.path.exists(path)})")
    print("=" * 50)

    return False

# Setup auth imports
AUTH_ENABLED = setup_auth_imports()

# Import authentication modules with fallback
if AUTH_ENABLED:
    try:
        from auth.jwt_handler import (
            authenticate_user, create_token_pair, refresh_access_token, revoke_token,
            Token, User, UserCreate, RefreshTokenRequest, create_user,
            JWT_ACCESS_TOKEN_EXPIRE_MINUTES, fake_users_db
        )
        from auth.dependencies import get_current_active_user, get_current_admin_user, rate_limit_dependency
        from auth.security import SecurityHeadersMiddleware, log_security_event
        print("Successfully imported auth modules")
    except ImportError as e:
        print(f"Failed to import auth modules: {e}")
        AUTH_ENABLED = False

if not AUTH_ENABLED:
    print("Authentication disabled - auth module not found")

    # Define minimal auth classes for fallback
    class User:
        def __init__(self, username="anonymous", **kwargs):
            self.username = username
            self.disabled = False
            self.is_admin = False
            for key, value in kwargs.items():
                setattr(self, key, value)

    class Token:
        def __init__(self, access_token="", refresh_token="", token_type="bearer", expires_in=0, refresh_expires_in=0):
            self.access_token = access_token
            self.refresh_token = refresh_token
            self.token_type = token_type
            self.expires_in = expires_in
            self.refresh_expires_in = refresh_expires_in

    class UserCreate:
        def __init__(self, username="", email="", password="", **kwargs):
            self.username = username
            self.email = email
            self.password = password
            for key, value in kwargs.items():
                setattr(self, key, value)

    class RefreshTokenRequest:
        def __init__(self, refresh_token=""):
            self.refresh_token = refresh_token

    # Create a default user for E2E testing
    default_test_user = {
        "username": "testuser",
        "email": "test@example.com",
        "password": "testpass123",
        "disabled": False,
        "is_admin": False
    }

    # Fallback user database with default user
    fake_users_db = {
        "testuser": default_test_user
    }

    # Fallback auth functions
    def authenticate_user(username, password):
        """Authenticate user with hashed password support"""
        import hashlib

        if username not in fake_users_db:
            return None

        user_data = fake_users_db[username]

        # Check if password is hashed (contains salt)
        if "hashed_password" in user_data and ":" in user_data["hashed_password"]:
            stored_hash, salt = user_data["hashed_password"].split(":", 1)
            computed_hash = hashlib.sha256((password + salt).encode()).hexdigest()
            if computed_hash == stored_hash:
                return User(**user_data)
        # Fallback for plain text passwords (for existing test users)
        elif user_data.get("password") == password:
            return User(**user_data)

        return None

    def create_token_pair(user):
        # Return a dummy token when auth is disabled
        return Token(access_token="dummy-token", token_type="bearer", expires_in=3600)

    def refresh_access_token(refresh_token):
        raise HTTPException(status_code=503, detail="Authentication service unavailable")

    def revoke_token(token):
        pass

    def create_user(user_data):
        # Create a simple user for testing when auth is disabled
        if hasattr(user_data, 'username'):
            username = user_data.username
            email = getattr(user_data, 'email', '')
            password = getattr(user_data, 'password', '')
        else:
            username = user_data.get('username', 'anonymous')
            email = user_data.get('email', '')
            password = user_data.get('password', '')

        # Add user to fake database
        user_dict = {
            "username": username,
            "email": email,
            "password": password,
            "disabled": False,
            "is_admin": False
        }
        fake_users_db[username] = user_dict

        user = User(**user_dict)
        return user

    async def get_current_active_user():
        return User(username="anonymous")

    async def get_current_admin_user():
        return User(username="anonymous", is_admin=True)

    async def rate_limit_dependency(request: Request):
        """Fallback rate limiting - no actual limiting"""
        return True

    def log_security_event(event_type: str, user: Optional[str] = None,
                          request: Optional[Request] = None, details: Optional[dict] = None):
        """Fallback security logging"""
        logger.info(f"Security event (auth disabled): {event_type}")

    class SecurityHeadersMiddleware:
        """Fallback security headers middleware"""
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            await self.app(scope, receive, send)

    JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Import metrics
from metrics import (
    PrometheusMiddleware,
    record_document_indexed,
    record_document_chunks,
    record_search_request,
    record_file_upload,
    record_text_extraction,
    record_es_operation,
    record_es_error,
    update_es_connection_status
)

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ES_HOST = os.getenv("ELASTICSEARCH_HOST", "http://localhost:9200")
ES_USERNAME = os.getenv("ELASTICSEARCH_USERNAME", "elastic")
ES_PASSWORD = os.getenv("ELASTICSEARCH_PASSWORD", "changeme123!")
ES_VERIFY_CERTS = os.getenv("ELASTICSEARCH_VERIFY_CERTS", "true").lower() in ["true", "1"]
ES_CA_PATH = os.getenv("ELASTICSEARCH_CA_PATH", "/app/certs/ca/ca.crt")
ES_DISABLE_SSL_VERIFICATION = os.getenv("ELASTICSEARCH_DISABLE_SSL_VERIFICATION", "false").lower() in ["true", "1"]
INDEX = os.getenv("ELASTICSEARCH_INDEX", "docs")

APP_TITLE = os.getenv("APP_TITLE", "QueryLens Ingestion & Search")
CHUNK_MAX_TOKENS = int(os.getenv("CHUNK_MAX_TOKENS", "250"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))
CHUNK_THRESHOLD_WORDS = int(os.getenv("CHUNK_THRESHOLD_WORDS", "500"))


def create_ssl_context():
    """Create SSL context for Elasticsearch connection"""
    if not ES_HOST.startswith("https://"):
        logger.info("Using HTTP connection (no SSL)")
        return None

    context = ssl.create_default_context()

    if ES_DISABLE_SSL_VERIFICATION:
        logger.warning("SSL certificate verification is DISABLED")
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    if ES_VERIFY_CERTS:
        if os.path.exists(ES_CA_PATH):
            try:
                context.load_verify_locations(ES_CA_PATH)
                logger.info(f"Using CA certificate from: {ES_CA_PATH}")
            except ssl.SSLError as e:
                logger.error(f"Failed to load CA certificate: {e}")
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
        else:
            # CA file doesn't exist, try to load default certs
            try:
                context.load_default_certs()
                logger.info("Using system default CA certificates")
            except ssl.SSLError as e:
                logger.error(f"Failed to load default certificates: {e}")
                logger.warning("Falling back to disabled SSL verification")
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
    else:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    return context


# Enhanced Elasticsearch configuration
es_config = {
    "hosts": [ES_HOST],
    "request_timeout": 30,  # Updated from deprecated 'timeout'
    "max_retries": 3,
    "retry_on_timeout": True,
    "sniff_on_start": False,  # Disable sniffing for containerized environments
    "sniff_on_node_failure": False,  # Updated from deprecated 'sniff_on_connection_fail'
}

# Add authentication if credentials are provided
if ES_USERNAME and ES_PASSWORD:
    es_config["basic_auth"] = (ES_USERNAME, ES_PASSWORD)
    logger.info(f"Using basic authentication with user: {ES_USERNAME}")

# Add SSL configuration for HTTPS
if ES_HOST.startswith("https://"):
    ssl_context = create_ssl_context()
    if ssl_context:
        es_config["ssl_context"] = ssl_context

    # Set verify_certs based on our security configuration
    if ES_DISABLE_SSL_VERIFICATION:
        es_config["verify_certs"] = False
        logger.warning("SSL certificate verification disabled")
    else:
        es_config["verify_certs"] = ES_VERIFY_CERTS
        logger.info(f"SSL certificate verification: {'enabled' if ES_VERIFY_CERTS else 'disabled'}")

es = Elasticsearch(**es_config)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up application...")

    if not wait_for_es():
        logger.warning(f"Could not connect to Elasticsearch at {ES_HOST}")
        raise RuntimeError("Elasticsearch connection failed")
    else:
        create_index_mapping()
    logger.info("Application startup complete")

    yield

    # Shutdown
    logger.info("Shutting down application...")

app = FastAPI(title=APP_TITLE, lifespan=lifespan)

# Add security middleware
if AUTH_ENABLED:
    app.add_middleware(SecurityHeadersMiddleware)

# Add Prometheus middleware
app.add_middleware(PrometheusMiddleware)

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


# Enhanced retry decorator with metrics
@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type(ConnectionError),
    reraise=True
)
def es_operation_with_retry(operation, *args, **kwargs):
    """Execute Elasticsearch operation with retry logic and metrics"""
    operation_name = getattr(operation, '__name__', repr(operation))
    start_time = time.time()

    try:
        result = operation(*args, **kwargs)
        duration = time.time() - start_time
        record_es_operation(operation_name, duration, "success")
        return result

    except NotFoundError as e:
        # Don't retry on 404 errors - they're not transient
        duration = time.time() - start_time
        record_es_operation(operation_name, duration, "not_found")
        record_es_error("not_found", operation_name)
        raise e

    except TransportError as e:
        duration = time.time() - start_time
        record_es_operation(operation_name, duration, "error")
        record_es_error("transport_error", operation_name)
        raise e

    except ConnectionError as e:
        record_es_error("connection_error", operation_name)
        raise e

    except Exception as e:
        record_es_error("unknown_error", operation_name)
        logger.warning(f"ES operation failed, will retry: {str(e)}")
        raise


def wait_for_es(max_retries=30, delay=2):
    """Wait for Elasticsearch to be ready with enhanced error handling"""
    for i in range(max_retries):
        try:
            if es.ping():
                logger.info(f"Successfully connected to Elasticsearch at {ES_HOST}")
                update_es_connection_status(True)
                return True
        except Exception as e:
            error_msg = str(e)
            if "certificate verify failed" in error_msg or "TLS error" in error_msg:
                logger.error(f"SSL/TLS certificate verification failed: {error_msg}")
                logger.error("Consider setting ELASTICSEARCH_DISABLE_SSL_VERIFICATION=true for development")
                logger.error("Or provide a valid CA certificate path with ELASTICSEARCH_CA_PATH")
            else:
                logger.info(f"Attempt {i + 1}: Waiting for Elasticsearch... ({error_msg})")
            update_es_connection_status(False)
            time.sleep(delay)

    logger.error(f"Failed to connect to Elasticsearch after {max_retries} attempts")
    update_es_connection_status(False)
    return False


def extract_text_from_file(file_content: bytes, filename: str) -> tuple[str, str]:
    import tempfile
    """Extract text from a file using textract with metrics"""
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required")
    if not file_content:
        raise HTTPException(status_code=400, detail="File content is empty")

    start_time = time.time()
    file_type = "unknown"

    try:
        if filename.lower().endswith('.md'):
            file_type = "markdown"
            try:
                content = file_content.decode("utf-8")
                record_text_extraction(file_type, time.time() - start_time)
                return content, "text/markdown"
            except UnicodeDecodeError:
                raise HTTPException(status_code=400, detail="Invalid UTF-8 encoding in markdown file")

        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}") as temp_file:
            temp_file.write(file_content)
            temp_file.flush()

            try:
                detected_type = magic.from_file(temp_file.name, mime=True)
                file_type = detected_type.split('/')[0] if '/' in detected_type else detected_type
            except Exception:
                file_type = "unknown"

            try:
                content = textract.process(temp_file.name).decode("utf-8")
                content = content.strip()
            except Exception as e:
                raise HTTPException(status_code=422, detail=f"Unsupported file format or corrupted file: {str(e)}")
            finally:
                os.unlink(temp_file.name)

            if not content:
                raise HTTPException(status_code=422, detail="No text content could be extracted from the file")

            record_text_extraction(file_type, time.time() - start_time)
            return content, detected_type or "application/octet-stream"

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

@app.get("/metrics")
async def get_metrics():
    """Prometheus metrics endpoint"""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# Authentication endpoints
if AUTH_ENABLED:
    @app.post("/auth/token", response_model=Token)
    async def login_for_access_token(
        form_data: OAuth2PasswordRequestForm = Depends(),
        request: Request = Request,
        _: bool = Depends(rate_limit_dependency)
    ):
        """Login endpoint to get JWT token pair"""
        try:
            user = authenticate_user(form_data.username, form_data.password)
            if not user:
                log_security_event("login_failed", form_data.username, request)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Incorrect username or password",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            token_pair = create_token_pair(user)
            log_security_event("login_success", user.username, request)

            return token_pair
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Login error: {str(e)}")
            log_security_event("login_error", form_data.username, request, {"error": str(e)})
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Authentication service error"
            )

    @app.post("/auth/refresh", response_model=Token)
    async def refresh_token(
        refresh_request: RefreshTokenRequest,
        request: Request = Request
    ):
        """Refresh access token using refresh token"""
        try:
            new_token_pair = refresh_access_token(refresh_request.refresh_token)
            log_security_event("token_refresh", request=request)
            return new_token_pair
        except HTTPException:
            log_security_event("token_refresh_failed", request=request)
            raise
        except Exception as e:
            logger.error(f"Token refresh error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Token refresh failed"
            )

    @app.post("/auth/revoke")
    async def revoke_token_endpoint(
        current_user: User = Depends(get_current_active_user),
        request: Request = Request
    ):
        """Revoke current token"""
        try:
            # Get token from request
            auth_header = request.headers.get("authorization")
            if (auth_header and auth_header.startswith("Bearer ")):
                token = auth_header.split(" ")[1]
                revoke_token(token)
                log_security_event("token_revoked", current_user.username, request)
                return {"message": "Token revoked successfully"}
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No token provided"
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Token revoke error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Token revocation failed"
            )

    @app.post("/auth/register", response_model=User)
    async def register_user(
        user_data: UserCreate,
        request: Request = Request,
        _: bool = Depends(rate_limit_dependency)
    ):
        """Register a new user"""
        try:
            new_user = create_user(user_data)
            log_security_event("user_registered", new_user.username, request)
            return new_user
        except HTTPException:
            log_security_event("user_registration_failed", user_data.username, request)
            raise
        except Exception as e:
            logger.error(f"User registration error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="User registration failed"
            )

    @app.get("/auth/me", response_model=User)
    async def read_users_me(current_user: User = Depends(get_current_active_user)):
        """Get current user information"""
        return current_user

    @app.get("/auth/admin/users")
    async def list_users(
        current_user: User = Depends(get_current_admin_user),
        skip: int = 0,
        limit: int = 100
    ):
        """List all users (admin only)"""
        # In production, this would query the database
        users = []
        for user_data in list(fake_users_db.values())[skip:skip+limit]:
            user = User(**user_data)
            users.append(user)
        return {"users": users, "total": len(fake_users_db)}

else:
    # Add disabled auth endpoints that return appropriate errors
    @app.post("/auth/token")
    async def login_disabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is disabled"
        )

    @app.post("/auth/refresh")
    async def refresh_disabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is disabled"
        )

    @app.post("/auth/revoke")
    async def revoke_disabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is disabled"
        )

    @app.post("/auth/register")
    async def register_disabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is disabled"
        )

    @app.get("/auth/me")
    async def me_disabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is disabled"
        )

    @app.get("/auth/admin/users")
    async def list_users_disabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is disabled"
        )

@app.get("/")
def root():
    try:
        # Test both ping and authentication
        es_healthy = es.ping()
        if es_healthy:
            # Verify we can actually perform operations
            cluster_health = es.cluster.health()
            es_status = f"connected ({cluster_health['status']})"
        else:
            es_status = "disconnected"
        update_es_connection_status(es_healthy)
    except Exception as e:
        error_msg = str(e)
        if "certificate verify failed" in error_msg or "TLS error" in error_msg:
            logger.error(f"SSL/TLS certificate verification failed: {error_msg}")
            es_status = "ssl_error: certificate verification failed"
        else:
            logger.error(f"Elasticsearch connection error: {error_msg}")
            es_status = f"error: {error_msg}"
        update_es_connection_status(False)

    return {
        "status": "ok",
        "elasticsearch": ES_HOST,
        "elasticsearch_status": es_status,
        "security_enabled": ES_HOST.startswith("https://"),
        "cert_verification": ES_VERIFY_CERTS and not ES_DISABLE_SSL_VERIFICATION,
        "ssl_verification_disabled": ES_DISABLE_SSL_VERIFICATION
    }

@app.post("/docs/", status_code=201)
async def index_doc(
    doc: Doc,
    current_user: User = Depends(get_current_active_user),
    request: Request = Request
):
    """Index a document with structured data"""
    if not doc.id or not doc.id.strip():
        raise HTTPException(status_code=422, detail="Document ID is required and cannot be empty")

    if not doc.title or not doc.title.strip():
        raise HTTPException(status_code=422, detail="Document title is required and cannot be empty")

    if not doc.content or not doc.content.strip():
        raise HTTPException(status_code=422, detail="Document content is required and cannot be empty")

    try:
        resp = es_operation_with_retry(es.index, index=INDEX, id=doc.id, document=doc.model_dump())
        record_document_indexed("api")
        if AUTH_ENABLED:
            log_security_event("document_indexed", current_user.username, request, {"doc_id": doc.id})
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


@app.get("/docs/{doc_id}")
async def get_doc(doc_id: str, current_user: User = Depends(get_current_active_user)):
    if not doc_id or not doc_id.strip():
        raise HTTPException(status_code=400, detail="Document ID is required")

    try:
        resp = es_operation_with_retry(es.get, index=INDEX, id=doc_id)
        return resp["_source"]
    except NotFoundError:
        raise HTTPException(status_code=404, detail=f"Document with ID '{doc_id}' not found")
    except ConnectionError:
        raise HTTPException(status_code=503, detail="Elasticsearch service unavailable")
    except TransportError as e:
        logger.error(f"Elasticsearch transport error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve document")
    except Exception as e:
        logger.error(f"Failed to retrieve document: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve document")


@app.post("/docs/upload/")
async def upload_doc(
    file: UploadFile = File(...),
    doc_id: str = Form(...),
    title: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    author: Optional[str] = Form(None),
    source_system: Optional[str] = Form("ingestion-api"),
    enable_chunking: bool = Form(False),
    current_user: User = Depends(get_current_active_user),
    request: Request = Request
):
    """Upload and index a document from a file (PDF, Word, Markdown, etc.)"""
    # Input validation
    if not doc_id or not doc_id.strip():
        raise HTTPException(status_code=422, detail="Document ID is required and cannot be empty")

    if not file.filename:
        raise HTTPException(status_code=422, detail="File must have a filename")

    # File size validation (10MB limit)
    file_content = await file.read()
    file_size = len(file_content)

    if file_size > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File size exceeds 10MB limit")

    try:
        extracted_content, file_type = extract_text_from_file(file_content, file.filename)

        # Parse tags if provided
        tags_list = []
        if tags:
            try:
                tags_list = [tag.strip() for tag in tags.split(',') if tag.strip()]
            except Exception:
                tags_list = []

        base_metadata = {
            "file_type": file_type,
            "original_filename": file.filename,
            "author": author,
            "source_system": source_system,
            "file_size_bytes": file_size,
            "timestamp": datetime.now()
        }

        if enable_chunking and should_chunk_document(extracted_content):
            result = await index_chunked_document(
                doc_id=doc_id,
                title=title or file.filename,
                content=extracted_content,
                tags=tags_list,
                metadata=base_metadata
            )
            record_document_indexed("upload")
            record_document_chunks(result["chunks"])
            record_file_upload(file_type, file_size, "success")
            if AUTH_ENABLED:
                log_security_event("document_uploaded", current_user.username, request,
                                 {"doc_id": doc_id, "filename": file.filename, "chunked": True})
            return result
        else:
            doc = Doc(
                id=doc_id,
                title=title or file.filename,
                content=extracted_content,
                tags=tags_list,
                **base_metadata
            )

            response = es_operation_with_retry(es.index, index=INDEX, id=doc.id, document=doc.model_dump())
            record_document_indexed("upload")
            record_file_upload(file_type, file_size, "success")
            if AUTH_ENABLED:
                log_security_event("document_uploaded", current_user.username, request,
                                 {"doc_id": doc_id, "filename": file.filename, "chunked": False})

            return {
                "result": response["result"],
                "id": response["_id"],
                "file_type": file_type,
                "original_filename": file.filename,
                "file_size_bytes": file_size
            }

    except HTTPException:
        record_file_upload(file_type if 'file_type' in locals() else "unknown", file_size, "error")
        raise
    except ConnectionError:
        record_file_upload(file_type if 'file_type' in locals() else "unknown", file_size, "error")
        raise HTTPException(status_code=503, detail="Elasticsearch service unavailable")
    except Exception as e:
        record_file_upload(file_type if 'file_type' in locals() else "unknown", file_size, "error")
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

            response = es_operation_with_retry(es.index, index=INDEX, id=chunk_id, document=doc.model_dump())
            indexed_chunks.append(chunk_id)

        return {
            "result": "created",
            "id": doc_id,
            "chunks": len(indexed_chunks)
        }

    except Exception as e:
        logger.error(f"Failed to index chunked document: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to index document chunks")


@app.get("/search/")
async def search(
    q: str = Query(..., min_length=1, max_length=500, pattern=r"^[a-zA-Z0-9\s\-_.]+$"),
    size: int = Query(10, ge=1, le=100),
    group_chunks: bool = True,
    current_user: User = Depends(get_current_active_user),
    request: Request = Request
):
    """Search for documents"""
    if not q or not q.strip():
        raise HTTPException(status_code=422, detail="Search query is required")

    if size < 1 or size > 100:
        raise HTTPException(status_code=422, detail="Size must be between 1 and 100")

    try:
        query = {
            "query": {
                "multi_match": {
                    "query": q,
                    "fields": ["title^2", "content", "tags"],
                    "type": "best_fields"
                }
            },
            "size": size,
            "highlight": {
                "fields": {
                    "content": {},
                    "title": {}
                }
            }
        }

        resp = es_operation_with_retry(es.search, index=INDEX, body=query)
        hits = resp["hits"]["hits"]
        total_results = resp["hits"]["total"]["value"]

        # Group chunks if requested
        if group_chunks:
            results = group_chunk_results(hits, size)
        else:
            results = []
            for hit in hits:
                result = {
                    "id": hit["_id"],
                    "score": hit["_score"],
                    **hit["_source"]
                }
                if "highlight" in hit:
                    result["highlight"] = hit["highlight"]
                results.append(result)

        record_search_request(group_chunks, len(results))
        if AUTH_ENABLED:
            log_security_event("search_performed", current_user.username, request,
                             {"query": q, "results_count": len(results)})

        return {
            "total": total_results,
            "results": results
        }

    except ConnectionError:
        raise HTTPException(status_code=503, detail="Elasticsearch service unavailable")
    except Exception as e:
        logger.error(f"Search failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Search failed")


def group_chunk_results(hits: List[dict], limit: int) -> List[dict]:
    """Group chunk results by parent document"""
    grouped = {}

    for hit in hits:
        source = hit["_source"]
        parent_id = source.get("parent_document_id")

        # If parent_document_id is not set, try to extract from tags
        if not parent_id:
            tags = source.get("tags", [])
            for tag in tags:
                if (tag.startswith("parent:")):
                    parent_id = tag.split("parent:", 1)[1]
                    break

        if parent_id:
            # This is a chunk
            if parent_id not in grouped:
                grouped[parent_id] = {
                    "id": parent_id,
                    "title": source["title"].split(" (Part ")[0],  # Remove part indicator
                    "content": source["content"],
                    "tags": [tag for tag in source.get("tags", []) if
                             not tag.startswith("chunk") and not tag.startswith("parent:")],
                    "score": hit["_score"],
                    "chunks": 1,
                    "file_type": source.get("file_type"),
                    "original_filename": source.get("original_filename"),
                    "author": source.get("author"),
                    "source_system": source.get("source_system"),
                    "timestamp": source.get("timestamp")
                }
                if "highlight" in hit:
                    grouped[parent_id]["highlight"] = hit["highlight"]
            else:
                # For additional chunks of the same document, only update if score is higher
                if hit["_score"] > grouped[parent_id]["score"]:
                    grouped[parent_id]["score"] = hit["_score"]
                    if "highlight" in hit:
                        grouped[parent_id]["highlight"] = hit["highlight"]
                grouped[parent_id]["chunks"] += 1
        else:
            # This is a regular document
            result = {
                "id": hit["_id"],
                "score": hit["_score"],
                **source
            }
            if "highlight" in hit:
                result["highlight"] = hit["highlight"]
            grouped[hit["_id"]] = result

    # Sort by score and return top results
    results = sorted(grouped.values(), key=lambda x: x["score"], reverse=True)
    return results[:limit]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
