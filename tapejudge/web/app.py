"""TapeJudge HTTP application (Flask).

Eight screens, one job each, matching the Mission 3 UX specification:

1. ``/``                     competition list and discovery
2. ``/new``                  create competition
3. ``/c/<id>/rubric``        configure and review the rubric
4. ``/c/<id>/publish``       publish / freeze (publication state)
5. ``/c/<id>/submit``        submit an entry
6. ``/c/<id>/judge``         judge evaluation
7. ``/c/<id>/results``       competition results
8. ``/c/<id>/verify``        public verification (the proof panel)

Plus a small JSON API under ``/api`` used by the tests and by any client.

Design rules honoured here:

* The server never invents data. Every number on a page is recomputed from the
  frozen rubric and the stored evaluations.
* Publication state is reported verbatim from the service (``local_only`` vs
  ``onchain_pending_signature`` vs ``onchain_verified``); the UI never upgrades a
  local circuit to "on-chain".
* Every route that can fail returns a page that says what failed, not a stack.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

from .. import auth
from ..auth import LoginThrottle
from ..netlist import IllFormed
from ..service import (
    AuthError,
    ConflictError,
    InvitationError,
    TapeJudgeService,
    ValidationError,
    _as_int,
)
from ..store import make_store
from ..xlayer import XLayerClient
from .ratelimit import RateLimiter

_HERE = Path(__file__).resolve().parent

# Every environment variable whose presence means a *hosted* database could be
# selected. A testing app must present none of them (see _require_test_isolation).
# Deliberately excludes TAPEJUDGE_DB (a SQLite file path) and
# TAPEJUDGE_TEST_DATABASE_URL (the isolated Postgres contract DSN, which never
# flows through create_app).
_HOSTED_DB_INDICATORS = (
    "VERCEL",                     # the serverless marker: _require_production_config
    "TAPEJUDGE_DATABASE_URL",     # the DSN make_store() selects Postgres from
    "DATABASE_URL",               # generic hosted-DSN aliases a deploy may expose
    "POSTGRES_URL",
    "POSTGRES_URL_NON_POOLING",
    "POSTGRES_PRISMA_URL",
    "POSTGRES_HOST",
    "POSTGRES_USER",
)


def create_app(*, db_path: str | None = None, chain_client=None,
               testing: bool = False) -> Flask:
    app = Flask(__name__,
                template_folder=str(_HERE / "templates"),
                static_folder=str(_HERE / "static"))
    _require_production_config(testing)
    _require_test_isolation(testing)
    _configure_secrets(app, testing)
    _configure_sessions(app)
    app.config["TESTING"] = testing
    db = db_path or os.environ.get("TAPEJUDGE_DB", ":memory:")
    store = make_store(db)
    app.config["STORE"] = store
    app.config["SERVICE"] = TapeJudgeService(store, chain_client=chain_client)
    app.config["CHAIN"] = chain_client or XLayerClient()
    app.config["LOGIN_THROTTLE"] = LoginThrottle()
    # Opt-in HSTS for a host that serves its own domain over TLS. Never default.
    app.config["ENABLE_HSTS"] = os.environ.get("TAPEJUDGE_ENABLE_HSTS") == "1"
    _register_routes(app)
    _register_security_headers(app)
    _register_error_handlers(app)
    _register_rate_limit(app, testing)
    return app


def _register_rate_limit(app: Flask, testing: bool) -> None:
    """Blunt abuse on the write paths (entry spam, evaluation flooding).

    Disabled under TESTING so the suite is deterministic. Limits are generous for
    a human, tight for a script; a rejected request is a clean 429.
    """
    if testing or os.environ.get("TAPEJUDGE_DISABLE_RATELIMIT") == "1":
        return
    limiter = RateLimiter(default_limit=240, window_seconds=60)
    limiter.rule("eval", 120)
    limiter.rule("entry", 60)
    limiter.rule("comp", 30)
    app.config["RATE_LIMITER"] = limiter

    def _bucket() -> str:
        p = request.path
        if p.endswith("/evaluations"):
            return "eval"
        if p.endswith("/entries"):
            return "entry"
        if p.endswith(("/rubric", "/freeze")) or p == "/api/competitions":
            return "comp"
        return "misc"

    @app.before_request
    def _limit():
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return None
        ip = request.remote_addr or "unknown"
        if not limiter.allow(ip, _bucket()):
            if request.path.startswith("/api/"):
                return jsonify({"error": "too many requests; slow down"}), 429
            return render_template("error.html", code=429,
                                   message="Too many requests. Please slow down."), 429
        return None


def _require_production_config(testing: bool) -> None:
    """Refuse a hosted start that would silently lose data or ship a weak secret.

    A serverless host sets ``VERCEL`` (and often ``VERCEL_ENV``). There the two
    graceful local fallbacks are both wrong: a per-process random secret logs
    every user out on each cold start, and the default SQLite ``:memory:`` store
    is wiped on every invocation, so the deployment would look healthy while
    persisting nothing. Fail loudly at boot instead of degrading. Local runs,
    the test suite and the container are unaffected (they do not set ``VERCEL``).
    """
    if testing or not os.environ.get("VERCEL"):
        return
    if os.environ.get("TAPEJUDGE_ALLOW_DEV_SECRET") == "1":
        raise RuntimeError(
            "refusing to start: TAPEJUDGE_ALLOW_DEV_SECRET must not be set on a "
            "hosted deployment")
    if not os.environ.get("TAPEJUDGE_SECRET"):
        raise RuntimeError(
            "refusing to start: TAPEJUDGE_SECRET is not set. A hosted deployment "
            "must have a fixed secret or sessions are lost on every cold start.")
    if not os.environ.get("TAPEJUDGE_DATABASE_URL"):
        raise RuntimeError(
            "refusing to start: TAPEJUDGE_DATABASE_URL is not set. Vercel's "
            "function filesystem is not persistent, so the SQLite store would "
            "lose every record; configure a PostgreSQL DSN.")
    if not os.environ.get("TAPEJUDGE_DB_SCHEMA"):
        raise RuntimeError(
            "refusing to start: TAPEJUDGE_DB_SCHEMA is not set. A serverless host "
            "builds a new store per request; without a stable schema each request "
            "would read an empty database.")


def _require_test_isolation(testing: bool) -> None:
    """A test build must never open a hosted or production database.

    The incident this closes: a test run inherited a deployed shell's
    environment (``VERCEL`` plus ``POSTGRES_*``), so ``make_store`` selected the
    live PostgreSQL DSN and hundreds of fixture rows were written into the
    *production* schema. Checking a single variable is not enough — a hosted
    environment can present the DSN under any of several names, or only as
    discrete ``POSTGRES_*`` parts, or only as the ``VERCEL`` marker.

    So this guard **fails closed**: if *any* hosted-database indicator is present
    in a test process it refuses to start, before :func:`make_store` is reached,
    rather than silently degrading to SQLite. Tests get the SQLite store
    (``db_path=":memory:"`` or ``TAPEJUDGE_DB``). The PostgreSQL contract
    (``tests/test_store_contract.py``) never goes through here: it builds its own
    store against an explicitly isolated ``TAPEJUDGE_TEST_DATABASE_URL`` schema.
    """
    if not testing:
        return
    present = sorted(v for v in _HOSTED_DB_INDICATORS if os.environ.get(v))
    if present:
        raise RuntimeError(
            "refusing to start: a testing app must not run with a hosted "
            "database configuration present (" + ", ".join(present) + "). "
            "Tests use the SQLite store (db_path or TAPEJUDGE_DB); the "
            "PostgreSQL contract runs against an isolated "
            "TAPEJUDGE_TEST_DATABASE_URL schema and never through create_app.")


def _configure_secrets(app: Flask, testing: bool) -> None:
    """Never ship a known default secret in production.

    Flask signs the flash-message session cookie with ``SECRET_KEY``. A hardcoded
    default is fine for a local demo but is a real weakness the moment the app is
    reachable: anyone who knows it can forge a cookie. In production we refuse to
    start with no configured secret, and we do not fall back to a fixed string.
    """
    secret = os.environ.get("TAPEJUDGE_SECRET")
    if not secret:
        if testing or app.debug or os.environ.get("TAPEJUDGE_ALLOW_DEV_SECRET") == "1":
            # A fixed, obviously-fake secret is acceptable for tests and a local
            # demo only; it is never reachable unless one of those is true.
            secret = "dev-only-not-a-secret"  # noqa: S105
        else:
            # A per-process random secret: sessions do not survive a restart, but
            # nothing is forgeable and no default value is ever shipped. This is
            # still a misconfiguration for a long-lived deployment (every restart
            # logs everyone out), so say so loudly rather than degrade silently.
            secret = secrets.token_hex(32)
            logging.getLogger("tapejudge").warning(
                "TAPEJUDGE_SECRET is not set: using a per-process random secret. "
                "Sessions will not survive a restart. Set TAPEJUDGE_SECRET for a "
                "real deployment."
            )
    app.config["SECRET_KEY"] = secret


def _configure_sessions(app: Flask) -> None:
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Only send the cookie over HTTPS in production; a local http demo would
        # otherwise never receive it.
        SESSION_COOKIE_SECURE=os.environ.get("TAPEJUDGE_INSECURE_COOKIES") != "1",
        PERMANENT_SESSION_LIFETIME=3600,
        MAX_CONTENT_LENGTH=256 * 1024,
    )


def _register_security_headers(app: Flask) -> None:
    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        # Deny powerful browser features the app never uses. An empty allowlist
        # disables each for this origin (and its frames) rather than leaving the
        # browser default in place.
        resp.headers.setdefault(
            "Permissions-Policy",
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
            "magnetometer=(), microphone=(), payment=(), usb=()")
        # Same-origin-allow-popups keeps the browsing context isolated while still
        # permitting the popups a wallet flow may open; the window object is not
        # shared with cross-origin documents.
        resp.headers.setdefault("Cross-Origin-Opener-Policy",
                                "same-origin-allow-popups")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'")
        if request.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        # HSTS is opt-in and only on a host that serves its own domain over TLS.
        # Forcing it on a shared work-host domain would pin every sibling
        # subdomain to HTTPS for a year, so it is never the default.
        if app.config.get("ENABLE_HSTS"):
            resp.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains")
        return resp


def _svc() -> TapeJudgeService:
    return g.app_service


def _register_routes(app: Flask) -> None:

    @app.before_request
    def _bind():
        g.app_service = app.config["SERVICE"]
        # Resolve the session to a live account once per request. Every
        # authorisation decision reads ``g.account``; nothing reads an identity
        # from the request body.
        g.account = auth.current_account(session, app.config["STORE"])
        # A token is minted lazily so a GET also has one to embed in a form.
        g.csrf_token = auth.session_csrf_token(session)

    # CSRF: every state-changing request must carry the session's token. This is
    # enforced centrally, so a new write route cannot be added without it.
    _SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}

    @app.before_request
    def _csrf_guard():
        if request.method in _SAFE_METHODS:
            return None
        supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not auth.csrf_ok(session, supplied):
            # A caller who is not signed in has not merely lost a token; telling
            # them to sign in is clearer than a CSRF error, and no state can
            # change either way. An authenticated caller gets the CSRF refusal.
            if g.account is None:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "authentication required"}), 401
                flash("Sign in to continue.", "error")
                return redirect(url_for("login", next=request.path))
            if request.path.startswith("/api/"):
                return jsonify({"error": "missing or invalid CSRF token"}), 403
            return render_template("error.html", code=403,
                                   message="Your session token was missing or stale. "
                                           "Reload the page and try again."), 403
        return None

    # -- helpers ------------------------------------------------------------ #
    def _comp_or_404(cid):
        try:
            return _svc().get_competition(cid)
        except KeyError:
            abort(404)

    def _account_id():
        return g.account["id"] if g.account else None

    def _safe_next(nxt: str | None) -> str | None:
        """Return ``nxt`` only if it is a safe, same-site path; else ``None``.

        A bare ``startswith("/")`` check is not enough: a browser treats a
        backslash as a forward slash, so ``/\\evil.com`` becomes a protocol-
        relative URL to another host — an open redirect through the sign-in form.
        Accept only a path with a single leading slash and no backslash, and
        reject a control character that could split the Location header.
        """
        if not nxt or not nxt.startswith("/"):
            return None
        if nxt.startswith("//") or "\\" in nxt:
            return None
        if any(ord(ch) < 0x20 for ch in nxt):
            return None
        return nxt

    def _require_login(next_url=None):
        if g.account is not None:
            return None
        if request.path.startswith("/api/"):
            abort(401)
        flash("Sign in to continue.", "error")
        return redirect(url_for("login", next=next_url or request.path))

    def _require_organiser(cid):
        """The one browser gate for an organiser action. Raises ``AuthError``,
        which the central handler turns into a 404 for HTML and a 403 for JSON —
        the same response an unknown competition gives, so it cannot be used to
        probe ownership."""
        _svc().require_organiser(cid, _account_id())

    def _form_dict(prefix="crit"):
        """Collect criteria rows from a form: crit_name[], crit_weight[], crit_max[]."""
        names = request.form.getlist(f"{prefix}_name")
        weights = request.form.getlist(f"{prefix}_weight")
        maxima = request.form.getlist(f"{prefix}_max")
        out = []
        for n, w, m in zip(names, weights, maxima, strict=False):
            if not (n or "").strip() and not (w or "").strip():
                continue
            out.append({"name": n.strip(), "weight": w, "max_score": m})
        return out

    # ---------------------------------------------------------------- list #
    @app.get("/")
    def index():
        comps = _svc().store.list_competitions()
        # The landing page shows a real, frozen competition as its ledger
        # preview when one exists, so the example is never fabricated. With no
        # frozen competition the template falls back to a clearly-labelled
        # synthetic example.
        featured = next((c for c in comps if c.get("rubric_digest")), None)
        return render_template("index.html", competitions=comps,
                               featured=featured,
                               featured_rubric=(_featured_rubric(featured)),
                               chain_reachable=app.config["CHAIN"].is_reachable())

    # Browsers request /favicon.ico on every page even when a PNG/SVG icon is
    # declared. Serve the SVG there so the page never logs a 404 for it.
    @app.get("/favicon.ico")
    def favicon():
        return send_from_directory(app.static_folder, "favicon.svg",
                                   mimetype="image/svg+xml")

    # --------------------------------------------------------------- auth -- #
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.account is not None:
            return redirect(url_for("index"))
        if request.method == "POST":
            email = request.form.get("email", "")
            password = request.form.get("password", "")
            ip = request.remote_addr or "unknown"
            throttle = app.config["LOGIN_THROTTLE"]
            if not throttle.allow(ip, email):
                flash("Too many sign-in attempts. Wait a few minutes and try again.", "error")
                return render_template("login.html", email=email), 429
            acct = _svc().authenticate(email=email, password=password)
            if acct is None:
                # The same message whether the account exists or the password is
                # wrong: never disclose which.
                flash("Email or password is incorrect.", "error")
                return render_template("login.html", email=email), 401
            throttle.reset(ip, email)
            # Clear any pre-login state, then rotate the session id and token.
            session.clear()
            auth.rotate_session(session, acct)
            nxt = _safe_next(request.args.get("next") or request.form.get("next"))
            return redirect(nxt or url_for("index"))
        return render_template("login.html", email="")

    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        if g.account is not None:
            return redirect(url_for("index"))
        if request.method == "POST":
            email = request.form.get("email", "")
            password = request.form.get("password", "")
            display_name = request.form.get("display_name", "")
            try:
                acct = _svc().register_account(
                    email=email, password=password, display_name=display_name)
            except (ValidationError, ConflictError) as e:
                flash(str(e), "error")
                return render_template("signup.html", email=email,
                                       display_name=display_name), 400
            session.clear()
            auth.rotate_session(session, acct)
            flash("Account created.", "ok")
            return redirect(url_for("index"))
        return render_template("signup.html", email="", display_name="")

    @app.post("/logout")
    def logout():
        session.clear()
        flash("Signed out.", "ok")
        return redirect(url_for("index"))

    @app.get("/account")
    def account():
        if g.account is None:
            return redirect(url_for("login", next="/account"))
        memberships = _svc().store.memberships_for_account(g.account["id"])
        comps = []
        for m in memberships:
            comp = _svc().store.get_competition(m["competition_id"])
            if comp:
                comps.append({"competition": comp, "role": m["role"], "label": m["label"]})
        return render_template("account.html", memberships=comps)

    @app.route("/invitations/<token>", methods=["GET", "POST"])
    def accept_invitation(token):
        inv = _svc().invitation_by_token(token)
        status = _svc().invitation_status(inv)
        if g.account is None:
            # Ask them to sign in (or create an account) first; the token is
            # carried in the path so the round-trip preserves it.
            return redirect(url_for("login", next=url_for("accept_invitation", token=token)))
        if request.method == "POST":
            try:
                result = _svc().accept_invitation(token, account_id=_account_id())
            except InvitationError as e:
                flash(str(e), "error")
                return render_template("invitation.html", inv=inv, status=status), 400
            flash("Invitation accepted. You are a judge on this competition.", "ok")
            return redirect(url_for("judge", cid=result["competition_id"]))
        return render_template("invitation.html", inv=inv, status=status)

    # -------------------------------------------------------------- create #
    @app.route("/new", methods=["GET", "POST"])
    def new_competition():
        if request.method == "POST":
            r = _require_login()
            if r:
                return r
            judges = [j.strip() for j in request.form.getlist("judge") if j.strip()]
            if not judges:
                judges = [j.strip() for j in
                          (request.form.get("judges_raw", "") or "").replace(",", "\n").splitlines()
                          if j.strip()]
            try:
                cid = _svc().create_competition(
                    name=request.form.get("name", ""),
                    description=request.form.get("description", ""),
                    organiser=request.form.get("organiser", "") or g.account["display_name"],
                    opens_at=request.form.get("opens_at", ""),
                    closes_at=request.form.get("closes_at", ""),
                    judges=judges,
                    prize_note=request.form.get("prize_note", ""),
                    owner_account_id=_account_id())
                return redirect(url_for("rubric", cid=cid))
            except ValidationError as e:
                flash(str(e), "error")
        return render_template("new.html")

    # -------------------------------------------------------------- rubric #
    @app.route("/c/<cid>/rubric", methods=["GET", "POST"])
    def rubric(cid):
        comp = _comp_or_404(cid)
        if request.method == "POST":
            r = _require_login()
            if r:
                return r
            # The ownership gate runs *outside* the try: its AuthError must
            # reach the central handler so a foreign competition is refused with
            # the same 404 as an unknown one, never a 200 page with a flash.
            _require_organiser(cid)
            try:
                criteria = _form_dict()
                tie_break = [t for t in request.form.getlist("tie_break") if t]
                judges_n = _as_int(request.form.get("judges", comp.get("judges", 1) or 1))
                _svc().set_rubric(cid, criteria, judges_n, tie_break)
                flash("Rubric configured. Review it, then freeze.", "ok")
                return redirect(url_for("rubric", cid=cid))
            except (ValidationError, ConflictError) as e:
                flash(str(e), "error")
        comp = _comp_or_404(cid)
        preview = _rubric_preview(comp)
        return render_template("rubric.html", c=comp, preview=preview,
                               max_total=_max_total(comp),
                               is_organiser=_svc().is_organiser(cid, _account_id()))

    # ------------------------------------------------------------- publish #
    @app.route("/c/<cid>/publish", methods=["GET", "POST"])
    def publish(cid):
        comp = _comp_or_404(cid)
        if request.method == "POST":
            r = _require_login()
            if r:
                return r
            action = request.form.get("action")
            # Ownership gate outside the try (see rubric): a foreign competition
            # must be refused with the same 404 as an unknown one.
            _require_organiser(cid)
            try:
                if action == "freeze":
                    _svc().freeze(cid)
                    flash("Rubric frozen. The rule can no longer change.", "ok")
                elif action == "publish":
                    result = _svc().publish(cid)
                    cat = "ok" if result["mode"] != "local_only" else "warn"
                    flash(_publish_message(result), cat)
                elif action == "confirm":
                    result = _svc().confirm_publication(
                        cid, tx_hash=(request.form.get("tx_hash") or "").strip())
                    if result["verified"]:
                        flash("Publication confirmed on X Layer: " + result["reason"], "ok")
                    else:
                        flash("Not confirmed: " + result["reason"], "warn")
                return redirect(url_for("publish", cid=cid))
            except (ValidationError, ConflictError) as e:
                flash(str(e), "error")
        comp = _comp_or_404(cid)
        return render_template("publish.html", c=comp, preview=_rubric_preview(comp),
                               is_organiser=_svc().is_organiser(cid, _account_id()))

    # --------------------------------------------------------------- panel #
    @app.route("/c/<cid>/judges", methods=["GET", "POST"])
    def judges(cid):
        comp = _comp_or_404(cid)
        r = _require_login()
        if r:
            return r
        _require_organiser(cid)
        new_token = None
        if request.method == "POST":
            action = request.form.get("action", "invite")
            try:
                if action == "revoke":
                    _svc().revoke_judge(cid, label=request.form.get("label", ""),
                                        organiser_account_id=_account_id())
                    flash("Judge revoked.", "ok")
                else:
                    result = _svc().invite_judge(
                        cid, email=request.form.get("email", ""),
                        label=request.form.get("label", ""),
                        organiser_account_id=_account_id())
                    new_token = result["token"]
                    flash("Invitation created. Share the link below with the judge.", "ok")
            except (ValidationError, ConflictError, AuthError) as e:
                flash(str(e), "error")
        return render_template("judges.html", c=comp,
                               invitations=_svc().list_invitations(cid, _account_id()),
                               new_token=new_token,
                               invite_url=(url_for("accept_invitation", token=new_token,
                                                   _external=False) if new_token else None))

    # -------------------------------------------------------------- submit #
    @app.route("/c/<cid>/submit", methods=["GET", "POST"])
    def submit(cid):
        comp = _comp_or_404(cid)
        if request.method == "POST":
            r = _require_login()
            if r:
                return r
            try:
                _svc().add_entry(
                    cid, stable_id=request.form.get("stable_id", ""),
                    title=request.form.get("title", ""),
                    summary=request.form.get("summary", ""),
                    link=request.form.get("link", ""),
                    private=request.form.get("private") == "on",
                    submitter_account_id=_account_id())
                flash("Entry submitted.", "ok")
                return redirect(url_for("submit", cid=cid))
            except (ValidationError, ConflictError) as e:
                flash(str(e), "error")
        entries = _svc().list_entries(
            cid, viewer_account_id=_account_id())
        return render_template("submit.html", c=comp, entries=entries)

    # --------------------------------------------------------------- judge #
    @app.route("/c/<cid>/judge", methods=["GET", "POST"])
    def judge(cid):
        comp = _comp_or_404(cid)
        r = _require_login()
        if r:
            return r
        # A judge may only score under the label their membership binds them to;
        # the label is never taken from the request.
        label = _svc().judge_label(cid, _account_id())
        if request.method == "POST":
            action = request.form.get("action", "score")
            try:
                if label is None:
                    raise AuthError("you are not a judge on this competition")
                if action == "conflict":
                    _svc().declare_conflict(cid, judge=label,
                                            entry_stable_id=request.form.get("entry", ""),
                                            reason=request.form.get("reason", ""),
                                            judge_account_id=_account_id())
                    flash("Conflict of interest recorded; this judge will "
                          "not score that entry.", "ok")
                else:
                    scores = {c["name"]: request.form.get(f"score_{c['name']}")
                              for c in (comp.get("rubric") or {}).get("criteria", [])}
                    _svc().evaluate(cid, entry_stable_id=request.form.get("entry", ""),
                                    judge=label, scores=scores,
                                    justification=request.form.get("justification", ""),
                                    judge_account_id=_account_id())
                    flash("Score committed. It cannot be changed.", "ok")
                return redirect(url_for("judge", cid=cid))
            except (ValidationError, ConflictError, AuthError) as e:
                flash(str(e), "error")
        return render_template("judge.html", c=comp,
                               entries=_svc().list_entries(cid, viewer_account_id=_account_id()),
                               judge=label,
                               matrix=_judge_matrix(cid))

    # ------------------------------------------------------------- results #
    @app.route("/c/<cid>/results", methods=["GET", "POST"])
    def results(cid):
        comp = _comp_or_404(cid)
        if request.method == "POST":
            r = _require_login()
            if r:
                return r
            # Ownership gate outside the try (see rubric): a foreign competition
            # must be refused with the same 404 as an unknown one.
            _require_organiser(cid)
            try:
                _svc().settle(cid)
                flash("Competition finalised.", "ok")
            except (ValidationError, ConflictError) as e:
                flash(str(e), "error")
            return redirect(url_for("results", cid=cid))
        if not comp.get("rubric_json"):
            return render_template("pending.html", c=comp, what="results")
        res = _svc().results(cid)
        return render_template("results.html", c=comp, r=res,
                               criteria=(comp.get("rubric") or {}).get("criteria", []))

    # -------------------------------------------------------------- verify #
    @app.route("/c/<cid>/verify")
    def verify(cid):
        comp = _comp_or_404(cid)
        if not comp.get("rubric_json"):
            return render_template("pending.html", c=comp, what="verification")
        live = request.args.get("live") == "1"
        rep = _svc().verify(cid, live_chain=live)
        # The exact eval() command a reader can run for each entry.
        commands = []
        for e in rep["ranking"]:
            commands.append({
                "entry": e["entry"],
                "total": e["total"],
                "call": _eval_call(comp, e["entry"]),
            })
        return render_template("verify.html", c=comp, rep=rep, commands=commands,
                               live=live, api_url=url_for("api_verify", cid=cid, _external=False))

    # ------------------------------------------------------------- API ---- #
    # Reads are public (the product's promise is that anyone can verify).
    @app.get("/api/health")
    def api_health():
        return jsonify({"ok": True, "chain_reachable": app.config["CHAIN"].is_reachable()})

    @app.get("/api/integration")
    def api_integration():
        """The honest state of the X Layer TapeOut integration (public read)."""
        return jsonify(_svc().tapeout_integration_state())

    @app.get("/api/me")
    def api_me():
        if g.account is None:
            return jsonify({"authenticated": False}), 200
        return jsonify({"authenticated": True, "account": g.account})

    @app.get("/api/competitions")
    def api_competitions():
        return jsonify([{"id": c["id"], "name": c["name"], "status": c["status"],
                         "rubric_digest": c["rubric_digest"]}
                        for c in _svc().store.list_competitions()])

    @app.get("/api/competitions/<cid>")
    def api_competition(cid):
        return jsonify(_comp_or_404(cid))

    @app.get("/api/competitions/<cid>/results")
    def api_results(cid):
        _comp_or_404(cid)
        return jsonify(_svc().results(cid))

    @app.get("/api/competitions/<cid>/verify")
    def api_verify(cid):
        _comp_or_404(cid)
        return jsonify(_svc().verify(cid, live_chain=request.args.get("live") == "1"))

    @app.get("/api/competitions/<cid>/audit")
    def api_audit(cid):
        comp = _comp_or_404(cid)
        return jsonify({"competition": comp,
                        "events": _svc().store.list_events(cid)})

    # -- API writes --------------------------------------------------------- #
    # Every write requires (1) an authenticated account, (2) the right role for
    # this competition, (3) ownership of the specific object, (4) a valid
    # lifecycle state. The checks are the same service calls the browser routes
    # use, so a client cannot bypass them by talking to the API directly.
    @app.post("/api/competitions")
    def api_create():
        if g.account is None:
            abort(401)
        body = request.get_json(force=True, silent=True) or {}
        cid = _svc().create_competition(
            name=body.get("name", ""), description=body.get("description", ""),
            organiser=body.get("organiser", ""), opens_at=body.get("opens_at", ""),
            closes_at=body.get("closes_at", ""), judges=body.get("judges") or [],
            owner_account_id=_account_id())
        return jsonify({"id": cid}), 201

    @app.post("/api/competitions/<cid>/rubric")
    def api_set_rubric(cid):
        # No existence pre-check: the ownership gate raises one uniform error for
        # "unknown competition" and "not your competition", so the response
        # cannot be used to probe which competitions exist or who owns them.
        _require_organiser(cid)
        body = request.get_json(force=True, silent=True) or {}
        _svc().set_rubric(cid, body.get("criteria", []), body.get("judges", 1),
                          body.get("tie_break"))
        return jsonify(_svc().get_competition(cid))

    @app.post("/api/competitions/<cid>/freeze")
    def api_freeze(cid):
        _require_organiser(cid)
        _svc().freeze(cid)
        return jsonify(_svc().get_competition(cid))

    @app.post("/api/competitions/<cid>/entries")
    def api_add_entry(cid):
        if g.account is None:
            abort(401)
        _comp_or_404(cid)
        body = request.get_json(force=True, silent=True) or {}
        e = _svc().add_entry(cid, stable_id=body.get("stable_id", ""),
                             title=body.get("title", ""), summary=body.get("summary", ""),
                             link=body.get("link", ""), private=bool(body.get("private")),
                             submitter_account_id=_account_id())
        return jsonify(e), 201

    @app.post("/api/competitions/<cid>/evaluations")
    def api_evaluate(cid):
        if g.account is None:
            abort(401)
        _comp_or_404(cid)
        body = request.get_json(force=True, silent=True) or {}
        # The judge label is the caller's own membership label, never the body's.
        label = _svc().judge_label(cid, _account_id())
        out = _svc().evaluate(cid, entry_stable_id=body.get("entry", ""),
                              judge=label, scores=body.get("scores", {}),
                              justification=body.get("justification", ""),
                              judge_account_id=_account_id())
        return jsonify(out), 201

    @app.errorhandler(404)
    def _404(e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "not found"}), 404
        return render_template("error.html", code=404,
                               message="That competition does not exist."), 404

    @app.context_processor
    def _inject_auth():
        """Every template can read the current account and the CSRF token."""
        return {"account": g.get("account"), "csrf_token": g.get("csrf_token", "")}


def _register_error_handlers(app: Flask) -> None:
    """One place that turns an exception into an honest response.

    A read-only page must never answer 500 because a stored row is malformed, a
    competition has no rubric yet, or a value is out of range. Those are all
    ordinary, expected states of the domain, so they get a specific message and
    the right status code (4xx). A genuinely unexpected error is logged and shown
    as a generic 500 — never a stack trace, never a raw internal detail.
    """

    def _wants_json() -> bool:
        return request.path.startswith("/api/")

    @app.errorhandler(AuthError)
    def _auth_error(e):
        # The same response an unknown competition gives, so a non-owner cannot
        # learn that a competition exists. HTML: 404 with the identical message.
        # API: 403 (an authenticated client is told it is a permission problem,
        # not a missing route) with a uniform body.
        if _wants_json():
            return jsonify({"error": "not authorised for this competition"}), 403
        return render_template("error.html", code=404,
                               message="That competition does not exist."), 404

    @app.errorhandler(InvitationError)
    def _invitation_error(e):
        if _wants_json():
            return jsonify({"error": "this invitation is not valid"}), 400
        flash(str(e), "error")
        return render_template("error.html", code=400, message=str(e)), 400

    @app.errorhandler(401)
    def _unauth(e):
        if _wants_json():
            return jsonify({"error": "authentication required"}), 401
        flash("Sign in to continue.", "error")
        return redirect(url_for("login", next=request.path))

    @app.errorhandler(ValidationError)
    def _validation(e):
        if _wants_json():
            return jsonify({"error": str(e)}), 400
        flash(str(e), "error")
        return render_template("error.html", code=400, message=str(e)), 400

    @app.errorhandler(ConflictError)
    def _conflict(e):
        # The API's established contract is 400 for a rejected operation (a frozen
        # rubric, a duplicate score); keep it, so an existing client is not broken
        # by a status-code change. The important fix is that this is a clean 4xx,
        # never a 500 from an unhandled database error.
        if _wants_json():
            return jsonify({"error": str(e)}), 400
        flash(str(e), "error")
        return render_template("error.html", code=409, message=str(e)), 409

    @app.errorhandler(IllFormed)
    def _illformed(e):
        msg = f"this rule cannot be compiled into a circuit: {e}"
        if _wants_json():
            return jsonify({"error": msg}), 400
        return render_template("error.html", code=400, message=msg), 400

    @app.errorhandler(413)
    def _too_large(e):
        if _wants_json():
            return jsonify({"error": "request body is too large"}), 413
        return render_template("error.html", code=413,
                               message="That request body is too large."), 413

    @app.errorhandler(405)
    def _method(e):
        if _wants_json():
            return jsonify({"error": "method not allowed"}), 405
        return render_template("error.html", code=405,
                               message="That method is not allowed here."), 405

    @app.errorhandler(500)
    def _500(e):  # pragma: no cover - safety net
        app.logger.exception("unhandled error on %s", request.path)
        if _wants_json():
            return jsonify({"error": "internal error"}), 500
        return render_template("error.html", code=500,
                               message="Something went wrong on our side."), 500

    # A catch-all keeps a surprise error from ever leaking a stack trace in
    # production. It is deliberately NOT registered under TESTING, so a genuine
    # bug still raises into the test runner instead of being hidden as a 500.
    if not app.config.get("TESTING"):
        @app.errorhandler(Exception)
        def _unexpected(e):  # pragma: no cover - production safety net
            app.logger.exception("unhandled error on %s", request.path)
            if _wants_json():
                return jsonify({"error": "internal error"}), 500
            return render_template("error.html", code=500,
                                   message="Something went wrong on our side."), 500


# --------------------------------------------------------------------------- #
# view helpers
# --------------------------------------------------------------------------- #

def _rubric_preview(comp: dict) -> dict | None:
    if not comp.get("rubric"):
        return None
    r = comp["rubric"]
    parts = [f"{c['weight']}×({c['name']})" for c in r["criteria"]]
    n_crit = len(r["criteria"])
    return {
        "criteria": r["criteria"],
        "judges": r["judges"],
        "tie_break": r["tie_break"],
        "sentence": (f"Each entry is scored by {r['judges']} judge"
                     f"{'s' if r['judges'] != 1 else ''} on {n_crit} "
                     f"{'criteria' if n_crit != 1 else 'criterion'}. "
                     f"The total is {' + '.join(parts)}, summed across the panel."),
    }


def _max_total(comp: dict) -> int | None:
    if not comp.get("rubric"):
        return None
    r = comp["rubric"]
    return sum(c["weight"] * c["max_score"] * r["judges"] for c in r["criteria"])


def _featured_rubric(comp: dict | None) -> dict | None:
    """Read the frozen rubric of the featured competition for the landing ledger
    preview. Returns criteria with a weight share (for the meter) and the max
    possible total. Read-only; never invents a rule."""
    if not comp or not comp.get("rubric_json"):
        return None
    try:
        r = json.loads(comp["rubric_json"])
    except (TypeError, ValueError):
        return None
    criteria = r.get("criteria") or []
    if not criteria:
        return None
    weight_sum = sum(int(c.get("weight") or 0) for c in criteria) or 1
    for c in criteria:
        c["share"] = round(100 * int(c.get("weight") or 0) / weight_sum)
    judges = int(r.get("judges") or 1)
    max_total = sum(int(c.get("weight") or 0) * int(c.get("max_score") or 0)
                    for c in criteria) * judges
    return {"criteria": criteria, "judges": judges, "max_total": max_total,
            "count": len(criteria)}


def _judge_matrix(cid: str) -> dict:
    """Which (judge, entry) pairs already have a committed score."""
    svc = g.app_service
    comp = svc.get_competition(cid)
    entries = svc.list_entries(cid)
    evals = svc.store.list_evaluations(cid)
    by_id = {e["id"]: e["stable_id"] for e in entries}
    done = {}
    for ev in evals:
        sid = by_id.get(ev["entry_id"])
        if sid is None:
            continue
        done[(ev["judge"], sid)] = {
            "commitment": ev["commitment"], "created_at": ev["created_at"]}
    conflicts = {(c["judge"], c["entry_id"]) for c in svc.store.list_conflicts(cid)}
    return {"done": done, "conflicts": conflicts,
            "judges": json.loads(comp["judges_json"]),
            "entries": [e["stable_id"] for e in entries],
            "id_by_stable": {e["stable_id"]: e["id"] for e in entries}}


def _publish_message(result: dict) -> str:
    mode = result["mode"]
    if mode == "onchain_pending_signature":
        return ("Publication prepared. Signing is a funded transaction your wallet "
                "must authorise — nothing has been sent.")
    if mode == "onchain_verified":
        return "Published on X Layer."
    if mode == "local_only":
        return "Recorded locally only: " + (result.get("note") or "no chain publication")
    return result.get("note", "")


def _eval_call(comp: dict, entry: str) -> str:
    pub = comp.get("publication") or {}
    processor = pub.get("processor") or "<processor>"
    cid = pub.get("circuit_id")
    cid_s = str(cid) if cid is not None else "<circuitId>"
    return (f'cast call {processor} \\\n'
            f'  "eval(uint256,bytes)(bytes)" {cid_s} <inputs>')


# WSGI entry point for `flask run` / gunicorn.
app = create_app()


def main() -> None:
    """Run the development server (``tapejudge`` console script)."""
    host = os.environ.get("TAPEJUDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("TAPEJUDGE_PORT", "8080"))
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()
