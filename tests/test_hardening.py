"""Regression tests for the Mission 5 hardening pass.

Each test pins a defect that was confirmed by an independent adversarial review
and then fixed. The intent is that none of these can silently regress:

* a read-only page must never answer 500 because of a state the domain allows
  (no rubric yet, a malformed stored row);
* an integrity error must never surface as a 500 — the database constraint is
  the real guard for the check-then-insert race;
* a score is never silently coerced (no float truncation, no ``True`` -> 1);
* the panel and the rubric cannot disagree about how many scores an entry needs;
* a rule that cannot compile is a clean 4xx, not a 500;
* the production app ships hardened headers, cookie flags and no default secret.
"""
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tapejudge.service import ConflictError, TapeJudgeService
from tapejudge.store import DuplicateError, Store
from tapejudge.web.app import create_app
from tapejudge.web.ratelimit import RateLimiter


class _StubChain:
    def prepare_tapeout(self, netlist, n_in, n_out):
        return {"mode": "local_only", "chain_id": 196, "processor": None,
                "circuit_id": None, "tx": None, "fee_wei": None, "fee_known": False,
                "netlist_bytes": len(netlist), "note": "stub", "blocked_reason": None}

    def is_reachable(self):
        return False


CRITERIA = [{"name": "demo", "weight": 1, "max_score": 10}]
JUDGES = ["j1"]


def make_client(*, testing=True, db_path=":memory:"):
    app = create_app(db_path=db_path, chain_client=_StubChain(), testing=testing)
    return app, app.test_client()


def build(c, *, freeze=True, evaluate=True):
    cid = c.post("/api/competitions",
                 json={"name": "Hardening", "judges": JUDGES}).get_json()["id"]
    c.post(f"/api/competitions/{cid}/rubric",
           json={"criteria": CRITERIA, "judges": 1})
    if freeze:
        c.post(f"/api/competitions/{cid}/freeze")
    c.post(f"/api/competitions/{cid}/entries",
           json={"stable_id": "a", "title": "A"})
    if freeze and evaluate:
        c.post(f"/api/competitions/{cid}/evaluations",
               json={"entry": "a", "judge": "j1", "scores": {"demo": 5}})
    return cid


def _tamper_scores(app, cid, value):
    with app.config["STORE"]._conn() as conn:
        conn.execute("UPDATE evaluations SET scores_json=? WHERE competition_id=?",
                     (value, cid))


class TestNoRubricNavigation(unittest.TestCase):
    """A draft with no rubric is an ordinary state, not an error page."""

    def setUp(self):
        self.app, self.client = make_client()

    def test_pages_render_pending_not_500(self):
        cid = self.client.post("/api/competitions",
                               json={"name": "Empty", "judges": ["j1"]}).get_json()["id"]
        for path in (f"/c/{cid}/results", f"/c/{cid}/verify"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn("No rule yet", r.get_data(as_text=True))

    def test_api_reports_missing_rule_as_4xx(self):
        cid = self.client.post("/api/competitions",
                               json={"name": "Empty", "judges": ["j1"]}).get_json()["id"]
        for path in (f"/api/competitions/{cid}/results",
                     f"/api/competitions/{cid}/verify"):
            r = self.client.get(path)
            self.assertGreaterEqual(r.status_code, 400)
            self.assertLess(r.status_code, 500, path)


class TestMalformedStoredRows(unittest.TestCase):
    """A malformed stored row is reported, never a 500, and never settled on."""

    def setUp(self):
        self.app, self.client = make_client()
        self.cid = build(self.client)

    def test_non_json_score_does_not_crash(self):
        _tamper_scores(self.app, self.cid, "not json")
        self.assertEqual(self.client.get(f"/api/competitions/{self.cid}/results").status_code, 200)
        self.assertEqual(self.client.get(f"/api/competitions/{self.cid}/verify").status_code, 200)

    def test_out_of_range_stored_score_does_not_crash(self):
        _tamper_scores(self.app, self.cid, '{"demo": 99}')
        r = self.client.get(f"/api/competitions/{self.cid}/verify")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertIn("malformed", body)
        # The out-of-range score is excluded from the ranking, not scored.
        self.assertEqual(body["ranking"], [])

    def test_settle_refuses_malformed(self):
        _tamper_scores(self.app, self.cid, "not json")
        r = self.client.post(f"/c/{self.cid}/results", follow_redirects=False)
        # The POST is a redirect back to the results page (with a flash), not a 500.
        self.assertEqual(r.status_code, 302)
        self.assertNotEqual(self.app.config["SERVICE"].get_competition(self.cid)["status"],
                            "settled")


class TestScoreValidation(unittest.TestCase):
    """A score is never coerced: no float truncation, no bool."""

    def setUp(self):
        self.app, self.client = make_client()
        self.cid = build(self.client, freeze=True, evaluate=False)

    def _score(self, value):
        return self.client.post(f"/api/competitions/{self.cid}/evaluations",
                                json={"entry": "a", "judge": "j1", "scores": {"demo": value}})

    def test_float_rejected(self):
        for v in (5.9, 5.0, 0.5, 10.999):
            self.assertEqual(self._score(v).status_code, 400, v)

    def test_bool_rejected(self):
        self.assertEqual(self._score(True).status_code, 400)

    def test_integer_string_accepted(self):
        r = self._score("5")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["scores"], {"demo": 5})

    def test_out_of_range_rejected(self):
        self.assertEqual(self._score(99).status_code, 400)


