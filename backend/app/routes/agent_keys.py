from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.authz import ALL_SCOPES, DEFAULT_SCOPES, SCOPE_INFO, generate_agent_key, require_human
from app.db import get_db
from app.models import AgentKey
from app.schemas import AgentKeyCreate, AgentKeyCreated, AgentKeyResponse, ScopeCatalogue

router = APIRouter(prefix="/agent-keys", tags=["agent-keys"])


@router.get("/scopes", response_model=ScopeCatalogue)
def list_scopes(user=Depends(require_human())):
    """The scope catalogue. The frontend builds its checkboxes from this so the
    permissions it offers cannot drift from the ones actually enforced."""
    return {
        "scopes": [
            {"name": name, **SCOPE_INFO[name], "granted_by_default": name in DEFAULT_SCOPES}
            for name in ALL_SCOPES
        ]
    }


@router.get("", response_model=list[AgentKeyResponse])
def list_agent_keys(user=Depends(require_human()), db: Session = Depends(get_db)):
    return db.query(AgentKey).order_by(AgentKey.created_at.desc()).all()


@router.post("", response_model=AgentKeyCreated, status_code=201)
def create_agent_key(data: AgentKeyCreate, user=Depends(require_human()), db: Session = Depends(get_db)):
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

    # `plaintext` is returned here and never again. Nothing in the database can
    # reproduce it, so the UI has to present it as one-time.
    return {**AgentKeyResponse.model_validate(agent_key).model_dump(), "key": plaintext}


@router.delete("/{key_id}")
def revoke_agent_key(key_id: int, user=Depends(require_human()), db: Session = Depends(get_db)):
    """Revoke rather than delete, so a revoked key still shows up in the list
    with the label it was issued under. A revoked key authenticates to nothing
    and the row is left as evidence that it existed."""
    agent_key = db.query(AgentKey).filter(AgentKey.id == key_id).first()
    if not agent_key:
        raise HTTPException(404, "Agent key not found")
    if agent_key.revoked_at is None:
        agent_key.revoked_at = datetime.utcnow()
        db.commit()
    return {"status": "revoked", "id": key_id}
