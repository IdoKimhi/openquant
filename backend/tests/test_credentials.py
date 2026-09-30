"""Credential rotation (issue #7).

The problem this closes is destructive and silent. `POST /credentials` used to
overwrite the stored key unconditionally, so rotating onto a new paper account
meant: paste the new keys, and if they were wrong the working ones were already
gone. Because the secret is Fernet-encrypted with a key that is not in the
database, there is no way back - the operator is left with a bot that cannot
trade and a key they have to go and find again.

So these tests are mostly about what must *not* happen:

- a rejected key must leave the stored one untouched (the old behaviour, which
  is the bug, is the one thing that must fail if this regresses);
- a probe that never reached the broker must not be reported as a verdict on
  the key, because the operator's response to "invalid" and to "try again later"
  are completely different and only one of them is correct;
- the secret must not appear in the audit trail, in a log line, or in the
  masked `GET /credentials` body.

The fake broker returns what the real one returns: a `uuid.UUID` id is not
needed here, but `equity` and `buying_power` are **`Decimal`**, not `str` and
not `float` (gotcha 5b). A stub that returns a string here is a stub that would
have hidden the coercion boundary.
"""

import json
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import requests
from alpaca.common.exceptions import APIError
from fastapi.testclient import TestClient

from app.authz import ALL_SCOPES, generate_agent_key
from app.db import get_db, get_engine, get_session_local
from app.main import app
from app.models import AgentKey, ApiCredentials
from app.security import decrypt, encrypt

client = TestClient(app)


def override_get_db():
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


# ---------------------------------------------------------------- helpers


def admin_headers():
    resp = client.post("/auth/login", json={"password": "testpass"})
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def stored_credentials(key_id="PKGOOD", secret_key="good-secret"):
    """The credential row a rotation is trying to replace."""
    db = get_session_local()()
    try:
        db.query(ApiCredentials).delete()
        db.add(
            ApiCredentials(
                key_id_encrypted=encrypt(key_id),
                secret_key_encrypted=encrypt(secret_key),
            )
        )
        db.commit()
    finally:
        db.close()


def read_stored():
    """(key_id, secret_key) as they are really on disk, decrypted."""
    db = get_session_local()()
    try:
        creds = db.query(ApiCredentials).first()
        if creds is None:
            return None
        return (
            decrypt(creds.key_id_encrypted),
            decrypt(creds.secret_key_encrypted),
        )
    finally:
        db.close()


def clear_credentials():
    db = get_session_local()()
    try:
        db.query(ApiCredentials).delete()
        db.commit()
    finally:
        db.close()


def fake_account(equity="25000.00", buying_power="50000.00", status="ACTIVE"):
    """A stand-in for `Account`, with the SDK's real field types.

    `equity`/`buying_power` are `Decimal` on the wire object. Returning a float
    here would hide whether the route coerces at the boundary.
    """
    class _Account:
        pass

    account = _Account()
    account.equity = Decimal(equity)
    account.buying_power = Decimal(buying_power)
    account.status = status
    return account


def broker_api_error(status, code, message):
    """An `APIError` shaped like the one a real rejection raises.

    Built from a genuine `requests` response so `APIError.status_code` - which
    reads `http_error.response.status_code` - resolves the way it does in
    production. The route branches on that status, so a stub without it would
    make every branch untestable.
    """
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps({"code": code, "message": message}).encode()
    http_error = requests.HTTPError(response=response)
    return APIError(response.text, http_error)


def broker_patches(get_account):
    """Patch `AlpacaClient` in the credentials router, and nowhere else.

    `autospec` is deliberately not used: the route constructs the client with
    keyword-free positional args and only ever awaits `get_account`, so a
    narrower fake keeps the test honest about the surface the route depends on.
    """
    mock_client = AsyncMock()
    mock_client.get_account = get_account
    return patch("app.routes.credentials.AlpacaClient", return_value=mock_client)


