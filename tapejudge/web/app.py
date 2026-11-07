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
import os
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
    url_for,
)

from ..service import ConflictError, TapeJudgeService, ValidationError
from ..store import Store
from ..xlayer import XLayerClient

_HERE = Path(__file__).resolve().parent


def create_app(*, db_path: str | None = None, chain_client=None,
               testing: bool = False) -> Flask:
    app = Flask(__name__,
                template_folder=str(_HERE / "templates"),
                static_folder=str(_HERE / "static"))
    app.config["SECRET_KEY"] = os.environ.get("TAPEJUDGE_SECRET", "dev-only-not-a-secret")
    app.config["TESTING"] = testing
    db = db_path or os.environ.get("TAPEJUDGE_DB", ":memory:")
    store = Store(db)
    app.config["STORE"] = store
    app.config["SERVICE"] = TapeJudgeService(store, chain_client=chain_client)
    app.config["CHAIN"] = chain_client or XLayerClient()
    _register_routes(app)
    return app


def _svc() -> TapeJudgeService:
    return g.app_service


def _register_routes(app: Flask) -> None:

    @app.before_request
    def _bind():
        g.app_service = app.config["SERVICE"]

    # -- helpers ------------------------------------------------------------ #
    def _comp_or_404(cid):
        try:
            return _svc().get_competition(cid)
        except KeyError:
            abort(404)

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
        return render_template("index.html", competitions=comps,
                               chain_reachable=app.config["CHAIN"].is_reachable())

    # -------------------------------------------------------------- create #
    @app.route("/new", methods=["GET", "POST"])
    def new_competition():
        if request.method == "POST":
            judges = [j.strip() for j in request.form.getlist("judge") if j.strip()]
            if not judges:
                judges = [j.strip() for j in
                          (request.form.get("judges_raw", "") or "").replace(",", "\n").splitlines()
                          if j.strip()]
            try:
                cid = _svc().create_competition(
                    name=request.form.get("name", ""),
                    description=request.form.get("description", ""),
                    organiser=request.form.get("organiser", ""),
                    opens_at=request.form.get("opens_at", ""),
                    closes_at=request.form.get("closes_at", ""),
                    judges=judges,
                    prize_note=request.form.get("prize_note", ""))
                return redirect(url_for("rubric", cid=cid))
            except ValidationError as e:
                flash(str(e), "error")
        return render_template("new.html")

    # -------------------------------------------------------------- rubric #
    @app.route("/c/<cid>/rubric", methods=["GET", "POST"])
    def rubric(cid):
        comp = _comp_or_404(cid)
        if request.method == "POST":
            criteria = _form_dict()
            tie_break = [t for t in request.form.getlist("tie_break") if t]
            try:
                judges_n = int(request.form.get("judges", comp.get("judges", 1) or 1))
                _svc().set_rubric(cid, criteria, judges_n, tie_break)
                flash("Rubric configured. Review it, then freeze.", "ok")
                return redirect(url_for("rubric", cid=cid))
            except (ValidationError, ConflictError) as e:
                flash(str(e), "error")
        comp = _comp_or_404(cid)
        preview = _rubric_preview(comp)
        return render_template("rubric.html", c=comp, preview=preview,
                               max_total=_max_total(comp))

    # ------------------------------------------------------------- publish #
    @app.route("/c/<cid>/publish", methods=["GET", "POST"])
    def publish(cid):
        comp = _comp_or_404(cid)
        result = None
        if request.method == "POST":
            action = request.form.get("action")
            try:
                if action == "freeze":
                    _svc().freeze(cid)
                    flash("Rubric frozen. The rule can no longer change.", "ok")
                elif action == "publish":
                    result = _svc().publish(cid)
                    cat = "ok" if result["mode"] != "local_only" else "warn"
                    flash(_publish_message(result), cat)
                return redirect(url_for("publish", cid=cid))
            except (ValidationError, ConflictError) as e:
                flash(str(e), "error")
        comp = _comp_or_404(cid)
        return render_template("publish.html", c=comp, preview=_rubric_preview(comp))

    # -------------------------------------------------------------- submit #
    @app.route("/c/<cid>/submit", methods=["GET", "POST"])
    def submit(cid):
        comp = _comp_or_404(cid)
        if request.method == "POST":
            try:
                _svc().add_entry(
                    cid, stable_id=request.form.get("stable_id", ""),
                    title=request.form.get("title", ""),
                    summary=request.form.get("summary", ""),
                    link=request.form.get("link", ""),
                    private=request.form.get("private") == "on")
                flash("Entry submitted.", "ok")
                return redirect(url_for("submit", cid=cid))
            except (ValidationError, ConflictError) as e:
                flash(str(e), "error")
        entries = _svc().list_entries(cid)
        return render_template("submit.html", c=comp, entries=entries)

    # --------------------------------------------------------------- judge #
    @app.route("/c/<cid>/judge", methods=["GET", "POST"])
    def judge(cid):
        comp = _comp_or_404(cid)
        if request.method == "POST":
            action = request.form.get("action", "score")
            try:
                if action == "conflict":
                    _svc().declare_conflict(cid, judge=request.form.get("judge", ""),
                                            entry_stable_id=request.form.get("entry", ""),
                                            reason=request.form.get("reason", ""))
                    flash("Conflict of interest recorded; this judge will "
                          "not score that entry.", "ok")
                else:
                    scores = {c["name"]: request.form.get(f"score_{c['name']}")
                              for c in (comp.get("rubric") or {}).get("criteria", [])}
                    _svc().evaluate(cid, entry_stable_id=request.form.get("entry", ""),
                                    judge=request.form.get("judge", ""),
                                    scores=scores,
                                    justification=request.form.get("justification", ""))
                    flash("Score committed. It cannot be changed.", "ok")
                return redirect(url_for("judge", cid=cid, judge=request.form.get("judge", "")))
            except (ValidationError, ConflictError) as e:
                flash(str(e), "error")
        judge_id = request.args.get("judge", "")
        return render_template("judge.html", c=comp,
                               entries=_svc().list_entries(cid),
                               judge=judge_id,
                               matrix=_judge_matrix(cid))

    # ------------------------------------------------------------- results #
    @app.route("/c/<cid>/results", methods=["GET", "POST"])
    def results(cid):
        comp = _comp_or_404(cid)
        if request.method == "POST":
            try:
                _svc().settle(cid)
                flash("Competition finalised.", "ok")
            except (ValidationError, ConflictError) as e:
                flash(str(e), "error")
            return redirect(url_for("results", cid=cid))
        res = _svc().results(cid)
        return render_template("results.html", c=comp, r=res,
                               criteria=(comp.get("rubric") or {}).get("criteria", []))

    # -------------------------------------------------------------- verify #
    @app.route("/c/<cid>/verify")
    def verify(cid):
        comp = _comp_or_404(cid)
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
    @app.get("/api/health")
    def api_health():
        return jsonify({"ok": True, "chain_reachable": app.config["CHAIN"].is_reachable()})

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

    # -- API writes (used by tests; the UI posts forms to the same service) -- #
    @app.post("/api/competitions")
    def api_create():
        body = request.get_json(force=True, silent=True) or {}
        try:
            cid = _svc().create_competition(
                name=body.get("name", ""), description=body.get("description", ""),
                organiser=body.get("organiser", ""), opens_at=body.get("opens_at", ""),
                closes_at=body.get("closes_at", ""), judges=body.get("judges") or [])
            return jsonify({"id": cid}), 201
        except ValidationError as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/competitions/<cid>/rubric")
    def api_set_rubric(cid):
        _comp_or_404(cid)
        body = request.get_json(force=True, silent=True) or {}
        try:
            _svc().set_rubric(cid, body.get("criteria", []), body.get("judges", 1),
                              body.get("tie_break"))
            return jsonify(_svc().get_competition(cid))
        except (ValidationError, ConflictError) as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/competitions/<cid>/freeze")
    def api_freeze(cid):
        _comp_or_404(cid)
        try:
            _svc().freeze(cid)
            return jsonify(_svc().get_competition(cid))
        except (ValidationError, ConflictError) as e:
            return jsonify({"error": str(e)}), 409

    @app.post("/api/competitions/<cid>/entries")
    def api_add_entry(cid):
        _comp_or_404(cid)
        body = request.get_json(force=True, silent=True) or {}
        try:
            e = _svc().add_entry(cid, stable_id=body.get("stable_id", ""),
                                 title=body.get("title", ""), summary=body.get("summary", ""),
                                 link=body.get("link", ""), private=bool(body.get("private")))
            return jsonify(e), 201
        except (ValidationError, ConflictError) as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/competitions/<cid>/evaluations")
    def api_evaluate(cid):
        _comp_or_404(cid)
        body = request.get_json(force=True, silent=True) or {}
        try:
            out = _svc().evaluate(cid, entry_stable_id=body.get("entry", ""),
                                  judge=body.get("judge", ""), scores=body.get("scores", {}),
                                  justification=body.get("justification", ""))
            return jsonify(out), 201
        except (ValidationError, ConflictError) as e:
            return jsonify({"error": str(e)}), 400

    @app.errorhandler(404)
    def _404(e):
        return render_template("error.html", code=404,
                               message="That competition does not exist."), 404

    @app.errorhandler(500)
    def _500(e):  # pragma: no cover - safety net
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
    return {
        "criteria": r["criteria"],
        "judges": r["judges"],
        "tie_break": r["tie_break"],
        "sentence": (f"Each entry is scored by {r['judges']} judge"
                     f"{'s' if r['judges'] != 1 else ''} on {len(r['criteria'])} criteria. "
                     f"The total is {' + '.join(parts)}, summed across the panel."),
    }


def _max_total(comp: dict) -> int | None:
    if not comp.get("rubric"):
        return None
    r = comp["rubric"]
    return sum(c["weight"] * c["max_score"] * r["judges"] for c in r["criteria"])


def _judge_matrix(cid: str) -> dict:
    """Which (judge, entry) pairs already have a committed score."""
    svc = g.app_service
    comp = svc.get_competition(cid)
    entries = svc.list_entries(cid)
    evals = svc.store.list_evaluations(cid)
    by_id = {e["id"]: e["stable_id"] for e in entries}
    done = {}
    for ev in evals:
        done[(ev["judge"], by_id[ev["entry_id"]])] = {
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
