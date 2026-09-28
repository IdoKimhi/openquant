from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import ApiCredentials
from app.schemas import CredentialsIn, CredentialsStatus, TestConnectionResponse
from app.security import encrypt, decrypt
from app.alpaca_client import AlpacaClient
from app.authz import require_human

router = APIRouter(prefix="/credentials", tags=["credentials"])


@router.post("")
def store_credentials(
    data: CredentialsIn,
    request: Request,
    principal=Depends(require_human()),
    db: Session = Depends(get_db),
):
    creds = db.query(ApiCredentials).first()
    if not creds:
        creds = ApiCredentials()
        db.add(creds)
    creds.key_id_encrypted = encrypt(data.key_id)
    creds.secret_key_encrypted = encrypt(data.secret_key)
    db.commit()
    # Deliberately no record_summary here. The action is logged by
    # method + path + status; adding detail would mean deciding what about a
    # broker key is safe to keep, and there is no version of that question with
    # a good answer. `test_agent_audit.py` pins that the material never lands
    # in the table.
    return {"status": "stored"}


@router.get("/status", response_model=CredentialsStatus)
def credentials_status(principal=Depends(require_human()), db: Session = Depends(get_db)):
    creds = db.query(ApiCredentials).first()
    return {"has_keys": creds is not None, "last_tested": creds.updated_at if creds else None}


@router.post("/test", response_model=TestConnectionResponse)
async def test_connection(principal=Depends(require_human()), db: Session = Depends(get_db)):
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