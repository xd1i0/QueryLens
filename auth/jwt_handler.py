import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
import jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field, validator
from fastapi import HTTPException, status
import logging
import hashlib
import time

logger = logging.getLogger(__name__)

# JWT Configuration with secure defaults
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not JWT_SECRET_KEY:
    logger.warning("JWT_SECRET_KEY not set in environment. Using random key (will invalidate tokens on restart)")
    JWT_SECRET_KEY = secrets.token_urlsafe(32)
elif len(JWT_SECRET_KEY) < 32:
    raise ValueError("JWT_SECRET_KEY must be at least 32 characters long")

JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "15"))  # Shorter for security
JWT_REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "30"))
JWT_ISSUER = os.getenv("JWT_ISSUER", "querylens-api")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "querylens-users")

# Password hashing with stronger settings
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12,  # Increased rounds for better security
)

# Token blacklist (in production, use Redis or database)
token_blacklist = set()

# Active sessions tracking
active_sessions = {}

# In-memory user store (in production, use a proper database)
fake_users_db = {
    "admin": {
        "username": "admin",
        "email": "admin@querylens.com",
        "hashed_password": pwd_context.hash("Admin123!@#"),  # Stronger default password
        "full_name": "System Administrator",
        "disabled": False,
        "is_admin": True,
        "created_at": datetime.now(timezone.utc),
        "last_login": None,
        "failed_login_attempts": 0,
        "locked_until": None,
    },
    "user": {
        "username": "user",
        "email": "user@querylens.com",
        "hashed_password": pwd_context.hash("User123!@#"),  # Stronger default password
        "full_name": "Regular User",
        "disabled": False,
        "is_admin": False,
        "created_at": datetime.now(timezone.utc),
        "last_login": None,
        "failed_login_attempts": 0,
        "locked_until": None,
    }
}

# Pydantic models
class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_expires_in: int

class TokenData(BaseModel):
    username: Optional[str] = None
    scopes: List[str] = []
    token_type: str = "access"

class User(BaseModel):
    username: str
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    disabled: Optional[bool] = None
    is_admin: Optional[bool] = False
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None

class UserInDB(User):
    hashed_password: str
    failed_login_attempts: int = 0
    locked_until: Optional[datetime] = None

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, pattern=r'^[a-zA-Z0-9_-]+$')
    email: EmailStr
    full_name: Optional[str] = Field(None, max_length=100)
    password: str = Field(..., min_length=8, max_length=128)

    @validator('password')
    def validate_password(cls, v):
        """Validate password strength"""
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
        if not any(c.isupper() for c in v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not any(c.islower() for c in v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not any(c.isdigit() for c in v):
            raise ValueError('Password must contain at least one digit')
        if not any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?' for c in v):
            raise ValueError('Password must contain at least one special character')
        return v

class RefreshTokenRequest(BaseModel):
    refresh_token: str

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """Hash a password"""
    return pwd_context.hash(password)

def get_user(username: str) -> Optional[UserInDB]:
    """Get user from database"""
    if username in fake_users_db:
        user_dict = fake_users_db[username]
        return UserInDB(**user_dict)
    return None

def is_account_locked(user: UserInDB) -> bool:
    """Check if account is locked due to failed login attempts"""
    if user.locked_until and user.locked_until > datetime.now(timezone.utc):
        return True
    return False

def record_failed_login(username: str):
    """Record failed login attempt and lock account if necessary"""
    if username in fake_users_db:
        user_data = fake_users_db[username]
        user_data["failed_login_attempts"] += 1
        
        # Lock account after 5 failed attempts for 30 minutes
        if user_data["failed_login_attempts"] >= 5:
            user_data["locked_until"] = datetime.now(timezone.utc) + timedelta(minutes=30)
            logger.warning(f"Account locked for user: {username}")

def reset_failed_login_attempts(username: str):
    """Reset failed login attempts on successful login"""
    if username in fake_users_db:
        fake_users_db[username]["failed_login_attempts"] = 0
        fake_users_db[username]["locked_until"] = None
        fake_users_db[username]["last_login"] = datetime.now(timezone.utc)

def authenticate_user(username: str, password: str) -> Optional[UserInDB]:
    """Authenticate user with username and password"""
    user = get_user(username)
    if not user:
        # Still hash the password to prevent timing attacks
        pwd_context.hash("dummy_password")
        return None
    
    if is_account_locked(user):
        logger.warning(f"Login attempt on locked account: {username}")
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Account is temporarily locked due to multiple failed login attempts"
        )
    
    if not verify_password(password, user.hashed_password):
        record_failed_login(username)
        logger.warning(f"Failed login attempt for user: {username}")
        return None
    
    reset_failed_login_attempts(username)
    logger.info(f"Successful login for user: {username}")
    return user

def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token with enhanced security"""
    to_encode = data.copy()
    
    # Set expiration
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    
    # Add standard JWT claims
    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "type": "access",
        "jti": secrets.token_hex(16),  # JWT ID for token tracking
    })
    
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt

def create_refresh_token(data: Dict[str, Any]) -> str:
    """Create JWT refresh token"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    
    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "type": "refresh",
        "jti": secrets.token_hex(16),
    })
    
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt

