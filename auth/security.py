import logging
import hashlib
import time
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Security event logging
security_logger = logging.getLogger("security")
security_logger.setLevel(logging.INFO)

# Create a separate handler for security events
security_handler = logging.StreamHandler()
security_handler.setFormatter(
    logging.Formatter(
        fmt="%(asctime)s - SECURITY - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
)
security_logger.addHandler(security_handler)


def log_security_event(
        event_type: str,
        user: Optional[str] = None,
        request: Optional[Request] = None,
        details: Optional[Dict[str, Any]] = None
):
    """Log security-related events for monitoring and auditing"""
    try:
        # Build the security event data
        event_data = {
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user": user or "anonymous",
        }

        # Add request details if available
        if request:
            event_data.update({
                "client_ip": getattr(request.client, 'host', 'unknown') if request.client else 'unknown',
                "user_agent": request.headers.get("user-agent", "unknown"),
                "method": request.method,
                "path": str(request.url.path),
                "query_params": str(request.query_params) if request.query_params else None,
            })

        # Add additional details
        if details:
            event_data["details"] = details

        # Log the event
        security_logger.info(f"Security Event: {event_data}")

    except Exception as e:
        logger.error(f"Failed to log security event: {e}")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to add security headers to all responses"""

    def __init__(self, app):
        super().__init__(app)
        self.security_headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'",
            "Permissions-Policy": "geolocation=(), microphone=(), camera=()"
        }

    async def dispatch(self, request: Request, call_next):
        """Add security headers to response"""
        try:
            response = await call_next(request)

            # Add security headers
            for header, value in self.security_headers.items():
                response.headers[header] = value

            # Log security-sensitive requests
            if self._is_security_sensitive_request(request):
                log_security_event(
                    "security_sensitive_request",
                    request=request,
                    details={"endpoint": request.url.path}
                )

            return response

        except Exception as e:
            logger.error(f"Security middleware error: {e}")
            # Return the response even if security headers fail
            return await call_next(request)

    def _is_security_sensitive_request(self, request: Request) -> bool:
        """Check if request is security-sensitive"""
        sensitive_paths = [
            "/auth/",
            "/admin/",
            "/docs/upload/",
            "/docs/",
        ]

        return any(request.url.path.startswith(path) for path in sensitive_paths)


class SecurityMonitor:
    """Monitor for security threats and suspicious activities"""

    def __init__(self):
        self.failed_attempts: Dict[str, list] = {}
        self.rate_limits: Dict[str, list] = {}
        self.suspicious_ips: set = set()

    def record_failed_attempt(self, identifier: str, attempt_type: str = "login"):
        """Record a failed authentication attempt"""
        current_time = time.time()

        if identifier not in self.failed_attempts:
            self.failed_attempts[identifier] = []

        self.failed_attempts[identifier].append({
            "timestamp": current_time,
            "type": attempt_type
        })

        # Clean old attempts (older than 1 hour)
        self.failed_attempts[identifier] = [
            attempt for attempt in self.failed_attempts[identifier]
            if current_time - attempt["timestamp"] < 3600
        ]

        # Check if IP should be flagged as suspicious
        if len(self.failed_attempts[identifier]) >= 5:
            self.suspicious_ips.add(identifier)
            log_security_event(
                "suspicious_activity_detected",
                details={
                    "identifier": identifier,
                    "failed_attempts": len(self.failed_attempts[identifier]),
                    "attempt_type": attempt_type
                }
            )

    def is_suspicious_ip(self, ip: str) -> bool:
        """Check if IP is flagged as suspicious"""
        return ip in self.suspicious_ips

    def check_rate_limit(self, identifier: str, limit: int = 100, window: int = 3600) -> bool:
        """Check if identifier exceeds rate limit"""
        current_time = time.time()

        if identifier not in self.rate_limits:
            self.rate_limits[identifier] = []

        # Clean old requests
        self.rate_limits[identifier] = [
            timestamp for timestamp in self.rate_limits[identifier]
            if current_time - timestamp < window
        ]

        # Check if limit exceeded
        if len(self.rate_limits[identifier]) >= limit:
            return False

        # Record this request
        self.rate_limits[identifier].append(current_time)
        return True


# Global security monitor instance
security_monitor = SecurityMonitor()


def validate_request_security(request: Request) -> bool:
    """Validate request for security threats"""
    try:
        client_ip = getattr(request.client, 'host', 'unknown') if request.client else 'unknown'

        # Check for suspicious IP
        if security_monitor.is_suspicious_ip(client_ip):
            log_security_event(
                "request_from_suspicious_ip",
                request=request,
                details={"client_ip": client_ip}
            )
            return False

        # Check rate limiting
        if not security_monitor.check_rate_limit(client_ip):
            log_security_event(
                "rate_limit_exceeded",
                request=request,
                details={"client_ip": client_ip}
            )
            return False

        return True

    except Exception as e:
        logger.error(f"Security validation error: {e}")
        return True  # Allow request if validation fails


def hash_token(token: str) -> str:
    """Hash a token for secure storage"""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_security_report() -> Dict[str, Any]:
    """Generate a security report with current statistics"""
    return {
        "failed_attempts": len(security_monitor.failed_attempts),
        "suspicious_ips": len(security_monitor.suspicious_ips),
        "rate_limited_ips": len(security_monitor.rate_limits),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


class SecurityHTTPBearer(HTTPBearer):
    """Enhanced HTTP Bearer authentication with security monitoring"""

    def __init__(self, auto_error: bool = True):
        super().__init__(auto_error=auto_error)

    async def __call__(self, request: Request) -> Optional[HTTPAuthorizationCredentials]:
        """Validate bearer token with security checks"""
        try:
            # Validate request security first
            if not validate_request_security(request):
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests or suspicious activity detected"
                )

            # Get the credentials
            credentials = await super().__call__(request)

            if credentials:
                # Log successful token usage
                log_security_event(
                    "token_used",
                    request=request,
                    details={"token_scheme": credentials.scheme}
                )

            return credentials

        except HTTPException:
            # Log failed authentication attempt
            client_ip = getattr(request.client, 'host', 'unknown') if request.client else 'unknown'
            security_monitor.record_failed_attempt(client_ip, "bearer_token")
            raise
        except Exception as e:
            logger.error(f"Security bearer authentication error: {e}")
            raise HTTPException(status_code=500, detail="Authentication error")