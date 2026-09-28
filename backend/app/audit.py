"""Audit trail for state-changing calls, whoever makes them.

An agent key is a long-lived credential. When something moves - the bot
starts, a profile is activated, the kill switch fires - the question that has
to be answerable afterwards is "was that the human or an agent, and which one".
`AgentKey.last_used_at` cannot answer it: it records only that a key was
presented, and it stops recording the moment the key is revoked, which is
precisely when the question gets asked.

Three design decisions, all of which are the kind that quietly go wrong:

**Reads are not recorded.** The dashboard polls on an interval. Auditing GETs
buries the actions that matter under a stream of noise and grows the table
forever, which makes the trail the first thing anyone turns off.

**Request bodies are not captured.** The tempting version of this middleware
logs what was sent, and that is how an Alpaca secret key ends up sitting in
plaintext in a table that any `read` key can fetch back. `detail` is written
only by a route that opts in through `record_summary`, so what lands here is
always something a human reviewed and decided was safe to keep.

**A failed audit write never fails the request.** By the time the entry is
written, the action has already been applied. A locked SQLite file must not be
able to turn a completed trade into an error response - that is the same
confusion as gotcha 11, where a logging bug reads as a trading bug.

One consequence of auditing by method rather than by an allowlist:
`POST /auth/login` is recorded, and a *successful* login shows as `anonymous`,
because the request that mints the token is by definition made by nobody who
is authenticated yet. That is the honest reading, and it means repeated failed
logins show up in the trail, which is where they are worth showing up.

The principal comes from `request.state`, which `get_current_principal` sets on
success. Starlette shares one `scope` dict across the middleware and the
endpoint, so a write to `request.state` inside a dependency is visible here
afterwards. It is *not* a contextvar, which would not survive the separate task
`BaseHTTPMiddleware` runs the endpoint in.
"""

import json
import logging

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.db import get_session_local
from app.models import AuditLog

logger = logging.getLogger(__name__)

AUDITED_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})


def record_summary(request: Request, **summary) -> None:
    """Attach what this call actually changed, for the audit entry.

    Opt-in, per route, and the only way a value reaches `AuditLog.detail`.
    Prefer the resulting state over the request: an audit reader wants to know
    the schedule is now `0 10 * * MON-FRI`, not that someone POSTed a body.

        record_summary(request, active_profile_id=profile.id)
    """
    request.state.audit_summary = summary


def _write_entry(request: Request, status_code: int) -> None:
    """Best-effort. Never raises - see the module docstring.

    A consequence worth naming: under SQLite contention the insert can be lost
    silently. That is the deliberate trade. The alternative - propagating the
    failure - means a busy database turns a completed trade into a 500, and
    the caller cannot tell "your order did not go through" from "we could not
    write a log line". A gap in the trail is recoverable; a lie about an order
    is not.
    """
    try:
        principal = getattr(request.state, "principal", None)
        summary = getattr(request.state, "audit_summary", None)
        # Falls back to the concrete path when no route matched, which is the
        # case for a 404 - still worth recording.
        action = getattr(request.scope.get("route"), "path", None) or request.url.path

        entry = AuditLog(
            actor_kind=principal.kind if principal else "anonymous",
            actor_label=principal.label if principal else None,
            key_id=principal.key_id if principal else None,
            method=request.method,
            path=request.url.path,
            action=action,
            status_code=status_code,
            detail=json.dumps(summary, default=str) if summary is not None else None,
            client_ip=request.client.host if request.client else None,
        )

        db = get_session_local()()
        try:
            db.add(entry)
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to write audit entry for %s %s", request.method, request.url.path)


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method not in AUDITED_METHODS:
            return await call_next(request)

        try:
            response = await call_next(request)
        except Exception:
            # An unhandled 500. Still worth a row - something tried to change
            # state and blew up - then let it propagate untouched.
            _write_entry(request, 500)
            raise

        # Rejections land here as ordinary responses, because FastAPI's
        # exception handling is inside this middleware. A 403 is the row that
        # matters most: the route never ran, so nothing downstream recorded
        # the attempt.
        _write_entry(request, response.status_code)
        return response