def verify_token(token: str, token_type: str = "access") -> TokenData:
    """Verify JWT token and return token data"""
    try:
        # Check if token is blacklisted
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        if token_hash in token_blacklist:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        payload = jwt.decode(
            token, 
            JWT_SECRET_KEY, 
            algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER
        )
        
        # Verify token type
        if payload.get("type") != token_type:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        scopes = payload.get("scopes", [])
        token_data = TokenData(username=username, scopes=scopes, token_type=token_type)
        return token_data
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

def create_user(user_data: UserCreate) -> User:
    """Create a new user"""
    if user_data.username in fake_users_db:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    
    # Check if email already exists
    for existing_user in fake_users_db.values():
        if existing_user.get("email") == user_data.email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )

    hashed_password = get_password_hash(user_data.password)
    user_dict = {
        "username": user_data.username,
        "email": user_data.email,
        "full_name": user_data.full_name,
        "hashed_password": hashed_password,
        "disabled": False,
        "is_admin": False,
        "created_at": datetime.now(timezone.utc),
        "last_login": None,
        "failed_login_attempts": 0,
        "locked_until": None,
    }

    fake_users_db[user_data.username] = user_dict
    logger.info(f"New user created: {user_data.username}")
    return User(**user_dict)

def get_current_user_from_token(token: str) -> User:
    """Get current user from JWT token"""
    token_data = verify_token(token)
    user = get_user(username=token_data.username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return User(**user.model_dump())

def get_current_active_user_from_token(token: str) -> User:
    """Get current active user from JWT token"""
    current_user = get_current_user_from_token(token)
    if current_user.disabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )
    return current_user

def refresh_access_token(refresh_token: str) -> Token:
    """Create new access token from refresh token"""
    token_data = verify_token(refresh_token, token_type="refresh")
    user = get_user(username=token_data.username)
    
    if user is None or user.disabled:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create new tokens
    access_token_expires = timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    scopes = ["read", "write"] if user.is_admin else ["read"]
    
    access_token = create_access_token(
        data={"sub": user.username, "scopes": scopes},
        expires_delta=access_token_expires
    )
    
    new_refresh_token = create_refresh_token(data={"sub": user.username})
    
    return Token(
        access_token=access_token,
        refresh_token=new_refresh_token,
        token_type="bearer",
        expires_in=JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        refresh_expires_in=JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
    )

def revoke_token(token: str):
    """Add token to blacklist"""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    token_blacklist.add(token_hash)
    logger.info("Token revoked")

def create_token_pair(user: UserInDB) -> Token:
    """Create access and refresh token pair"""
    access_token_expires = timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    scopes = ["read", "write"] if user.is_admin else ["read"]
    
    access_token = create_access_token(
        data={"sub": user.username, "scopes": scopes},
        expires_delta=access_token_expires
    )
    
    refresh_token = create_refresh_token(data={"sub": user.username})
    
    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        refresh_expires_in=JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
    )
