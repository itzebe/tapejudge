"""TapeJudge application service.

The domain layer between the HTTP app and the verified scoring core. It owns the
rules that make the product's promise true:

* **A rubric cannot change after it is frozen.** ``set_rubric`` only works while
  the competition is a draft; ``freeze`` moves it to a state the store refuses to
  update.
* **A result is never stored.** ``results`` and ``verify`` recompile the frozen
  rubric and re-run the circuit from the stored evaluations every time. There is
  no ``ranking`` column to tamper with.
* **An evaluation is immutable.** The store's UNIQUE(competition, entry, judge)
  constraint plus the commitment make a second, different submission a hard error.
* **Local vs on-chain is never blurred.** A competition records exactly how its
  rule was published (``publish_mode``), and the API surfaces that verbatim.

The scoring itself is the Mission 3 core (``rubric.compile_rubric`` /
``engine.Competition``); this module does not reimplement any arithmetic.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import secrets

from . import auth as _auth
from .engine import Competition, JudgeEvaluation, ScoringError
from .netlist import encode
from .rubric import Criterion, Rubric, compile_rubric
from .store import DuplicateError, Store
from .xlayer import XLayerClient

# Publication modes. Only the first two are "published"; the rest are honest
# states, never dressed up as an on-chain circuit.
PUBLISH_NOT_PUBLISHED = "not_published"
PUBLISH_LOCAL_ONLY = "local_only"
PUBLISH_ONCHAIN_VERIFIED = "onchain_verified"
PUBLISH_ONCHAIN_PENDING_SIGNATURE = "onchain_pending_signature"

# Judge invitations expire; an old link is not a permanent key.
INVITATION_TTL_SECONDS = 7 * 24 * 3600
MIN_PASSWORD_LENGTH = 10


class ValidationError(ValueError):
    pass


class ConflictError(RuntimeError):
    pass


class AuthError(RuntimeError):
    """The caller is not authenticated, or not authorised for this object.

    Deliberately a single error type for "not allowed": the response must not
    tell an attacker whether an object exists, whether they have the wrong role,
    or whether a competition id is merely unknown. All three are one 403/404.
    """


class InvitationError(RuntimeError):
    """An invitation is invalid, expired, revoked, or already accepted."""


def _utc_after(seconds: int) -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + seconds))


def _utc_now() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _valid_email(email: str) -> bool:
    email = (email or "").strip()
    if not email or len(email) > 200 or " " in email:
        return False
    local, _, domain = email.partition("@")
    return bool(local) and "." in domain and not domain.startswith(".") \
        and not domain.endswith(".")


# A panel larger than this cannot be judged by hand and only inflates the
# circuit; the MVP caps it so a rubric stays compilable and a page stays useful.
MAX_CRITERIA = 32
MAX_JUDGES = 25


def _as_int(value) -> int:
    """Parse a whole number, rejecting booleans, floats and non-integers.

    ``True`` is an ``int`` in Python and ``int(5.9)`` silently truncates, so a
    naive ``int(value)`` accepts ``true`` and turns ``5.9`` into ``5`` — a score
    the judge never submitted. Both must be rejected, not coerced.
    """
    if isinstance(value, bool):
        raise ValueError("a boolean is not a whole number")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise ValueError("a fractional number is not a whole number")
    if isinstance(value, str):
        s = value.strip()
        if s and (s.lstrip("+-").isdigit()):
            return int(s)
        raise ValueError("not a whole number")
    raise ValueError("not a whole number")


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def rubric_from_request(competition: str, criteria: list[dict], judges: int,
                        tie_break: list[str] | None) -> Rubric:
    """Build a validated Rubric from a request body, with clear error messages."""
    if not isinstance(criteria, list) or not criteria:
        raise ValidationError("a rubric needs at least one criterion")
    if len(criteria) > MAX_CRITERIA:
        raise ValidationError(f"a rubric supports at most {MAX_CRITERIA} criteria")
    seen = set()
    built = []
    for i, c in enumerate(criteria):
        name = str(c.get("name", "")).strip()
        if not name:
            raise ValidationError(f"criterion {i + 1}: a name is required")
        if len(name) > 60:
            raise ValidationError(f"criterion {i + 1}: name is too long (max 60 characters)")
        if name in seen:
            raise ValidationError(f"criterion name {name!r} is used more than once")
        seen.add(name)
        try:
            weight = _as_int(c.get("weight"))
        except (TypeError, ValueError) as e:
            raise ValidationError(
                f"criterion {name!r}: weight must be a whole number") from e
        try:
            max_score = _as_int(c.get("max_score"))
        except (TypeError, ValueError) as e:
            raise ValidationError(
                f"criterion {name!r}: maximum score must be a whole number") from e
        if weight < 1:
            raise ValidationError(f"criterion {name!r}: weight must be at least 1")
        if weight > 1_000_000:
            raise ValidationError(f"criterion {name!r}: weight is unreasonably large")
        if max_score < 1:
            raise ValidationError(f"criterion {name!r}: maximum score must be at least 1")
        if max_score > 1_000_000:
            raise ValidationError(f"criterion {name!r}: maximum score is unreasonably large")
        built.append(Criterion(name, weight, max_score))
    try:
        judges = _as_int(judges)
    except (TypeError, ValueError) as e:
        raise ValidationError("panel size must be a whole number") from e
    if judges < 1:
        raise ValidationError("the panel must have at least one judge")
    if judges > MAX_JUDGES:
        raise ValidationError(f"the MVP supports at most {MAX_JUDGES} judges on a panel")
    tb = tuple(tie_break) if tie_break else ()
    for n in tb:
        if n not in seen:
            raise ValidationError(f"tie-break names unknown criterion {n!r}")
    try:
        rubric = Rubric(competition, tuple(built), judges, tb)
    except ValueError as e:  # from the core's own validation
        raise ValidationError(str(e)) from e
    return rubric


class TapeJudgeService:
    def __init__(self, store: Store, *, chain_client=None):
        self.store = store
        # The chain client is injected so tests can pass a stub; the default is
        # the real read-only X Layer client.
        self.chain = chain_client if chain_client is not None else XLayerClient()

    # -- competitions ------------------------------------------------------- #
    def create_competition(self, *, name, description="", organiser="", opens_at="",
                           closes_at="", judges, prize_note="",
                           owner_account_id=None) -> str:
        if not str(name).strip():
            raise ValidationError("a competition needs a name")
        if not judges:
            raise ValidationError("a competition needs at least one judge")
        judges = [str(j).strip() for j in judges if str(j).strip()]
        if not judges:
            raise ValidationError("a competition needs at least one judge")
        if len(judges) > MAX_JUDGES:
            raise ValidationError(
                f"the MVP supports at most {MAX_JUDGES} judges on a panel")
        if len(set(judges)) != len(judges):
            raise ValidationError("judge identifiers must be unique")
        cid = self.store.create_competition(
            name=name.strip(), description=description.strip(), organiser=organiser.strip(),
            opens_at=opens_at.strip(), closes_at=closes_at.strip(),
            judges=judges, prize_note=prize_note.strip(),
            owner_account_id=owner_account_id)
        if owner_account_id:
            # The creator is the organiser of record: this membership is what an
            # authorisation check reads. It is not derived from the request again.
            self.store.add_membership(account_id=owner_account_id, competition_id=cid,
                                      role=_auth.ROLE_ORGANISER, label="")
        return cid

    def set_rubric(self, cid: str, criteria: list[dict], judges: int,
                   tie_break: list[str] | None = None) -> dict:
        comp = self._require(cid)
        if comp["status"] != "draft":
            raise ConflictError("the rubric is frozen and cannot be replaced")
        rubric = rubric_from_request(comp["name"], criteria, judges, tie_break)
        # The panel identifiers are the only identities allowed to score, so the
        # frozen panel size must equal the number of evaluations each entry needs.
        # If they disagree, an entry could be "complete" with fewer scores than
        # the panel (or impossible to complete), which would corrupt a settlement.
        panel = json.loads(comp["judges_json"])
        if len(panel) != rubric.judges:
            raise ValidationError(
                f"the rubric needs {rubric.judges} evaluations per entry, but the "
                f"panel has {len(panel)} judge(s). Set the panel size to match the "
                "judges, or edit the judge list.")
        try:
            circuit, meta = compile_rubric(rubric)
        except Exception as e:  # IllFormed and friends: a rubric that cannot compile
            raise ValidationError(
                f"this rubric cannot be compiled into a circuit: {e}") from e
        net = encode(circuit.elements)
        self.store.set_rubric(
            cid, rubric_json=rubric.to_json(), rubric_digest=rubric.digest(),
            netlist_hex="0x" + net.hex(), netlist_sha256=hashlib.sha256(net).hexdigest(),
            n_in=meta["n_in"], n_out=meta["n_out"], gates=circuit.n_gates,
            tie_break=rubric.tie_break)
        self.store.log_event(cid, "rubric_configured",
                             {"digest": rubric.digest(), "gates": circuit.n_gates})
        return self.get_competition(cid)

    def freeze(self, cid: str) -> dict:
        comp = self._require(cid)
        if comp["status"] != "draft":
            raise ConflictError("competition is already frozen")
        if not comp["rubric_json"]:
            raise ValidationError("configure a rubric before freezing")
        commit_key = secrets.token_hex(32)
        self.store.freeze(cid, commit_key)
        self.store.log_event(cid, "rubric_frozen",
                             {"rubric_digest": comp["rubric_digest"],
                              "netlist_sha256": comp["netlist_sha256"]})
        return self.get_competition(cid)

    def publish(self, cid: str) -> dict:
        """Attempt to publish the frozen circuit.

        The MVP records the frozen rubric and its netlist locally and reports the
        exact publication state. A real on-chain publication needs a funded
        wallet and a signed transaction, which is an authorisation boundary: when
        one is configured the service builds the *unsigned* ``tapeout`` call and
        stops, reporting ``onchain_pending_signature``. It never sends a
        transaction and never labels a local circuit as on-chain.
        """
        comp = self._require(cid)
        if comp["status"] == "draft":
            raise ValidationError("freeze the rubric before publishing")
        netlist = bytes.fromhex(comp["netlist_hex"][2:])
        prepared = self.chain.prepare_tapeout(netlist, comp["n_in"], comp["n_out"])
        self.store.record_publication(
            cid, chain_id=prepared["chain_id"],
            processor=prepared["processor"] or None,
            circuit_id=prepared["circuit_id"],
            tx_hash=None, mode=prepared["mode"],
            fee_wei=prepared.get("fee_wei") if prepared.get("fee_known") else None)
        self.store.log_event(cid, "publication_recorded",
                             {"mode": prepared["mode"], "chain_id": prepared["chain_id"]})
        return prepared

    # -- entries ------------------------------------------------------------ #
    def add_entry(self, cid: str, *, stable_id, title, summary="", link="",
                  evidence=None, private=False, submitter_account_id=None) -> dict:
        comp = self._require(cid)
        if comp["status"] == "draft":
            raise ConflictError("publish the rubric before accepting entries")
        if comp["status"] == "settled":
            raise ConflictError("competition is settled; entries are closed")
        if not str(stable_id).strip():
            raise ValidationError("an entry needs a stable identifier")
        if self.store.get_entry_by_stable(cid, stable_id.strip()):
            raise ConflictError(f"entry {stable_id!r} already exists in this competition")
        evidence = evidence or {}
        content_hash = hashlib.sha256(_canonical({
            "stable_id": stable_id, "title": title, "summary": summary,
            "link": link, "evidence": evidence})).hexdigest()
        try:
            eid = self.store.add_entry(
                cid, stable_id=stable_id.strip(), title=title.strip(), summary=summary.strip(),
                link=link.strip(), evidence=evidence, content_hash=content_hash, private=private,
                submitter_account_id=submitter_account_id)
        except DuplicateError as e:
            raise ConflictError(
                f"entry {stable_id!r} already exists in this competition") from e
        if submitter_account_id and not self.store.get_membership(
                cid, submitter_account_id, _auth.ROLE_ENTRANT):
            # The entrant membership records who submitted. It is what the
            # private-entry view checks; it is never read from the request.
            with contextlib.suppress(DuplicateError):
                self.store.add_membership(account_id=submitter_account_id,
                                          competition_id=cid,
                                          role=_auth.ROLE_ENTRANT, label="")
        return self.get_entry(cid, eid, viewer_account_id=submitter_account_id)

    def get_entry(self, cid: str, entry_id: str, *, viewer_account_id=None) -> dict:
        see_all = self.can_view_private(cid, viewer_account_id)
        for e in self.store.list_entries(cid):
            if e["id"] == entry_id:
                return self._entry_view(e, see_all_private=see_all,
                                        viewer_account_id=viewer_account_id)
        raise KeyError(entry_id)

    def list_entries(self, cid: str, *, include_private_fields=False,
                     viewer_account_id=None) -> list[dict]:
        # An organiser of this competition sees every private entry; an entrant
        # sees their own. This is the single place the rule is resolved, so no
        # caller has to remember to pass it.
        see_all = include_private_fields or self.can_view_private(cid, viewer_account_id)
        return [self._entry_view(e, see_all_private=see_all,
                                 viewer_account_id=viewer_account_id)
                for e in self.store.list_entries(cid)]

    # -- evaluation --------------------------------------------------------- #
    def evaluate(self, cid: str, *, entry_stable_id, judge, scores, justification="",
                 judge_account_id=None) -> dict:
        comp = self._require(cid)
        if comp["status"] not in ("frozen", "open", "judging"):
            raise ConflictError("this competition is not open for judging")
        judge = str(judge).strip()
        if judge not in json.loads(comp["judges_json"]):
            raise ValidationError(f"{judge!r} is not on the panel for this competition")
        if judge_account_id is not None:
            # The authenticated account must hold the judge membership for the
            # label it is scoring under. A judge can never score under another
            # judge's label by putting it in the request.
            m = self.store.get_membership(cid, judge_account_id, _auth.ROLE_JUDGE)
            if m is None or m["label"] != judge:
                raise AuthError(
                    "this account is not the invited judge for that identifier")
        entry = self.store.get_entry_by_stable(cid, entry_stable_id)
        if entry is None:
            raise ValidationError(f"unknown entry {entry_stable_id!r}")
        if self._has_conflict(cid, judge, entry["id"]):
            raise ConflictError(
                f"{judge!r} has declared a conflict of interest for {entry_stable_id!r} "
                "and may not score it")
        if self.store.has_evaluation(cid, entry["id"], judge):
            raise ConflictError(
                f"{judge!r} has already scored {entry_stable_id!r}; "
                "a submitted score cannot change")
        rubric = self._rubric(comp)
        # Validate against the frozen rubric *before* touching the store.
        normalised = self._validate_scores(rubric, scores)
        commitment = self._commitment(comp, judge, entry_stable_id, normalised)
        try:
            self.store.add_evaluation(
                cid, entry_id=entry["id"], judge=judge, scores=normalised,
                justification=justification.strip(), commitment=commitment,
                nonce="", judge_account_id=judge_account_id)
        except DuplicateError as e:
            # The has_evaluation check above and this insert are not atomic; two
            # concurrent requests can both pass the check. The DB constraint is
            # the real guard, so translate its failure into the same conflict.
            raise ConflictError(
                f"{judge!r} has already scored {entry_stable_id!r}; "
                "a submitted score cannot change") from e
        return {"judge": judge, "entry": entry_stable_id, "scores": normalised,
                "commitment": commitment}

    def declare_conflict(self, cid: str, *, judge, entry_stable_id, reason="",
                         judge_account_id=None) -> dict:
        comp = self._require(cid)
        judge = str(judge).strip()
        if judge not in json.loads(comp["judges_json"]):
            raise ValidationError(f"{judge!r} is not on the panel")
        if judge_account_id is not None:
            m = self.store.get_membership(cid, judge_account_id, _auth.ROLE_JUDGE)
            if m is None or m["label"] != judge:
                raise AuthError(
                    "this account is not the invited judge for that identifier")
        entry = self.store.get_entry_by_stable(cid, entry_stable_id)
        if entry is None:
            raise ValidationError(f"unknown entry {entry_stable_id!r}")
        self.store.declare_conflict(cid, judge=judge, entry_id=entry["id"], reason=reason)
        return {"judge": judge, "entry": entry_stable_id, "reason": reason}

    def add_correction(self, cid: str, *, note, actor="") -> dict:
        """Record a correction or appeal note. Append-only; inputs are untouched."""
        self._require(cid)
        if not str(note).strip():
            raise ValidationError("a correction note cannot be empty")
        self.store.log_event(cid, "correction_recorded",
                             {"note": note.strip(), "actor": str(actor).strip()})
        return {"note": note.strip(), "actor": str(actor).strip()}

    def set_judges(self, cid: str, judges: list[str], organiser_account_id) -> None:
        """Replace the declared panel while the competition is a draft.

        This is an organiser action, so it goes through the same ownership gate
        as every other organiser operation. The panel is frozen with the rubric;
        it cannot move afterwards.
        """
        self.require_organiser(cid, organiser_account_id)
        judges = [str(j).strip() for j in (judges or []) if str(j).strip()]
        if not judges:
            raise ValidationError("a competition needs at least one judge")
        if len(judges) > MAX_JUDGES:
            raise ValidationError(f"the MVP supports at most {MAX_JUDGES} judges on a panel")
        if len(set(judges)) != len(judges):
            raise ValidationError("judge identifiers must be unique")
        # A panel member who has already accepted an invitation must not be
        # silently dropped: revoke them explicitly instead.
        joined = {m["label"] for m in self.store.list_memberships(cid)
                  if m["role"] == _auth.ROLE_JUDGE}
        if not joined.issubset(set(judges)):
            raise ConflictError(
                "a judge who has already accepted cannot be removed here; "
                "revoke them explicitly first")
        try:
            self.store.set_judges(cid, judges)
        except PermissionError as e:
            raise ConflictError(str(e)) from e
        self.store.log_event(cid, "panel_updated", {"judges": judges})

    # -- accounts and authentication ---------------------------------------- #
    def register_account(self, *, email, password, display_name="") -> dict:
        email = (email or "").strip().lower()
        if not _valid_email(email):
            raise ValidationError("enter a valid email address")
        if len(str(password)) < MIN_PASSWORD_LENGTH:
            raise ValidationError(
                f"a password must be at least {MIN_PASSWORD_LENGTH} characters")
        if self.store.get_account_by_email(email):
            raise ConflictError("an account with that email already exists")
        try:
            aid = self.store.create_account(
                email=email, display_name=str(display_name or "").strip()[:120],
                password_hash=_auth.hash_password(password))
        except DuplicateError as e:
            raise ConflictError("an account with that email already exists") from e
        self.store.log_event(aid, "account_created", {"email": email})
        return _auth.public_account(self.store.get_account(aid))

    def authenticate(self, *, email, password) -> dict:
        """Return the account on a correct password, else ``None``.

        The caller must return the *same* message whether the account is missing
        or the password is wrong; this method does not distinguish them either.
        """
        email = (email or "").strip().lower()
        acct = self.store.get_account_by_email(email)
        if acct is None:
            # Still spend a hash so timing does not reveal that the email is
            # unknown. A dummy hash of the right shape is enough.
            _auth.verify_password(
                "scrypt:32768:8:1$AAAAAAAAAAAAAAAA$" + "0" * 128, password or "")
            return None
        if not _auth.verify_password(acct["password_hash"], password or ""):
            return None
        return _auth.public_account(acct)

    def change_password(self, account_id: str, new_password: str) -> None:
        if len(str(new_password)) < MIN_PASSWORD_LENGTH:
            raise ValidationError(
                f"a password must be at least {MIN_PASSWORD_LENGTH} characters")
        self.store.set_password_hash(account_id, _auth.hash_password(new_password))
        # Invalidate every existing session for this account.
        self.store.bump_session_epoch(account_id)

    def logout_everywhere(self, account_id: str) -> None:
        self.store.bump_session_epoch(account_id)

    # -- authorisation (the single place the policy lives) ------------------ #
    def _role_for(self, cid: str, account_id: str | None, role: str) -> dict | None:
        if not account_id:
            return None
        return self.store.get_membership(cid, account_id, role)

    def is_organiser(self, cid: str, account_id: str | None) -> bool:
        return self._role_for(cid, account_id, _auth.ROLE_ORGANISER) is not None

    def judge_label(self, cid: str, account_id: str | None) -> str | None:
        """The judge identifier this account may score under, or ``None``."""
        m = self._role_for(cid, account_id, _auth.ROLE_JUDGE)
        return m["label"] if m else None

    def is_entrant(self, cid: str, account_id: str | None) -> bool:
        return self._role_for(cid, account_id, _auth.ROLE_ENTRANT) is not None

    def can_view_private(self, cid: str, account_id: str | None) -> bool:
        """An organiser sees every private entry; an entrant sees their own."""
        return self.is_organiser(cid, account_id) or self.is_entrant(cid, account_id)

    def require_organiser(self, cid: str, account_id: str | None) -> dict:
        """The one gate for every organiser action on a specific competition.

        Raises the same ``AuthError`` for an unknown competition, a non-owner and
        a missing account, so the response cannot be used to probe which
        competitions exist or who owns them.
        """
        if not account_id:
            raise AuthError("sign in to manage a competition")
        comp = self.store.get_competition(cid)
        if comp is None:
            raise AuthError("no such competition, or you are not its organiser")
        if comp.get("owner_account_id") != account_id:
            raise AuthError("no such competition, or you are not its organiser")
        return comp

    # -- judge invitations -------------------------------------------------- #
    def invite_judge(self, cid: str, *, email, label, organiser_account_id) -> dict:
        """Create a single-use judge invitation; returns the plaintext token once.

        Only the SHA-256 of the token is stored, so a database read cannot be
        replayed as an invitation. The token is scoped to one competition and one
        role, and it expires.
        """
        self.require_organiser(cid, organiser_account_id)
        email = (email or "").strip().lower()
        if not _valid_email(email):
            raise ValidationError("enter a valid email address for the judge")
        label = str(label or "").strip()
        if not label:
            raise ValidationError("a judge identifier is required")
        if len(label) > 60:
            raise ValidationError("the judge identifier is too long (max 60 characters)")
        # The identifier must already be on the declared panel: an invitation can
        # only bind an account to an identity the organiser already committed to.
        if label not in json.loads(self.store.get_competition(cid)["judges_json"]):
            raise ValidationError(
                f"{label!r} is not on this competition's declared panel")
        # A label already used by a different judge would make identity ambiguous.
        existing = self.store.find_membership_by_label(cid, label)
        if existing is not None:
            raise ConflictError(f"the judge identifier {label!r} is already in use here")
        # A pending invitation for the same label would be a second, competing
        # identity: refuse it rather than leave two live tokens for one label.
        for inv in self.store.list_invitations(cid):
            if inv["label"] == label and self.invitation_status(inv) == "pending":
                raise ConflictError(
                    f"an invitation for {label!r} is already outstanding")
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        self.store.create_invitation(
            competition_id=cid, email=email, role=_auth.ROLE_JUDGE, label=label,
            token_hash=token_hash, expires_at=_utc_after(INVITATION_TTL_SECONDS),
            invited_by=organiser_account_id)
        return {"token": token, "email": email, "label": label,
                "expires_at": _utc_after(INVITATION_TTL_SECONDS)}

    def invitation_by_token(self, token: str) -> dict | None:
        if not token:
            return None
        return self.store.get_invitation_by_token_hash(
            hashlib.sha256(token.encode()).hexdigest())

    def invitation_status(self, inv: dict) -> str:
        if inv is None:
            return "unknown"
        if inv.get("revoked_at"):
            return "revoked"
        if inv.get("accepted_at"):
            return "accepted"
        if inv.get("expires_at", "") <= _utc_now():
            return "expired"
        return "pending"

    def accept_invitation(self, token: str, *, account_id) -> dict:
        """Accept a judge invitation as the authenticated account.

        Every failure mode (unknown token, expired, revoked, already accepted,
        wrong email) raises the same ``InvitationError`` so the response does not
        reveal which invitation exists.
        """
        inv = self.invitation_by_token(token)
        if inv is None:
            raise InvitationError("this invitation is not valid")
        if not account_id:
            raise InvitationError("sign in to accept this invitation")
        acct = self.store.get_account(account_id)
        if acct is None:
            raise InvitationError("this invitation is not valid")
        status = self.invitation_status(inv)
        if status != "pending":
            raise InvitationError("this invitation is not valid")
        # The invitation is addressed to an email; the accepting account must
        # match it, so a leaked link cannot be redeemed by a stranger.
        if acct["email"].strip().lower() != inv["email"].strip().lower():
            raise InvitationError("this invitation is not valid")
        cid = inv["competition_id"]
        if self.store.get_membership(cid, account_id, _auth.ROLE_JUDGE):
            raise InvitationError("this invitation is not valid")
        comp = self.store.get_competition(cid)
        if comp is None or inv["label"] not in json.loads(comp["judges_json"]):
            raise InvitationError("this invitation is not valid")
        if not self.store.accept_invitation(inv["id"], account_id):
            # Lost a concurrent race to accept the same single-use token.
            raise InvitationError("this invitation is not valid")
        try:
            self.store.add_membership(account_id=account_id, competition_id=cid,
                                      role=_auth.ROLE_JUDGE, label=inv["label"])
        except DuplicateError as e:
            raise InvitationError(
                "that judge identifier was just taken; ask for a new invitation") from e
        self.store.log_event(cid, "judge_invitation_accepted",
                             {"label": inv["label"], "account": account_id})
        return {"competition_id": cid, "label": inv["label"], "role": _auth.ROLE_JUDGE}

    def list_invitations(self, cid: str, organiser_account_id) -> list[dict]:
        self.require_organiser(cid, organiser_account_id)
        out = []
        for inv in self.store.list_invitations(cid):
            out.append({
                "id": inv["id"], "email": inv["email"], "label": inv["label"],
                "status": self.invitation_status(inv),
                "created_at": inv["created_at"], "expires_at": inv["expires_at"],
            })
        return out

    def revoke_judge(self, cid: str, *, label, organiser_account_id) -> None:
        self.require_organiser(cid, organiser_account_id)
        m = self.store.find_membership_by_label(cid, label)
        if m is None:
            raise ValidationError(f"no judge with identifier {label!r} on this panel")
        self.store.remove_membership(m["id"])
        self.store.log_event(cid, "judge_revoked", {"label": label})

    # -- results and verification ------------------------------------------ #
    def results(self, cid: str) -> dict:
        """Recompute the ranking from the frozen rubric and stored evaluations.

        Nothing is read from a stored result: there is no stored result.
        """
        comp = self._require(cid)
        rubric = self._rubric(comp)
        entries = self.store.list_entries(cid)
        evals = self.store.list_evaluations(cid)
        comp_engine = Competition(rubric=rubric,
                                  entry_ids=tuple(e["stable_id"] for e in entries))
        by_id = {e["id"]: e["stable_id"] for e in entries}
        per_entry: dict[str, list[dict]] = {}
        valid_counts: dict[str, int] = {}
        malformed = []
        for ev in evals:
            sid = by_id.get(ev["entry_id"])
            if sid is None:
                # An evaluation whose entry no longer exists: report it, never
                # crash and never silently drop it.
                malformed.append({"entry": ev["entry_id"], "judge": ev["judge"],
                                  "error": "evaluation references an unknown entry"})
                continue
            per_entry.setdefault(sid, []).append(ev)
        for sid, evs in per_entry.items():
            for ev in evs:
                # A malformed stored row is reported below, never scored, and
                # never allowed to raise out of this read-only path.
                try:
                    scores = json.loads(ev["scores_json"])
                except (TypeError, ValueError):
                    malformed.append({"entry": sid, "judge": ev["judge"],
                                      "error": "stored score is not valid JSON"})
                    continue
                try:
                    scores = self._validate_scores(rubric, scores)
                except ValidationError as e:
                    malformed.append({"entry": sid, "judge": ev["judge"],
                                      "error": str(e)})
                    continue
                try:
                    comp_engine.submit(JudgeEvaluation(ev["judge"], sid, scores))
                    valid_counts[sid] = valid_counts.get(sid, 0) + 1
                except ScoringError as e:
                    malformed.append({"entry": sid, "judge": ev["judge"],
                                      "error": str(e)})
        result = comp_engine.rank()
        incomplete = []
        for e in entries:
            have = valid_counts.get(e["stable_id"], 0)
            if have < rubric.judges:
                incomplete.append({"entry": e["stable_id"], "received": have,
                                   "required": rubric.judges})
        return {
            "competition": comp["name"],
            "rubric_digest": comp["rubric_digest"],
            "ranking": result["ranking"],
            "invalid": [r["entry"] for r in result["invalid"]],
            "errors": [r for r in result["all"] if "error" in r] + malformed,
            "incomplete": incomplete,
            "entry_count": len(entries),
            "evaluation_count": len(evals),
            "identity": self.identity_report(evals),
        }

    def identity_report(self, evals: list[dict]) -> dict:
        """Classify stored evaluations by whether the judge identity is proven.

        An evaluation recorded before authentication existed has no
        ``judge_account_id``. It is never *silently* treated as authenticated:
        it is counted as ``unauthenticated`` and named, so a reader can see
        exactly which scores rest on an unverified identifier. This is the honest
        half of the migration — old strings are preserved, not promoted.
        """
        authenticated = [e for e in evals if e.get("judge_account_id")]
        unauthenticated = [e for e in evals if not e.get("judge_account_id")]
        return {
            "authenticated": len(authenticated),
            "unauthenticated": len(unauthenticated),
            "total": len(evals),
            "unauthenticated_judges": sorted({e["judge"] for e in unauthenticated}),
            "note": ("Scores marked unauthenticated were recorded before account "
                     "authentication and carry only a judge identifier, not a "
                     "verified account. They are reproduced unchanged, and the "
                     "classification is shown rather than hidden."),
        }

    def verify(self, cid: str, *, live_chain=False) -> dict:
        """Independently reproduce the ranking and check the frozen rule.

        ``live_chain`` additionally reads the published circuit from X Layer (a
        free ``eval`` read) and compares. It is only meaningful when the
        competition was actually published on chain.
        """
        from .verifier import verify_ranking

        comp = self._require(cid)
        rubric = self._rubric(comp)
        entries = self.store.list_entries(cid)
        evals = self.store.list_evaluations(cid)
        by_id = {e["id"]: e["stable_id"] for e in entries}
        raw = []
        malformed = []
        for ev in evals:
            sid = by_id.get(ev["entry_id"])
            if sid is None:
                malformed.append({"judge": ev["judge"], "entry": ev["entry_id"],
                                  "error": "evaluation references an unknown entry"})
                continue
            try:
                scores = json.loads(ev["scores_json"])
            except (TypeError, ValueError):
                malformed.append({"judge": ev["judge"], "entry": sid,
                                  "error": "stored score is not valid JSON"})
                continue
            try:
                scores = self._validate_scores(rubric, scores)
            except ValidationError as e:
                malformed.append({"judge": ev["judge"], "entry": sid,
                                  "error": str(e)})
                continue
            raw.append(JudgeEvaluation(ev["judge"], sid, scores))
        report = verify_ranking(rubric, tuple(e["stable_id"] for e in entries), raw)
        if malformed:
            report["malformed"] = malformed

        # Re-check the frozen digests independently of the stored strings.
        from .rubric import compile_rubric
        circuit, _meta = compile_rubric(rubric)
        net = encode(circuit.elements)
        checks = {
            "rubric_digest_matches": rubric.digest() == comp["rubric_digest"],
            "netlist_sha256_matches": hashlib.sha256(net).hexdigest() == comp["netlist_sha256"],
            "netlist_hex_matches": ("0x" + net.hex()) == comp["netlist_hex"],
        }
        report["frozen_rule_checks"] = checks
        report["frozen_rule_ok"] = all(checks.values())
        report["publication"] = self.publication_view(comp)
        report["identity"] = self.identity_report(evals)

        if live_chain and comp.get("processor") and comp.get("circuit_id"):
            try:
                inputs = self._pack_entry_inputs(cid, rubric, entries, evals)
                rows = []
                for sid, payload in inputs.items():
                    on_chain = self.chain.eval_circuit(
                        comp["processor"], comp["circuit_id"], payload)
                    rows.append({"entry": sid, "input": "0x" + payload.hex(),
                                 "chain_output": "0x" + on_chain.hex()})
                report["chain_reads"] = rows
            except Exception as e:
                report["chain_reads_error"] = str(e)
        return report

    def settle(self, cid: str) -> dict:
        self._require(cid)
        result = self.results(cid)
        if result["incomplete"]:
            raise ConflictError(
                "cannot settle: some entries do not have a full panel "
                f"({len(result['incomplete'])} incomplete)")
        if result["invalid"] or result["errors"]:
            raise ConflictError(
                "cannot settle: some evaluations are malformed or out of range. "
                "Fix the inputs first; a result that cannot be reproduced must "
                "not be finalised.")
        self.store.mark_settled(cid)
        self.store.log_event(cid, "competition_settled",
                             {"ranking": [r["entry"] for r in result["ranking"]]})
        return result

    # -- views -------------------------------------------------------------- #
    def get_competition(self, cid: str) -> dict:
        comp = self._require(cid)
        view = dict(comp)
        view["judges"] = json.loads(comp["judges_json"])
        view["tie_break"] = json.loads(comp["tie_break_json"])
        view["rubric"] = json.loads(comp["rubric_json"]) if comp["rubric_json"] else None
        view["publication"] = self.publication_view(comp)
        view.pop("commit_key", None)  # never expose the commitment key
        return view

    def publication_view(self, comp: dict) -> dict:
        mode = comp.get("publish_mode") or PUBLISH_NOT_PUBLISHED
        # "not_published" covers two genuinely different situations. A frozen
        # competition has a compiled circuit but no publication attempt recorded
        # yet; saying "the rubric is not frozen" then would be false.
        if mode == PUBLISH_NOT_PUBLISHED and comp.get("status") != "draft":
            not_published_label = (
                "Frozen locally — no publication attempt has been recorded yet")
        else:
            not_published_label = "Not published — the rubric is not frozen"
        labels = {
            PUBLISH_NOT_PUBLISHED: not_published_label,
            PUBLISH_LOCAL_ONLY: (
                "Frozen locally — the netlist is compiled but no on-chain "
                "circuit exists"),
            PUBLISH_ONCHAIN_PENDING_SIGNATURE: (
                "Ready to publish — awaiting a signed transaction "
                "(not on-chain yet)"),
            PUBLISH_ONCHAIN_VERIFIED: (
                "Published on X Layer — a frozen circuit exists on chain"),
        }
        return {
            "mode": mode,
            "label": labels.get(mode, mode),
            "chain_id": comp.get("chain_id"),
            "processor": comp.get("processor"),
            "circuit_id": comp.get("circuit_id"),
            "tx_hash": comp.get("tx_hash"),
            "published_at": comp.get("published_at"),
            "fee_wei": comp.get("fee_wei"),
            "on_chain": mode == PUBLISH_ONCHAIN_VERIFIED,
        }

    # -- internals ---------------------------------------------------------- #
    def _require(self, cid: str) -> dict:
        comp = self.store.get_competition(cid)
        if comp is None:
            raise KeyError(cid)
        return comp

    def _rubric(self, comp: dict) -> Rubric:
        if not comp["rubric_json"]:
            raise ValidationError("this competition has no rubric yet")
        return Rubric.from_dict(json.loads(comp["rubric_json"]))

    def _validate_scores(self, rubric: Rubric, scores) -> dict:
        if not isinstance(scores, dict):
            raise ValidationError("scores must be an object of criterion -> integer")
        out = {}
        for c in rubric.criteria:
            if c.name not in scores:
                raise ValidationError(f"missing a score for criterion {c.name!r}")
            try:
                v = _as_int(scores[c.name])
            except (TypeError, ValueError) as e:
                raise ValidationError(
                    f"{c.name!r}: score must be a whole number") from e
            if v < 0 or v > c.max_score:
                raise ValidationError(
                    f"{c.name!r}: score {v} is outside the permitted range 0–{c.max_score}")
            out[c.name] = v
        unknown = set(scores) - {c.name for c in rubric.criteria}
        if unknown:
            raise ValidationError(f"unknown criteria: {sorted(unknown)}")
        return out

    def _commitment(self, comp: dict, judge: str, entry_stable_id: str, scores: dict) -> str:
        import hmac
        key = (comp.get("commit_key") or "").encode()
        payload = {"judge": judge, "entry": entry_stable_id, "scores": scores}
        return hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()

    def _has_conflict(self, cid: str, judge: str, entry_id: str) -> bool:
        return any(c["judge"] == judge and c["entry_id"] == entry_id
                   for c in self.store.list_conflicts(cid))

    def _entry_view(self, e: dict, *, see_all_private=False,
                    viewer_account_id=None) -> dict:
        private = bool(e["private"])
        owner = e.get("submitter_account_id")
        can_see = (not private) or see_all_private or (
            owner is not None and owner == viewer_account_id)
        view = {
            "id": e["id"],
            "stable_id": e["stable_id"],
            "title": e["title"] if can_see else "(private entry)",
            "summary": e["summary"] if can_see else "",
            "link": e["link"] if can_see else "",
            "content_hash": e["content_hash"],
            "private": private,
            "submitted_at": e["submitted_at"],
        }
        if can_see and private:
            view["evidence"] = json.loads(e["evidence_json"])
        return view

    def entry_owner(self, cid: str, entry_id: str) -> str | None:
        """The account that submitted an entry, or ``None`` for a pre-auth entry."""
        for e in self.store.list_entries(cid):
            if e["id"] == entry_id:
                return e.get("submitter_account_id")
        return None

    def _pack_entry_inputs(self, cid: str, rubric: Rubric, entries: list[dict],
                           evals: list[dict]) -> dict[str, bytes]:
        """Pack each entry's committed scores exactly as the circuit expects."""
        from .netlist import pack
        by_id = {e["id"]: e["stable_id"] for e in entries}
        per: dict[str, dict[str, dict]] = {}
        for ev in evals:
            per.setdefault(by_id[ev["entry_id"]], {})[ev["judge"]] = json.loads(ev["scores_json"])
        widths = rubric.score_widths
        out = {}
        for sid, judges in per.items():
            if len(judges) != rubric.judges:
                continue
            bits = []
            for judge in sorted(judges):
                for ci, crit in enumerate(rubric.criteria):
                    v = judges[judge][crit.name]
                    for k in range(widths[ci]):
                        bits.append((v >> k) & 1)
            out[sid] = pack(bits)
        return out
