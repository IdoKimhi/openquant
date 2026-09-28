from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import ApiCredentials
from app.schemas import CredentialsIn, CredentialsStatus, TestConnectionResponse
from app.security import encrypt, decrypt
from app.alpaca_client import AlpacaClient
from app.authz import require_human

router = APIRouter(prefix="/credentials", tags=["credentials"])


@router.post("")
def store_credentials(data: CredentialsIn, user=Depends(require_human()), db: Session = Depends(get_db)):
    creds = db.query(ApiCredentials).first()
    if not creds:
        creds = ApiCredentials()
        db.add(creds)
    creds.key_id_encrypted = encrypt(data.key_id)
    creds.secret_key_encrypted = encrypt(data.secret_key)
    db.commit()
    return {"status": "stored"}


@router.get("/status", response_model=CredentialsStatus)
def credentials_status(user=Depends(require_human()), db: Session = Depends(get_db)):
    creds = db.query(ApiCredentials).first()
    return {"has_keys": creds is not None, "last_tested": creds.updated_at if creds else None}


@router.post("/test", response_model=TestConnectionResponse)
async def test_connection(user=Depends(require_human()), db: Session = Depends(get_db)):
    creds = db.query(ApiCredentials).first()
    if not creds:
        raise HTTPException(400, "No credentials stored")
    client = AlpacaClient(decrypt(creds.key_id_encrypted), decrypt(creds.secret_key_encrypted))
    try:
        account = await client.get_account()
        return {
            "status": "connected",
            "equity": account.equity,
            "buying_power": account.buying_power,
            "account_status": account.status
        }
    except Exception as e:
        raise HTTPException(400, f"Connection failed: {e}")