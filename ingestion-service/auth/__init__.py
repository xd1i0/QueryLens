"""
Authentication module for QueryLens Ingestion Service.

This module provides JWT-based authentication functionality including:
- User authentication and authorization
- JWT token creation and validation
- Password hashing and verification
- FastAPI dependencies for protected routes
"""

from .jwt_handler import (
    authenticate_user,
    create_access_token,
    create_user,
    Token,
    User,
    UserCreate,
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES
)
from .dependencies import get_current_active_user, get_current_user

__all__ = [
    "authenticate_user",
    "create_access_token",
    "create_user",
    "Token",
    "User",
    "UserCreate",
    "JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
    "get_current_active_user",
    "get_current_user"
]