def accepts():
    """A broker that says yes."""
    return AsyncMock(return_value=fake_account())


def rejects(status=401, code=40110000, message="access key verification failed"):
    return AsyncMock(side_effect=broker_api_error(status, code, message))


# ------------------------------------------------- the core of issue #7


class TestARotatedKeyMustNotDestroyTheWorkingOne:
    def test_rejected_keys_leave_the_stored_credentials_untouched(self):
        """The bug. An invalid key must not cost you the working one.

        The pre-fix route wrote the new row first and validated never, so this
        assertion is the one that would have caught it. Asserting only on the
        400 would pass against both versions.
        """
        stored_credentials("PKGOOD", "good-secret")
        headers = admin_headers()

        with broker_patches(rejects()):
            resp = client.post(
                "/credentials",
                json={"key_id": "PKBAD", "secret_key": "bad-secret"},
                headers=headers,
            )

        assert resp.status_code == 400
        assert read_stored() == ("PKGOOD", "good-secret"), (
            "a rejected key destroyed the stored one - rotation is no longer "
            "safe and the operator cannot recover the old key from this app"
        )

    def test_a_broker_outage_is_also_not_a_verdict_on_the_key(self):
        """A 500 from Alpaca is not evidence that the key is wrong.

        Both leave the stored row alone, but the *message* matters: reporting a
        broker outage as "invalid credentials" sends the operator to re-enter a
        key that was fine, which is how a working configuration gets replaced
        with a worse one.
        """
        stored_credentials("PKGOOD", "good-secret")

        with broker_patches(rejects(status=500, code=50010000, message="internal error")):
            resp = client.post(
                "/credentials",
                json={"key_id": "PKNEW", "secret_key": "new-secret"},
                headers=admin_headers(),
            )

        assert resp.status_code == 502
        assert "invalid" not in resp.json()["detail"].lower()
        assert read_stored() == ("PKGOOD", "good-secret")

    def test_a_rate_limit_is_not_a_verdict_on_the_key(self):
        """429 is its own answer, and says so.

        Folding it into the auth branch is the tempting shortcut - one message,
        one status - and it tells the operator to do the one thing that makes it
        worse: re-paste keys against a broker that is asking them to slow down.
        """
        stored_credentials("PKGOOD", "good-secret")

        with broker_patches(rejects(status=429, code=42910000, message="rate limit exceeded")):
            resp = client.post(
                "/credentials",
                json={"key_id": "PKNEW", "secret_key": "new-secret"},
                headers=admin_headers(),
            )

        assert resp.status_code == 429
        assert "rate" in resp.json()["detail"].lower()
        assert read_stored() == ("PKGOOD", "good-secret")

    def test_a_transport_failure_is_not_a_verdict_on_the_key(self):
        """No HTTP response at all - a timeout, a DNS failure, a dead socket.

        This is why the route catches `APIError` and not `Exception`. A bare
        except would report any of these as "invalid credentials", which is
        both a lie and a bug that then gets "fixed" by entering a new key.
        """
        stored_credentials("PKGOOD", "good-secret")

        with broker_patches(AsyncMock(side_effect=TimeoutError("read timed out"))):
            resp = client.post(
                "/credentials",
                json={"key_id": "PKNEW", "secret_key": "new-secret"},
                headers=admin_headers(),
            )

        assert resp.status_code == 502
        assert "invalid" not in resp.json()["detail"].lower()
        assert read_stored() == ("PKGOOD", "good-secret")

    def test_a_bug_in_our_own_code_is_not_reported_as_invalid_credentials(self):
        """The negative case for the narrow except.

        A `TypeError` raised inside the route is our bug. Swallowing it into a
        400 that says "invalid credentials" hides it behind a message that
        points at the operator's key, where nobody will look for it.
        """
        stored_credentials("PKGOOD", "good-secret")

        with broker_patches(AsyncMock(side_effect=TypeError("bug in our code"))):
            resp = client.post(
                "/credentials",
                json={"key_id": "PKNEW", "secret_key": "new-secret"},
                headers=admin_headers(),
            )

        assert resp.status_code == 502
        assert "invalid credentials" not in resp.json()["detail"].lower()
        assert read_stored() == ("PKGOOD", "good-secret")

    def test_good_keys_do_replace_them(self):
        """The other direction: validation must not have made storing impossible."""
        stored_credentials("PKOLD", "old-secret")

        with broker_patches(accepts()):
            resp = client.post(
                "/credentials",
                json={"key_id": "PKNEW", "secret_key": "new-secret"},
                headers=admin_headers(),
            )

        assert resp.status_code == 200
        assert read_stored() == ("PKNEW", "new-secret")

    def test_the_brokers_own_reason_is_surfaced(self):
        """Key-id and secret failures share a 401 and are different problems.

        Neither is guessable from here, and the operator pasting keys into a
        form has no other way to tell which half they got wrong.
        """
        clear_credentials()
        with broker_patches(rejects(message="secret key verification failed")):
            resp = client.post(
                "/credentials",
                json={"key_id": "PKNEW", "secret_key": "nope"},
                headers=admin_headers(),
            )

        assert resp.status_code == 400
        assert "secret key verification failed" in resp.json()["detail"]

    def test_an_unparseable_broker_body_does_not_become_a_500(self):
        """`APIError.code` does an unguarded json.loads and raises.

        A proxy's HTML error page is not JSON, so reading the reason out of the
        body has to be guarded. Unguarded, a broker problem becomes a 500 and
        the operator learns nothing - and the failure looks like an app bug.
        """
        response = requests.Response()
        response.status_code = 401
        response._content = b"<html>502 Bad Gateway</html>"
        clear_credentials()

        with broker_patches(AsyncMock(side_effect=APIError(response.text, requests.HTTPError(response=response)))):
            resp = client.post(
                "/credentials",
                json={"key_id": "PKNEW", "secret_key": "nope"},
                headers=admin_headers(),
            )

        assert resp.status_code == 400
        assert "502 Bad Gateway" in resp.json()["detail"]