class TestPanelConsistency(unittest.TestCase):
    def setUp(self):
        self.app, self.client = make_client()

    def test_rubric_size_must_match_panel(self):
        cid = self.client.post(
            "/api/competitions",
            json={"name": "Panel", "judges": ["j1", "j2", "j3"]}).get_json()["id"]
        r = self.client.post(f"/api/competitions/{cid}/rubric",
                             json={"criteria": CRITERIA, "judges": 1})
        self.assertEqual(r.status_code, 400)
        self.assertIn("panel has 3 judge", r.get_json()["error"])

    def test_matching_size_is_accepted(self):
        cid = self.client.post(
            "/api/competitions",
            json={"name": "Panel", "judges": ["j1", "j2", "j3"]}).get_json()["id"]
        r = self.client.post(f"/api/competitions/{cid}/rubric",
                             json={"criteria": CRITERIA, "judges": 3})
        self.assertEqual(r.status_code, 200)

    def test_duplicate_judges_rejected(self):
        r = self.client.post("/api/competitions",
                             json={"name": "Panel", "judges": ["j1", "j1"]})
        self.assertEqual(r.status_code, 400)

    def test_too_many_judges_rejected(self):
        r = self.client.post("/api/competitions",
                             json={"name": "Panel", "judges": [f"j{i}" for i in range(50)]})
        self.assertEqual(r.status_code, 400)


class TestIllFormedRubric(unittest.TestCase):
    def setUp(self):
        self.app, self.client = make_client()

    def test_oversized_rubric_is_a_4xx_not_500(self):
        cid = self.client.post("/api/competitions",
                               json={"name": "Big", "judges": ["j1"]}).get_json()["id"]
        crit = [{"name": f"c{i}", "weight": 1, "max_score": 10} for i in range(900)]
        r = self.client.post(f"/api/competitions/{cid}/rubric",
                             json={"criteria": crit, "judges": 1})
        self.assertEqual(r.status_code, 400)
        self.assertNotEqual(r.status_code, 500)


