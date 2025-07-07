from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
import time
from collections import defaultdict
import logging

from .jwt_handler import get_current_active_user_from_token, User

logger = logging.getLogger(__name__)

# Rate limiting storage (in production, use Redis)
rate_limit_storage = defaultdict(list)

class BearerTokenAuth(HTTPBearer):
    """Custom Bearer token authentication with better error handling"""
    
    def __init__(self, auto_error: bool = True):
        super().__init__(auto_error=auto_error)
    
    async def __call__(self, request: Request) -> Optional[HTTPAuthorizationCredentials]:
        credentials = await super().__call__(request)
        
        if credentials:
            if credentials.scheme != "Bearer":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid authentication scheme",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            # Add request info to logs
            client_ip = request.client.host if request.client else "unknown"
            user_agent = request.headers.get("user-agent", "unknown")
            logger.info(f"Token authentication attempt from {client_ip} ({user_agent})")
            
            return credentials
        
        return None

bearer_auth = BearerTokenAuth()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_auth)
) -> User:
    """Get current user from Bearer token"""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return get_current_active_user_from_token(credentials.credentials)

async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """Get current active user"""
    if current_user.disabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )
    return current_user

async def get_current_admin_user(
    current_user: User = Depends(get_current_active_user)
) -> User:
    """Get current admin user"""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    return current_user

def rate_limit(max_requests: int = 100, window_seconds: int = 3600):
    """Rate limiting decorator"""
    def decorator(request: Request):
        client_ip = request.client.host if request.client else "unknown"
        current_time = time.time()
        
        # Clean old entries
        rate_limit_storage[client_ip] = [
            timestamp for timestamp in rate_limit_storage[client_ip]
            if current_time - timestamp < window_seconds
        ]
        
        # Check rate limit
        if len(rate_limit_storage[client_ip]) >= max_requests:
            logger.warning(f"Rate limit exceeded for IP: {client_ip}")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded"
            )
        
        # Add current request
        rate_limit_storage[client_ip].append(current_time)
        
        return True
    
    return decorator

async def rate_limit_dependency(request: Request):
    """Rate limiting dependency"""
    return rate_limit()(request)
