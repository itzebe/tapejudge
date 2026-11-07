"""Authentication and session primitives for TapeJudge.

This module is the *mechanism*; the *policy* (who may do what) lives in
``service.py`` and is enforced in ``web/app.py``. Keeping the two apart is what
makes the access rules reviewable in one place.

What it provides:

* Password hashing via ``werkzeug.security`` — the scrypt default (a recognised,
  memory-hard KDF). A plaintext password is never stored, logged or compared with
  ``==``; ``check_password_hash`` is constant-time.
* A login session: ``login()`` rotates the session (a fresh CSRF token and a
  fresh session id, defeating session fixation) and stamps the account's current
  ``session_epoch``. ``current_account()`` re-reads the account on every request
  and rejects a session whose epoch has moved — so a logout-everywhere, a
  password change or an admin action invalidates it immediately.
* CSRF: a per-session token, compared in constant time. Enforced on every
  state-changing request.
* A login throttle keyed on (client-ip, email), so a brute force is blunted
  without revealing whether an account exists.

Nothing here trusts a request body for identity. A role, a judge id or an
account id supplied by the client is *never* read as a fact; the session is the
only source of identity.
"""
from __future__ import annotations

import hmac
import secrets

from werkzeug.security import check_password_hash, generate_password_hash

# Roles. The only roles the product defines; do not add more.
ROLE_ORGANISER = "organiser"
ROLE_JUDGE = "judge"
ROLE_ENTRANT = "entrant"
ROLES = (ROLE_ORGANISER, ROLE_JUDGE, ROLE_ENTRANT)

# Session keys. ``account_id`` is the only identity; the rest are convenience.
_SK_ACCOUNT = "account_id"
_SK_CSRF = "csrf"
_SK_EPOCH = "session_epoch"


def hash_password(password: str) -> str:
    """Hash a password with scrypt (werkzeug's default method)."""
    return generate_password_hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    return check_password_hash(password_hash, password)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def rotate_session(sess, account: dict) -> str:
    """Start an authenticated session for ``account`` and return its CSRF token.

    The caller must clear the existing session first (``sess.clear()``) so no
    pre-login state — a chosen CSRF token, a fixed session id — carries over.
    Rotating the CSRF token on login is what defeats session fixation.
    """
    token = new_csrf_token()
    sess[_SK_ACCOUNT] = account["id"]
    sess[_SK_EPOCH] = int(account.get("session_epoch", 0))
    sess[_SK_CSRF] = token
    return token


def end_session(sess) -> None:
    sess.clear()


def session_csrf_token(sess) -> str:
    token = sess.get(_SK_CSRF)
    if not token:
        token = new_csrf_token()
        sess[_SK_CSRF] = token
    return token


def csrf_ok(sess, supplied: str | None) -> bool:
    expected = sess.get(_SK_CSRF)
    if not expected or not supplied:
        return False
    return hmac.compare_digest(str(expected), str(supplied))


def current_account(sess, store) -> dict | None:
    """Resolve the session to a live account, or ``None``.

    The account is re-read from the store every request and the session epoch is
    checked, so a stale session cannot outlive a logout-everywhere or a password
    change. Returns a *public* view: never the password hash.
    """
    aid = sess.get(_SK_ACCOUNT)
    if not aid:
        return None
    acct = store.get_account(aid)
    if acct is None:
        return None
    if int(sess.get(_SK_EPOCH, -1)) != int(acct.get("session_epoch", 0)):
        return None
    return public_account(acct)


def public_account(acct: dict) -> dict:
    return {
        "id": acct["id"],
        "email": acct["email"],
        "display_name": acct.get("display_name") or "",
        "is_admin": bool(acct.get("is_admin")),
    }


# -- login throttle --------------------------------------------------------- #
class LoginThrottle:
    """A per-(ip, email) fixed-window throttle for the login form.

    Distinct from the write-path limiter: it keys on the *attempted* email as
    well as the client, so one attacker cannot lock a victim out of the whole
    app, and a slow, spread-out guessing run is still bounded. It never says
    whether the email exists — a throttled request and a wrong password return
    the same message.
    """

    def __init__(self, *, limit: int = 10, window_seconds: int = 300):
        import threading
        self.limit = limit
        self.window = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, str], list[float]] = {}

    def allow(self, ip: str, email: str, *, now: float | None = None) -> bool:
        import time
        now = time.time() if now is None else now
        key = (ip or "unknown", (email or "").strip().lower())
        with self._lock:
            stamps = [t for t in self._hits.get(key, []) if t > now - self.window]
            if len(stamps) >= self.limit:
                self._hits[key] = stamps
                return False
            stamps.append(now)
            self._hits[key] = stamps
            if len(self._hits) > 20_000:
                for k in [k for k, v in self._hits.items()
                          if not v or v[-1] <= now - self.window]:
                    self._hits.pop(k, None)
            return True

    def reset(self, ip: str, email: str) -> None:
        with self._lock:
            self._hits.pop((ip or "unknown", (email or "").strip().lower()), None)
