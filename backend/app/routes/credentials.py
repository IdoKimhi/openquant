import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from alpaca.common.exceptions import APIError

from app.db import get_db
from app.models import ApiCredentials
from app.schemas import (
    CredentialsIn,
    CredentialsInfo,
    CredentialsStatus,
    TestConnectionResponse,
    TestCredentialsRequest,
)
from app.security import encrypt, decrypt
from app.alpaca_client import AlpacaClient
from app.authz import require_human

router = APIRouter(prefix="/credentials", tags=["credentials"])

logger = logging.getLogger(__name__)

# Statuses that mean "these keys are not usable", as opposed to "the broker
# could not be reached or would not talk to us right now".
#
# The distinction is the whole point of the message. The operator's correct
# response to a 401 is to go and re-copy the key; the correct response to a 429
# or a 503 is to wait. Reporting both as "invalid credentials" sends people to
# re-enter a key that was fine, which is how a working configuration gets
# replaced with a worse one - the exact harm issue #7 is about.
_AUTH_STATUSES = (401, 403)


def _broker_message(exc: APIError) -> str:
    """Alpaca's own error text, when it is JSON we can read.

    `APIError.code` does an unguarded `json.loads` and raises on anything that
    is not JSON - a proxy's HTML error page, an empty body - which would turn a
    broker problem into a 500. `str(exc)` is the raw body and is always safe, so
    that is the fallback.
    """
    body = getattr(exc, "_error", None)
    try:
        message = json.loads(body).get("message")
    except Exception:
        return str(body or exc).strip()
    return (message or str(body)).strip()


async def _probe(client: AlpacaClient) -> dict:
    """Ask the broker who we are, and describe the account if it answers.

    Raises HTTPException. The caller is a setup screen, so the broker's reason
    is the most useful thing we can return - "access key verification failed"
    and "secret key verification failed" are different problems with the same
    401, and neither is guessable from here.

    Catches `APIError` and not `Exception` on purpose. A bare except would also
    swallow a TypeError in our own code and report it to the operator as
    "invalid credentials", which is both a lie and the kind of bug that then
    gets "fixed" by entering a different key.
    """
    try:
        account = await client.get_account()
    except APIError as exc:
        status = exc.status_code
        reason = _broker_message(exc)
        if status in _AUTH_STATUSES:
            raise HTTPException(400, f"Alpaca rejected these credentials ({status}): {reason}")
        if status == 429:
            raise HTTPException(429, "Alpaca is rate limiting this key. Wait a moment and try again.")
        # `status_code` is None when the SDK was raised without an underlying
        # response, so it is not interpolated blindly - "reached Alpaca (None)"
        # reads like a bug report about this app.
        reached = f"Alpaca returned {status}" if status else "Alpaca did not respond"
        raise HTTPException(502, f"{reached}: {reason}. The keys were not tested.")
    except Exception:
        # A transport failure, a DNS error, a timeout. Not evidence about the
        # key, so it must not be reported as evidence against it.
        logger.exception("Credential probe failed before a broker response")
        raise HTTPException(502, "Could not reach Alpaca. The keys were not tested.")

    # Decimal -> float at the boundary, explicitly.
    #
    # Pydantic v2 lax mode does accept a Decimal for a float field, so this is
    # belt and braces rather than a fix for a 500. It is here because
    # `equity` is read by the frontend as a number, and the one place that has
    # to be true is where the broker's types are converted (gotcha 5b).
    return {
        "status": "connected",
        "equity": float(account.equity) if account.equity is not None else None,
        "buying_power": float(account.buying_power) if account.buying_power is not None else None,
        "account_status": str(account.status) if account.status is not None else None,
    }


@router.get("", response_model=CredentialsInfo)
def get_credentials(principal=Depends(require_human()), db: Session = Depends(get_db)):
    """Masked info about the stored key, for a rotation screen.

    Four characters of the key id and nothing else. There is no way to get the
    secret back out of this app - it is Fernet-encrypted with a key that is not
    in the database - so this is also the honest answer to "which account am I
    pointed at", which is the question a rotation actually starts with.
    """
    creds = db.query(ApiCredentials).first()
    if not creds:
        raise HTTPException(404, "No credentials stored")
    key_id = decrypt(creds.key_id_encrypted)
    return {
        "key_id_last4": key_id[-4:] if len(key_id) >= 4 else key_id,
        "created_at": creds.created_at,
    }


@router.delete("")
def delete_credentials(principal=Depends(require_human()), db: Session = Depends(get_db)):
    """Remove the stored keys, returning the app to its unconfigured state.

    A running bot survives this: the worker's `_get_alpaca_client` returns
    None with no credentials, and the cycle logs "Failed to create Alpaca
    client (no credentials)" and returns without trading. That is deliberate -
    quietly stopping the bot from under the operator would be a surprising side
    effect of a credential operation - but it does mean the bot goes quiet
    rather than loud, which the setup screen says out loud.
    """
    creds = db.query(ApiCredentials).first()
    if not creds:
        raise HTTPException(404, "No credentials stored")
    db.delete(creds)
    db.commit()
    return {"status": "deleted"}


@router.post("")
async def store_credentials(
    data: CredentialsIn,
    request: Request,
    principal=Depends(require_human()),
    db: Session = Depends(get_db),
):
    """Store Alpaca API credentials, replacing whatever is there.

    Validated against the broker *before* the overwrite, which is the whole
    point of issue #7. Overwriting first and validating afterwards means a typo
    in the secret key destroys a working configuration, and because the secret
    is not recoverable from this app the operator is left with a bot that
    cannot trade and a key they have to go and re-find. `_probe` raises before
    the write on any failure, so the stored row is only ever replaced by
    something that has just proven it works.

    Deliberately no record_summary here. The action is logged by
    method + path + status; adding detail would mean deciding what about a
    broker key is safe to keep, and there is no version of that question with a
    good answer. `test_agent_audit.py` pins that the material never lands
    in the table.
    """
    await _probe(AlpacaClient(data.key_id, data.secret_key))

    creds = db.query(ApiCredentials).first()
    if not creds:
        creds = ApiCredentials()
        db.add(creds)
    creds.key_id_encrypted = encrypt(data.key_id)
    creds.secret_key_encrypted = encrypt(data.secret_key)
    db.commit()
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
    return await _probe(AlpacaClient(decrypt(creds.key_id_encrypted), decrypt(creds.secret_key_encrypted)))


@router.post("/test-provided", response_model=TestConnectionResponse)
async def test_provided_credentials(
    data: TestCredentialsRequest,
    principal=Depends(require_human()),
):
    """Check credentials without storing them.

    The non-destructive half of a rotation, and the reason the destructive half
    is safe: an operator can paste a new paper account's keys here, read the
    equity back, and only then commit them with `POST /credentials`. Nothing
    this endpoint does touches the stored row.
    """
    return await _probe(AlpacaClient(data.key_id, data.secret_key))
