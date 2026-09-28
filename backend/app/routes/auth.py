from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from jose import jwt, JWTError
from app.config import get_settings
from app.security import verify_token, verify_password, create_access_token
from app.db import get_db
from app.schemas import LoginRequest, TokenResponse, VerifyResponse

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer()
settings = get_settings()


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    if not verify_password(data.password, settings.app_password_hash):
        raise HTTPException(status_code=401, detail="Invalid password")
    token = create_access_token({"sub": "user"})
    return {"token": token}


@router.get("/verify", response_model=VerifyResponse)
def verify(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = verify_token(credentials.credentials)
        return {"valid": True, "user": payload["sub"]}
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


# Kept for any route that only ever serves the human. New code should use
# `app.authz.require_human()`, or `require_scope(...)` where an agent key is
# genuinely allowed. An agent key hitting this gets 403, not 401 - the token
# was valid, the principal just is not a human.
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        return verify_token(credentials.credentials)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")