from cryptography.fernet import Fernet
import jwt
from datetime import datetime, timedelta, timezone
import bcrypt
from app.config import get_settings

settings = get_settings()
fernet = Fernet(settings.secret_encryption_key.encode())


def encrypt(plaintext: str) -> bytes:
    return fernet.encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    return fernet.decrypt(ciphertext).decode()


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())