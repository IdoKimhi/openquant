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
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import AgentKey
from app.security import verify_token

# Key namespace. Checked before the JWT path so a key can never be mistaken for
# a token: a JWT is `header.payload.signature`, and no JWT header starts "oq_".
AGENT_KEY_PREFIX = "oq_"
LAST_USED_WRITE_INTERVAL = timedelta(seconds=60)
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

# --- Rate limiting ---------------------------------------------------------
# An agent key is a long-lived bearer token with no other brake. `bot:control`
# lets a holder stop and start the bot as fast as the request round-trip
# allows, and a leaked `read` key can poll the dashboard endpoints forever.
# Neither shows up as a bug - both look exactly like a working API.
RATE_LIMIT_WINDOW = 60.0

# Methods that only read. OPTIONS matters: it is how a browser preflights, and
# counting it would charge the budget for a request that never reached a route.
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class RateLimiter:
    """Fixed-window request counter per agent key, in process memory.

    Fixed window rather than sliding because what matters here is the burst
    allowance, and a sliding window costs a timestamp per request. Windows are
    aligned to the clock, not started on first use: a client that straddles two
    of its own windows would otherwise get up to twice the limit, and an
    aligned window has no such edge.

    State is per process. That is exact for the single-replica deployment here
    and is *not* correct behind a load balancer with more than one backend -
    if that ever happens this needs to move to the shared SQLite volume or a
    real cache, not gain a second in-process copy.
    """

    def __init__(self, read_limit: int, write_limit: int, window: float = RATE_LIMIT_WINDOW, now=time.monotonic):
        self.read_limit = read_limit
        self.write_limit = write_limit
        self.window = window
        self._now = now
        # (key_id, "read"|"write") -> (window index, count)
        self._buckets: dict[tuple[int, str], tuple[int, int]] = {}

    @property
    def active_key_count(self) -> int:
        """Live buckets. Exposed because an unbounded map is a real leak here -
        see the pruning in `check`."""
        return len(self._buckets)

    def check(self, key_id: int, *, mutating: bool) -> tuple[bool, int, int]:
        """Charge one request against `key_id`. Returns (allowed, limit, retry_after)."""
        bucket = "write" if mutating else "read"
        limit = self.write_limit if mutating else self.read_limit

        now = self._now()
        window_id = int(now // self.window)
        self._prune(window_id)

        # The `seen_id != window_id` test is redundant given the prune above -
        # every surviving entry is already in this window. It stays because it
        # makes `check` correct on its own, and a future edit that moves the
        # prune below the read should not silently turn that into a bug.
        seen_id, count = self._buckets.get((key_id, bucket), (window_id, 0))
        if seen_id != window_id:
            count = 0

        if count >= limit:
            retry_after = max(1, round((window_id + 1) * self.window - now))
            return False, limit, int(retry_after)

        self._buckets[(key_id, bucket)] = (window_id, count + 1)
        return True, limit, 0

    def _prune(self, current_window: int) -> None:
        """Drop windows that can no longer be charged. Without this, a
        long-lived process accumulates one dead bucket per key per window and
        never releases them."""
        stale = [k for k, (window_id, _) in self._buckets.items() if window_id != current_window]
        for key in stale:
            del self._buckets[key]

    def reset(self, key_id: int | None = None) -> None:
        """Clear one key's budget, or all of them. Used by revocation and by
        the test suite, which reuses row ids after recreating the tables."""
        if key_id is None:
            self._buckets.clear()
            return
        for key in [k for k in self._buckets if k[0] == key_id]:
            del self._buckets[key]


_settings = get_settings()
rate_limiter = RateLimiter(
    read_limit=_settings.agent_read_rate_limit,
    write_limit=_settings.agent_write_rate_limit,
)


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
    reports the single "invalid or revoked" message either way.

    `last_used_at` is stamped here, at most once per `LAST_USED_WRITE_INTERVAL`,
    and that throttle is load-bearing rather than an optimisation. Stamping on
    every request made every *read* a write, and because authentication happens
    before the rate limit is charged, even a 429 took the write lock. At the 120
    reads/min the limiter allows, that serialised on SQLite and surfaced as
    `GET /profiles` returning 500 `database is locked`.
    """
    candidates = db.query(AgentKey).filter(AgentKey.key_prefix == agent_key_prefix(token)).all()
    for row in candidates:
        if row.revoked_at is not None:
            continue
        if secrets.compare_digest(row.key_hash, hash_agent_key(token)):
            now = datetime.utcnow()
            stale = row.last_used_at is None or (now - row.last_used_at) >= LAST_USED_WRITE_INTERVAL
            if stale:
                row.last_used_at = now
                db.commit()
            return Principal(
                kind="agent",
                scopes=frozenset(row.scopes or []),
                key_id=row.id,
                label=row.label,
            )
    return None


def get_current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> Principal:
    """Resolve the caller and, for agent keys, charge their rate-limit budget.

    Two things hang off this function that are easy to miss:

    - The resolved principal is stashed on `request.state`, which is how the
      audit middleware knows *who* acted without re-parsing the token. It has
      to happen here, and it only happens on success - a refused caller leaves
      no principal behind and is recorded as anonymous, which is the honest
      description of an attempt that never authenticated.
    - The rate limit is charged *after* authentication, keyed on the resolved
      key id. Keying on the raw token or the prefix instead would let anyone
      who knows a prefix starve the real key by guessing under it, turning a
      read-only limit into a denial of service against the human's bot.
    """
    if credentials.credentials.startswith(AGENT_KEY_PREFIX):
        principal = _authenticate_agent_key(credentials.credentials, db)
        if principal is None:
            raise HTTPException(401, "Invalid or revoked agent key")

        allowed, limit, retry_after = rate_limiter.check(
            principal.key_id, mutating=request.method not in _READ_METHODS
        )
        if not allowed:
            # 429, not 403. A 403 with no detail already means "no
            # Authorization header was sent" and the axios interceptor must be
            # able to tell the two apart.
            raise HTTPException(
                429,
                f"Rate limit exceeded for agent key '{principal.label}' "
                f"({limit} requests per {int(rate_limiter.window)}s). "
                f"Retry in {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )
        request.state.principal = principal
        return principal

    try:
        verify_token(credentials.credentials)
    except JWTError:
        raise HTTPException(401, "Invalid token")
    request.state.principal = ADMIN_PRINCIPAL
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