class TestEvaluationRace(unittest.TestCase):
    """The DB UNIQUE constraint is the real guard; a race is a 4xx, not a 500."""

    def test_store_raises_duplicate_error(self):
        st = Store(":memory:")
        cid = st.create_competition(name="C", description="", organiser="", opens_at="",
                                    closes_at="", judges=["j1"])
        st.add_entry(cid, stable_id="a", title="A", summary="", link="", evidence={},
                     content_hash="h")
        eid = st.list_entries(cid)[0]["id"]
        st.add_evaluation(cid, entry_id=eid, judge="j1", scores={"demo": 5},
                          justification="", commitment="c", nonce="")
        with self.assertRaises(DuplicateError):
            st.add_evaluation(cid, entry_id=eid, judge="j1", scores={"demo": 6},
                              justification="", commitment="c2", nonce="")

    def test_service_translates_duplicate_to_conflict(self):
        st = Store(":memory:")
        svc = TapeJudgeService(st, chain_client=_StubChain())
        cid = svc.create_competition(name="C", judges=["j1"])
        svc.set_rubric(cid, CRITERIA, 1)
        svc.freeze(cid)
        svc.add_entry(cid, stable_id="a", title="A")
        svc.evaluate(cid, entry_stable_id="a", judge="j1", scores={"demo": 5})
        with self.assertRaises(ConflictError):
            svc.evaluate(cid, entry_stable_id="a", judge="j1", scores={"demo": 6})

    def test_concurrent_evaluations_store_exactly_one(self):
        """Many threads race to submit the same (judge, entry): exactly one wins,
        the rest get a clean conflict, and no IntegrityError escapes."""
        with tempfile.TemporaryDirectory() as d:
            st = Store(os.path.join(d, "race.db"))
            svc = TapeJudgeService(st, chain_client=_StubChain())
            cid = svc.create_competition(name="C", judges=["j1"])
            svc.set_rubric(cid, CRITERIA, 1)
            svc.freeze(cid)
            svc.add_entry(cid, stable_id="a", title="A")

            results = []
            barrier = threading.Barrier(16)

            def worker():
                barrier.wait()
                try:
                    svc.evaluate(cid, entry_stable_id="a", judge="j1", scores={"demo": 5})
                    results.append("ok")
                except ConflictError:
                    results.append("conflict")
                except Exception as e:  # any other exception is a defect
                    results.append(f"UNEXPECTED:{type(e).__name__}")

            threads = [threading.Thread(target=worker) for _ in range(16)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(results.count("ok"), 1, results)
            self.assertEqual(results.count("conflict"), 15, results)
            self.assertFalse([r for r in results if r.startswith("UNEXPECTED")], results)
            self.assertEqual(len(st.list_evaluations(cid)), 1)


class TestSecurityHeadersAndSecrets(unittest.TestCase):
    def test_headers_present(self):
        _app, c = make_client(testing=False)
        r = c.get("/")
        self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.headers.get("X-Frame-Options"), "DENY")
        self.assertIn("default-src 'self'", r.headers.get("Content-Security-Policy", ""))

    def test_api_is_not_cached(self):
        _app, c = make_client(testing=False)
        r = c.get("/api/health")
        self.assertEqual(r.headers.get("Cache-Control"), "no-store")

    def test_cookie_flags(self):
        app, _c = make_client(testing=False)
        self.assertTrue(app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])

    def test_no_default_secret_in_production(self):
        old = os.environ.pop("TAPEJUDGE_SECRET", None)
        old_allow = os.environ.pop("TAPEJUDGE_ALLOW_DEV_SECRET", None)
        try:
            app1 = create_app(db_path=":memory:", chain_client=_StubChain(), testing=False)
            app2 = create_app(db_path=":memory:", chain_client=_StubChain(), testing=False)
            self.assertNotEqual(app1.config["SECRET_KEY"], "dev-only-not-a-secret")
            # Two production apps must not share a guessable constant.
            self.assertNotEqual(app1.config["SECRET_KEY"], app2.config["SECRET_KEY"])
        finally:
            if old is not None:
                os.environ["TAPEJUDGE_SECRET"] = old
            if old_allow is not None:
                os.environ["TAPEJUDGE_ALLOW_DEV_SECRET"] = old_allow

    def test_configured_secret_is_used(self):
        old = os.environ.get("TAPEJUDGE_SECRET")
        os.environ["TAPEJUDGE_SECRET"] = "configured-value"  # noqa: S105
        try:
            app = create_app(db_path=":memory:", chain_client=_StubChain(), testing=False)
            self.assertEqual(app.config["SECRET_KEY"], "configured-value")
        finally:
            if old is None:
                os.environ.pop("TAPEJUDGE_SECRET", None)
            else:
                os.environ["TAPEJUDGE_SECRET"] = old


class TestRateLimiter(unittest.TestCase):
    def test_fixed_window(self):
        rl = RateLimiter(default_limit=3, window_seconds=60)
        for _ in range(3):
            self.assertTrue(rl.allow("1.2.3.4", "misc", now=1000.0))
        self.assertFalse(rl.allow("1.2.3.4", "misc", now=1000.0))
        # A different bucket or client is unaffected.
        self.assertTrue(rl.allow("1.2.3.4", "eval", now=1000.0))
        self.assertTrue(rl.allow("5.6.7.8", "misc", now=1000.0))
        # The window rolls over.
        self.assertTrue(rl.allow("1.2.3.4", "misc", now=1061.0))

    def test_rule_overrides_default(self):
        rl = RateLimiter(default_limit=100, window_seconds=60)
        rl.rule("eval", 2)
        self.assertTrue(rl.allow("ip", "eval", now=0.0))
        self.assertTrue(rl.allow("ip", "eval", now=0.0))
        self.assertFalse(rl.allow("ip", "eval", now=0.0))

    def test_write_endpoint_returns_429_when_limited(self):
        app = create_app(db_path=":memory:", chain_client=_StubChain(), testing=False)
        c = app.test_client()
        r1 = c.post("/api/competitions", json={"name": "One", "judges": ["j1"]})
        self.assertEqual(r1.status_code, 201)
        # The app's own limiter allows 30 competition writes a minute; hammering
        # past that must answer 429, not 500.
        codes = [c.post("/api/competitions",
                        json={"name": f"N{i}", "judges": ["j1"]}).status_code
                 for i in range(40)]
        self.assertIn(429, codes)


class TestStylesheetRegression(unittest.TestCase):
    """The narrow-screen fixes are pinned so a later edit cannot silently
    reintroduce the horizontal scroll found at 390px."""

    def test_narrow_screen_rules_present(self):
        from pathlib import Path
        css = (Path(__file__).resolve().parents[1]
               / "tapejudge" / "web" / "static" / "style.css").read_text()
        self.assertIn("max-width: 640px", css)
        # A grid/flex child must be allowed to shrink, or a wide child (a table,
        # a hash) pushes the whole page past the viewport.
        self.assertIn(".split > *", css)
        self.assertIn("min-width: 0", css)
        self.assertIn("overflow-x: auto", css)


if __name__ == "__main__":
    unittest.main()
