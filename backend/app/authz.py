"""Authorization: who is calling, and what are they allowed to do.

`app.security` answers *authentication* - is this token real, is this password
correct. This module answers the question that matters for machine callers:
*which principal* is this, and *what may it touch*.

The app's own auth is a single admin password that mints one JWT shape shared by
every route. That is fine for one human with one browser, and useless for an
agent, which needs three things the admin credential cannot give it:

  * traceability - so an action taken by an agent is distinguishable from one
    taken by the human, and revocable on its own;
  * least privilege - a monitoring agent should not be able to flatten the
    account, and a kill switch should never be the default grant;
  * the same endpoints - an agent integrating with this app should not have to
    learn a second, parallel API that drifts from the first.

So there is deliberately **no** second router. `get_current_principal` is the
single auth dependency, it accepts either an admin JWT or a scoped agent key,
and every route declares the scope it requires via `require_scope`. Credentials
management uses `require_human` and is unreachable by an agent key entirely.
"""

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AgentKey
from app.security import verify_token

# Key namespace. Checked before the JWT path so a key can never be mistaken for
# a token: a JWT is `header.payload.signature`, and no JWT header starts "oq_".
AGENT_KEY_PREFIX = "oq_"
# Characters of the key kept as the lookup handle. 8 chars of base64url is ~48
# bits, so a prefix collision is a non-event, but the comparison below is still
# constant-time over the full hash - the prefix only narrows the search.
_KEY_PREFIX_LEN = len(AGENT_KEY_PREFIX) + 8

# --- Scopes ----------------------------------------------------------------
# Flat strings, not nested resources. An agent integrating against this app
# should not have to reason about URL structure to reason about permission.
SCOPE_READ = "read"
SCOPE_CONFIG_WRITE = "config:write"
SCOPE_BOT_CONTROL = "bot:control"
SCOPE_BOT_KILL = "bot:kill"

ALL_SCOPES: tuple[str, ...] = (
    SCOPE_READ,
    SCOPE_CONFIG_WRITE,
    SCOPE_BOT_CONTROL,
    SCOPE_BOT_KILL,
)

# Read-only by default. Placing no orders is still an opt-in, but even
# `config:write` re-points the bot at a different strategy, which is how a
# "read-only" bot starts trading something else entirely. The kill switch is
# never granted implicitly under any code path.
DEFAULT_SCOPES: tuple[str, ...] = (SCOPE_READ,)

# The single source of truth for scope metadata, mirrored by the frontend so the
# checkbox list can never drift from what the backend actually enforces.
SCOPE_INFO: dict[str, dict[str, str]] = {
    SCOPE_READ: {
        "label": "Read account data",
        "description": "View account, positions, orders, trade logs and market clock.",
        "danger": "low",
    },
    SCOPE_CONFIG_WRITE: {
        "label": "Change strategy and schedule",
        "description": "Create, edit, activate or delete strategy profiles and change the bot schedule. This decides what the bot trades.",
        "danger": "medium",
    },
    SCOPE_BOT_CONTROL: {
        "label": "Start and stop the bot",
        "description": "Start, stop or pause the trading cycle. An agent holding this can also stop the bot indefinitely.",
        "danger": "medium",
    },
    SCOPE_BOT_KILL: {
        "label": "Kill switch",
        "description": "Cancel every open order and close every position immediately. Never granted by default.",
        "danger": "high",
    },
}

bearer = HTTPBearer()


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. `kind` decides what the request is allowed to do."""

    kind: str  # "user" | "agent"
    scopes: frozenset[str] = field(default_factory=frozenset)
    key_id: int | None = None
    label: str = "admin"

    @property
    def is_agent(self) -> bool:
        return self.kind == "agent"


ADMIN_PRINCIPAL = Principal(kind="user", scopes=frozenset(ALL_SCOPES), label="admin")


def generate_agent_key() -> tuple[str, str, str]:
    """Mint a new agent key.

    Returns ``(plaintext, key_prefix, key_hash)``. The plaintext is the only
    copy that ever exists - it is returned to the caller once at creation and
    never stored, so it cannot be shown again afterwards.
    """
    plaintext = f"{AGENT_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return plaintext, agent_key_prefix(plaintext), hash_agent_key(plaintext)


def hash_agent_key(key: str) -> str:
    """sha256 of the full key. The key is already 256 bits of entropy, so this
    needs no stretching - it exists to make a stolen database useless, not to
    make an online guess expensive."""
    return hashlib.sha256(key.encode()).hexdigest()


def agent_key_prefix(key: str) -> str:
    """The lookup handle for a key. Exposed so tests and tooling derive it the
    same way the lookup does, instead of hardcoding the length."""
    return key[:_KEY_PREFIX_LEN]


def _authenticate_agent_key(token: str, db: Session) -> Principal | None:
    """Resolve a bearer token to an agent principal, or None if it is not a
    live agent key. Revoked keys resolve to None, not to an error - the caller
    reports the single "invalid or revoked" message either way."""
    candidates = db.query(AgentKey).filter(AgentKey.key_prefix == agent_key_prefix(token)).all()
    for row in candidates:
        if row.revoked_at is not None:
            continue
        if secrets.compare_digest(row.key_hash, hash_agent_key(token)):
            row.last_used_at = datetime.utcnow()
            db.commit()
            return Principal(
                kind="agent",
                scopes=frozenset(row.scopes or []),
                key_id=row.id,
                label=row.label,
            )
    return None


def get_current_principal(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> Principal:
    if credentials.credentials.startswith(AGENT_KEY_PREFIX):
        principal = _authenticate_agent_key(credentials.credentials, db)
        if principal is None:
            raise HTTPException(401, "Invalid or revoked agent key")
        return principal
    try:
        verify_token(credentials.credentials)
    except JWTError:
        raise HTTPException(401, "Invalid token")
    return ADMIN_PRINCIPAL


def require_scope(scope: str):
    """Dependency factory: the caller must hold `scope`.

    The admin session implicitly holds every scope - it is the owner, and
    demoting it would just push the work onto the kill-switch escape hatch.
    """

    def _dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if scope not in principal.scopes:
            if principal.is_agent:
                raise HTTPException(
                    403,
                    f"Agent key '{principal.label}' lacks the '{scope}' scope",
                )
            raise HTTPException(403, f"Missing scope '{scope}'")
        return principal

    return _dependency


def require_human():
    """Dependency factory: rejects agent keys outright.

    Used for the Alpaca credentials endpoints, which can overwrite the broker
    key. Granting that to an agent would hand it the paper account itself, and
    a leaked agent key would silently become a leaked trading credential.
    """

    def _dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if principal.is_agent:
            raise HTTPException(403, "This endpoint requires the admin session, not an agent key")
        return principal

    return _dependency