class TestTestingKeysWithoutCommittingThem:
    def test_a_passing_probe_stores_nothing(self):
        """The non-destructive half of a rotation.

        This is what makes the destructive half safe: the operator can read the
        new account's equity back and only then commit. If this endpoint wrote
        anything, "test before you commit" would be a lie.
        """
        stored_credentials("PKGOOD", "good-secret")

        with broker_patches(accepts()):
            resp = client.post(
                "/credentials/test-provided",
                json={"key_id": "PKCANDIDATE", "secret_key": "candidate-secret"},
                headers=admin_headers(),
            )

        assert resp.status_code == 200
        assert read_stored() == ("PKGOOD", "good-secret")

    def test_a_failing_probe_stores_nothing(self):
        stored_credentials("PKGOOD", "good-secret")

        with broker_patches(rejects()):
            resp = client.post(
                "/credentials/test-provided",
                json={"key_id": "PKCANDIDATE", "secret_key": "wrong"},
                headers=admin_headers(),
            )

        assert resp.status_code == 400
        assert read_stored() == ("PKGOOD", "good-secret")

    def test_it_reports_the_account_it_actually_reached(self):
        """So the operator can tell "this is the new account" from "same one"."""
        clear_credentials()
        with broker_patches(AsyncMock(return_value=fake_account(equity="98765.43"))):
            resp = client.post(
                "/credentials/test-provided",
                json={"key_id": "PKCANDIDATE", "secret_key": "candidate-secret"},
                headers=admin_headers(),
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "connected"
        # A number, because the frontend formats it as money. The SDK returns a
        # Decimal and the conversion happens at this boundary (gotcha 5b).
        assert body["equity"] == pytest.approx(98765.43)
        assert isinstance(body["equity"], (int, float))


class TestDeletingCredentials:
    def test_delete_removes_them(self):
        stored_credentials("PKGOOD", "good-secret")

        resp = client.delete("/credentials", headers=admin_headers())

        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert read_stored() is None

    def test_status_reports_unconfigured_afterwards(self):
        """The state the rest of the app reads. A delete that left `has_keys`
        true would show a bot wired to credentials that no longer exist."""
        stored_credentials("PKGOOD", "good-secret")

        client.delete("/credentials", headers=admin_headers())

        status = client.get("/credentials/status", headers=admin_headers())
        assert status.json()["has_keys"] is False

    def test_delete_is_idempotent_only_in_the_sense_that_it_404s(self):
        """A second delete has nothing to do and says so, rather than
        pretending to succeed - a 200 here would tell the operator their keys
        are gone when the request never found any."""
        clear_credentials()
        assert client.delete("/credentials", headers=admin_headers()).status_code == 404
        assert client.delete("/credentials", headers=admin_headers()).status_code == 404

    def test_delete_does_not_touch_the_profiles(self):
        """Rotating keys is not reconfiguring the bot.

        Losing the strategy configuration on a credential change would be a
        nasty surprise, and it is the kind of collateral damage nobody notices
        until they go looking for the profile.
        """
        from app.models import StrategyProfile, StrategyType

        stored_credentials("PKGOOD", "good-secret")
        db = get_session_local()()
        try:
            db.add(
                StrategyProfile(
                    name="survivor",
                    strategy_type=StrategyType.rsi_reversion,
                    parameters={"period": 14},
                    symbols=["AAPL"],
                    enabled=False,
                )
            )
            db.commit()
        finally:
            db.close()

        client.delete("/credentials", headers=admin_headers())

        db = get_session_local()()
        try:
            assert db.query(StrategyProfile).filter_by(name="survivor").one() is not None
        finally:
            db.close()


class TestMaskedCredentialInfo:
    def test_it_reports_the_last_four_and_never_the_rest(self):
        stored_credentials("PKABCDEFGHIJ", "super-secret-value")

        resp = client.get("/credentials", headers=admin_headers())

        assert resp.status_code == 200
        body = resp.json()
        assert body["key_id_last4"] == "PKABCDEFGHIJ"[-4:]
        # The whole response, serialised, must not contain either secret.
        assert "PKABCDEFGHIJ" not in resp.text
        assert "super-secret-value" not in resp.text

    def test_the_created_at_is_utc(self):
        """`UtcDatetime` on the model, so the browser cannot misread it.

        Naive datetimes from `datetime.utcnow()` are the bug behind issue #4's
        timestamps: they round-trip with no `Z` and a browser reads them as
        local time, which is invisible on a UTC machine and wrong for the
        operator.
        """
        stored_credentials("PKGOOD", "good-secret")

        body = client.get("/credentials", headers=admin_headers()).json()

        assert body["created_at"].endswith("Z") or "+00:00" in body["created_at"], (
            f"created_at is not explicitly UTC: {body['created_at']!r}"
        )

    def test_unconfigured_is_404_not_an_empty_object(self):
        """There is nothing to describe, and an empty 200 would render as
        "key: " on the setup screen."""
        clear_credentials()
        assert client.get("/credentials", headers=admin_headers()).status_code == 404


class TestCredentialsAreHumanOnly:
    """Issue #7 adds three new routes on the human-only router.

    A route declared without a scope dependency is public, and the audit
    middleware records whatever it sees - so a new route on this router is also
    a new thing an agent key could reach if the dependency is ever dropped.
    Checked in the negative direction: `ALL_SCOPES` is deliberately not used,
    because "an agent with every scope is refused" is the assertion that matters.
    """

    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("get", "/credentials", None),
            ("delete", "/credentials", None),
            ("post", "/credentials", {"key_id": "PKX", "secret_key": "s"}),
            ("post", "/credentials/test-provided", {"key_id": "PKX", "secret_key": "s"}),
            ("post", "/credentials/test", None),
        ],
    )
    def test_an_agent_key_is_refused_even_with_every_scope(self, method, path, body):
        db = get_session_local()()
        try:
            plaintext, prefix, key_hash = generate_agent_key()
            db.add(
                AgentKey(
                    label="all-scopes",
                    key_prefix=prefix,
                    key_hash=key_hash,
                    scopes=list(ALL_SCOPES),
                )
            )
            db.commit()
        finally:
            db.close()

        resp = client.request(method, path, json=body, headers={"Authorization": f"Bearer {plaintext}"})

        assert resp.status_code == 403, f"{method.upper()} {path} was reachable by an agent key"
        # A valid key on a human-only route is a different failure from a
        # missing header, and only the detail tells them apart (gotcha 13).
        assert "admin session" in resp.json()["detail"].lower()

    def test_an_unauthenticated_call_is_refused(self):
        for method, path in [("get", "/credentials"), ("delete", "/credentials")]:
            resp = getattr(client, method)(path)
            assert resp.status_code in (401, 403)


