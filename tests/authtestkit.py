"""Shared helpers for the HTTP tests.

The application now requires authentication and a CSRF token on every write.
These helpers give each test a real account, a real logged-in session (built
through the same ``auth.rotate_session`` the login route uses) and a CSRF-aware
request helper, so the existing behaviour tests keep exercising the real routes
rather than a bypass.

Nothing here weakens a check: it only performs the steps a browser would.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tapejudge import auth
from tapejudge.service import ConflictError

PASSWORD = "correct-horse-battery-staple"


def ensure_account(app, email, *, password=PASSWORD, name="Test Account"):
    """Create the account if it does not exist; return its public view."""
    svc = app.config["SERVICE"]
    try:
        return svc.register_account(email=email, password=password, display_name=name)
    except ConflictError:
        return svc.authenticate(email=email, password=password)


def login(client, app, email, *, password=PASSWORD, name="Test Account"):
    """Log a real session in for ``email`` (creating the account if needed)."""
    acct = ensure_account(app, email, password=password, name=name)
    assert acct is not None, f"could not log in {email}"
    with client.session_transaction() as sess:
        sess.clear()
        auth.rotate_session(sess, acct)
    return acct


def logout(client):
    with client.session_transaction() as sess:
        sess.clear()


def csrf(client) -> str:
    with client.session_transaction() as sess:
        token = sess.get("csrf", "")
    if not token:
        # Prime a session so the app mints a token, then read it.
        client.get("/")
        with client.session_transaction() as sess:
            token = sess.get("csrf", "")
    return token


def request(client, method, url, *, form=None, json=None, headers=None):
    """A request that carries the current CSRF token (form field or header)."""
    headers = dict(headers or {})
    headers.setdefault("X-CSRF-Token", csrf(client))
    if form is not None:
        form = dict(form)
        form.setdefault("csrf_token", csrf(client))
    return client.open(url, method=method, data=form, json=json, headers=headers)


def post(client, url, **kw):
    return request(client, "POST", url, **kw)


def get(client, url, **kw):
    return client.get(url, **kw)


def build_competition(app, client, *, judges=("j1", "j2"),
                      criteria=({"name": "demo", "weight": 3, "max_score": 10},
                                {"name": "tests", "weight": 2, "max_score": 10}),
                      freeze=True, entries=("alpha", "beta"), evaluate=True,
                      scores=None, org="org@test.dev", entrant="entrant@test.dev",
                      name="Synthetic Web Round", tie_break=None, invite=True):
    """Drive the whole flow through the real HTTP routes with real accounts.

    Returns ``(cid, judge_emails)``. The organiser creates and freezes the
    competition, an entrant submits entries, and each judge accepts an invitation
    and submits scores under their own identifier.
    """
    login(client, app, org, name="Organiser")
    cid = post(client, "/api/competitions",
               json={"name": name, "organiser": "SYNTH", "judges": list(judges)}
               ).get_json()["id"]
    r = post(client, f"/api/competitions/{cid}/rubric",
             json={"criteria": list(criteria), "judges": len(judges),
                   "tie_break": tie_break or [c["name"] for c in criteria]})
    assert r.status_code == 200, r.data
    if freeze:
        r = post(client, f"/api/competitions/{cid}/freeze")
        assert r.status_code == 200, r.data
    # Entries are submitted by an authenticated entrant.
    login(client, app, entrant, name="Entrant")
    for e in entries:
        post(client, f"/api/competitions/{cid}/entries",
             json={"stable_id": e, "title": e.title()})
    judge_emails = {}
    if invite:
        for j in judges:
            email = f"{j}@test.dev"
            judge_emails[j] = email
            ensure_account(app, email, name=f"Judge {j}")
            login(client, app, org, name="Organiser")
            tok = app.config["SERVICE"].invite_judge(
                cid, email=email, label=j, organiser_account_id=_account_id(app, org))
            login(client, app, email, name=f"Judge {j}")
            post(client, f"/invitations/{tok['token']}")
    if freeze and evaluate:
        scores = scores or _default_scores(judges, entries, criteria)
        for j in judges:
            login(client, app, judge_emails.get(j, f"{j}@test.dev"), name=f"Judge {j}")
            for e, per in scores.get(j, {}).items():
                post(client, f"/api/competitions/{cid}/evaluations",
                     json={"entry": e, "scores": per})
    # Leave the session as the organiser, the most common next actor in a test.
    login(client, app, org, name="Organiser")
    return cid, judge_emails


def _account_id(app, email):
    acct = app.config["SERVICE"].store.get_account_by_email(email)
    return acct["id"] if acct else None


def _default_scores(judges, entries, criteria):
    """A deterministic score set: judge index and entry index drive the value."""
    out = {}
    for ji, j in enumerate(judges):
        per = {}
        for ei, e in enumerate(entries):
            per[e] = {c["name"]: min(c["max_score"], 6 + ji + ei) for c in criteria}
        out[j] = per
    return out
