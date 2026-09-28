from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.audit import record_summary
from app.authz import (
    ALL_SCOPES,
    DEFAULT_SCOPES,
    SCOPE_INFO,
    generate_agent_key,
    rate_limiter,
    require_human,
)
from app.db import get_db
from app.models import AgentKey
from app.schemas import AgentKeyCreate, AgentKeyCreated, AgentKeyResponse, ScopeCatalogue

router = APIRouter(prefix="/agent-keys", tags=["agent-keys"])


@router.get("/scopes", response_model=ScopeCatalogue)
def list_scopes(principal=Depends(require_human())):
    """The scope catalogue. The frontend builds its checkboxes from this so the
    permissions it offers cannot drift from the ones actually enforced.

    The rate limits ride along: whoever is about to hand out a key is the
    person who needs to know what it will be able to do per minute, and an
    integrator finding out via a 429 is too late to design around it.
    """
    return {
        "scopes": [
            {"name": name, **SCOPE_INFO[name], "granted_by_default": name in DEFAULT_SCOPES}
            for name in ALL_SCOPES
        ],
        "rate_limits": {
            "requests_per_window": rate_limiter.read_limit + rate_limiter.write_limit,
            "window_seconds": int(rate_limiter.window),
            "read_limit": rate_limiter.read_limit,
            "write_limit": rate_limiter.write_limit,
        },
    }


@router.get("", response_model=list[AgentKeyResponse])
def list_agent_keys(principal=Depends(require_human()), db: Session = Depends(get_db)):
    return db.query(AgentKey).order_by(AgentKey.created_at.desc()).all()


@router.post("", response_model=AgentKeyCreated, status_code=201)
def create_agent_key(
    data: AgentKeyCreate,
    request: Request,
    principal=Depends(require_human()),
    db: Session = Depends(get_db),
):
    plaintext, prefix, key_hash = generate_agent_key()

    agent_key = AgentKey(
        label=data.label.strip(),
        key_prefix=prefix,
        key_hash=key_hash,
        scopes=data.scopes,
    )
    db.add(agent_key)
    db.commit()
    db.refresh(agent_key)

    # Granting access is the most security-relevant call in the app, so the
    # trail says who was given what. The key prefix is safe to record - it is
    # already rendered in the UI - and the plaintext deliberately is not.
    record_summary(
        request,
        key_id=agent_key.id,
        label=agent_key.label,
        key_prefix=prefix,
        scopes=data.scopes,
    )

    # `plaintext` is returned here and never again. Nothing in the database can
    # reproduce it, so the UI has to present it as one-time.
    return {**AgentKeyResponse.model_validate(agent_key).model_dump(), "key": plaintext}


@router.delete("/{key_id}")
def revoke_agent_key(
    key_id: int,
    request: Request,
    principal=Depends(require_human()),
    db: Session = Depends(get_db),
):
    """Revoke rather than delete, so a revoked key still shows up in the list
    with the label it was issued under. A revoked key authenticates to nothing
    and the row is left as evidence that it existed."""
    agent_key = db.query(AgentKey).filter(AgentKey.id == key_id).first()
    if not agent_key:
        raise HTTPException(404, "Agent key not found")
    if agent_key.revoked_at is None:
        agent_key.revoked_at = datetime.utcnow()
        db.commit()
        # Drop the throttle budget too. The key can no longer authenticate, so
        # the bucket is dead weight, and revoking is the natural moment to
        # release it.
        rate_limiter.reset(key_id=agent_key.id)
    record_summary(request, key_id=agent_key.id, label=agent_key.label)
    return {"status": "revoked", "id": key_id}