class TestSecretsStayOutOfTheTrail:
    """The rotation endpoints take a broker key in the request body.

    `record_summary` is opt-in per route precisely so that this stays a
    decision rather than a default, and `POST /credentials` has always been the
    worked example. The new routes have to hold the same line: a `read`-scoped
    agent can fetch the audit table, so anything written there is readable by
    the least privileged caller in the system.
    """

    def _audit_rows(self):
        """(path, status_code, detail) straight out of the table."""
        from sqlalchemy import text

        from app.db import get_engine

        with get_engine().connect() as conn:
            return [
                (row[0], row[1], row[2] or "")
                for row in conn.execute(text("SELECT path, status_code, detail FROM audit_log"))
            ]

    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("post", "/credentials", {"key_id": "PKLEAKME01", "secret_key": "LEAKMESECRET"}),
            ("post", "/credentials/test-provided", {"key_id": "PKLEAKME01", "secret_key": "LEAKMESECRET"}),
        ],
    )
    def test_no_route_writes_a_broker_key_into_the_audit_log(self, method, path, body):
        with broker_patches(accepts()):
            client.request(method, path, json=body, headers=admin_headers())

        for audit_path, _, detail in self._audit_rows():
            assert "LEAKMESECRET" not in detail, f"{audit_path} recorded the secret key"
            assert "PKLEAKME01" not in detail, f"{audit_path} recorded the key id"

    def test_the_rotation_routes_are_audited_at_all(self):
        """Recorded by method + path + status, with no body.

        A rotation is exactly the kind of privileged change an audit trail
        exists for, and an unreadable key cannot be attributed to anyone.
        """
        stored_credentials("PKGOOD", "good-secret")
        with broker_patches(accepts()):
            client.post(
                "/credentials",
                json={"key_id": "PKROTATED", "secret_key": "rotated-secret"},
                headers=admin_headers(),
            )

        assert any(path == "/credentials" for path, _, _ in self._audit_rows())

    def test_a_refused_rotation_is_also_recorded(self):
        """The 400 is the interesting row.

        A failed attempt to replace the broker key is precisely what an operator
        reviewing the account later wants to see, and the route never got as far
        as doing anything, so nothing downstream recorded the attempt.
        """
        stored_credentials("PKGOOD", "good-secret")
        with broker_patches(rejects()):
            client.post(
                "/credentials",
                json={"key_id": "PKBAD", "secret_key": "bad"},
                headers=admin_headers(),
            )

        rows = self._audit_rows()
        assert any(path == "/credentials" for path, _, _ in rows)
        assert any(status == 400 for _, status, _ in rows), "the refusal was not recorded"
