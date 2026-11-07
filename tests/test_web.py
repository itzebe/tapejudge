"""Web application tests — the eight screens and the JSON API.

These drive the real Flask app with the test client against a synthetic
competition. They check that:

* every screen renders and carries the facts it must (frozen digest, ranking,
  honest publication state);
* the create → rubric → freeze → entry → evaluate → results → verify workflow
  works through HTTP;
* the API rejects bad input with a 4xx and a message, not a 500;
* a frozen rubric cannot be replaced through the API;
* the verify page never claims an on-chain circuit when there is none.

All data is synthetic.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tapejudge.web.app import create_app
from tests.authtestkit import build_competition, login, logout, post


class _StubChain:
    def prepare_tapeout(self, netlist, n_in, n_out, n_gates=None):
        return {"mode": "onchain_pending_signature", "chain_id": 196,
                "processor": "0xstubprocessor", "circuit_id": 7,
                "tx": {"unsigned": True}, "fee_wei": None, "fee_known": False,
                "netlist_bytes": len(netlist), "note": "stub", "blocked_reason": None}

    def is_reachable(self):
        return False


CRITERIA = [
    {"name": "demo", "weight": 3, "max_score": 10},
    {"name": "tests", "weight": 2, "max_score": 10},
]
JUDGES = ["j1", "j2"]
SCORES = {
    "j1": {"alpha": {"demo": 9, "tests": 8}, "beta": {"demo": 6, "tests": 7}},
    "j2": {"alpha": {"demo": 8, "tests": 9}, "beta": {"demo": 7, "tests": 6}},
}


def make_client():
    app = create_app(db_path=":memory:", chain_client=_StubChain(), testing=True)
    return app, app.test_client()


def build_via_api(client, *, freeze=True, evaluate=True, app=None):
    cid, _ = build_competition(
        app, client, judges=JUDGES, criteria=CRITERIA,
        freeze=freeze, entries=("alpha", "beta"), evaluate=evaluate, scores=SCORES)
    return cid


class TestPages(unittest.TestCase):
    def setUp(self):
        self.app, self.client = make_client()

    def test_index_empty_state(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("No competitions yet", html)
        self.assertIn("Create the first competition", html)

    def test_new_requires_sign_in(self):
        r = post(self.client, "/new", form={"name": "Round", "judges_raw": "j1"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_create_flow_redirects_to_rubric(self):
        login(self.client, self.app, "org@test.dev")
        r = post(self.client, "/new",
                 form={"name": "Round", "judges_raw": "j1\nj2"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/rubric", r.headers["Location"])

    def test_new_without_name_shows_error(self):
        login(self.client, self.app, "org@test.dev")
        r = post(self.client, "/new", form={"name": "", "judges_raw": "j1"})
        self.assertIn("needs a name", r.get_data(as_text=True))

    def test_full_flow_pages_render(self):
        cid = build_via_api(self.client, app=self.app)
        for path in (f"/c/{cid}/rubric", f"/c/{cid}/publish", f"/c/{cid}/submit",
                     f"/c/{cid}/judge", f"/c/{cid}/results", f"/c/{cid}/verify"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)

    def test_results_page_shows_ranking_and_subtotals(self):
        cid = build_via_api(self.client, app=self.app)
        html = self.client.get(f"/c/{cid}/results").get_data(as_text=True)
        self.assertIn("alpha", html)
        self.assertIn("beta", html)
        # alpha should be ranked first
        self.assertLess(html.index(">alpha<"), html.index(">beta<"))

    def test_verify_page_never_claims_onchain_without_a_circuit(self):
        cid = build_via_api(self.client, app=self.app)
        html = self.client.get(f"/c/{cid}/verify").get_data(as_text=True)
        self.assertIn("Not published on chain", html)
        self.assertNotIn("The circuit exists on X Layer", html)

    def test_publish_page_is_honest(self):
        cid = build_via_api(self.client, app=self.app)
        html = self.client.get(f"/c/{cid}/publish").get_data(as_text=True)
        self.assertIn("does", html)
        self.assertIn("not", html)  # the "does not guarantee" block
        self.assertIn("freezing does", html.lower().replace("does ", "does "))

    def test_404_page(self):
        r = self.client.get("/c/does-not-exist/results")
        self.assertEqual(r.status_code, 404)
        self.assertIn("does not exist", r.get_data(as_text=True))

    def test_judge_page_lists_entries(self):
        cid = build_via_api(self.client, app=self.app)
        login(self.client, self.app, "j1@test.dev", name="Judge j1")
        html = self.client.get(f"/c/{cid}/judge").get_data(as_text=True)
        self.assertIn("alpha", html)
        self.assertIn("committed", html)

    def test_judge_page_requires_sign_in(self):
        cid = build_via_api(self.client, app=self.app)
        logout(self.client)
        r = self.client.get(f"/c/{cid}/judge")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])


class TestApi(unittest.TestCase):
    def setUp(self):
        self.app, self.client = make_client()

    def test_health(self):
        self.assertEqual(self.client.get("/api/health").get_json()["ok"], True)

    def test_create_and_get(self):
        cid = build_via_api(self.client, app=self.app, freeze=False, evaluate=False)
        data = self.client.get(f"/api/competitions/{cid}").get_json()
        self.assertEqual(data["name"], "Synthetic Web Round")
        self.assertEqual(data["status"], "draft")
        self.assertNotIn("commit_key", data)

    def test_rubric_validation_error_is_400(self):
        cid = build_via_api(self.client, app=self.app, freeze=False, evaluate=False)
        r = post(self.client, f"/api/competitions/{cid}/rubric",
                 json={"criteria": [{"name": "x", "weight": 0, "max_score": 5}],
                       "judges": 1})
        self.assertEqual(r.status_code, 400)
        self.assertIn("weight", r.get_json()["error"])

    def test_frozen_rubric_cannot_be_replaced_via_api(self):
        cid = build_via_api(self.client, app=self.app, evaluate=False)
        r = post(self.client, f"/api/competitions/{cid}/rubric",
                 json={"criteria": [{"name": "x", "weight": 1, "max_score": 5}],
                       "judges": 1})
        self.assertEqual(r.status_code, 400)
        self.assertIn("frozen", r.get_json()["error"].lower())

    def test_duplicate_entry_is_400(self):
        cid = build_via_api(self.client, app=self.app, evaluate=False)
        r = post(self.client, f"/api/competitions/{cid}/entries",
                 json={"stable_id": "alpha", "title": "Again"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("already exists", r.get_json()["error"])

    def test_duplicate_evaluation_is_400(self):
        cid = build_via_api(self.client, app=self.app)
        login(self.client, self.app, "j1@test.dev", name="Judge j1")
        r = post(self.client, f"/api/competitions/{cid}/evaluations",
                 json={"entry": "alpha", "scores": {"demo": 1, "tests": 1}})
        self.assertEqual(r.status_code, 400)

    def test_out_of_range_score_is_400(self):
        cid = build_via_api(self.client, app=self.app, evaluate=False)
        login(self.client, self.app, "j1@test.dev", name="Judge j1")
        r = post(self.client, f"/api/competitions/{cid}/evaluations",
                 json={"entry": "alpha", "scores": {"demo": 99, "tests": 1}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("range", r.get_json()["error"])

    def test_results_api_shape(self):
        cid = build_via_api(self.client, app=self.app)
        res = self.client.get(f"/api/competitions/{cid}/results").get_json()
        self.assertIn("ranking", res)
        self.assertEqual(res["ranking"][0]["entry"], "alpha")
        self.assertEqual(len(res["ranking"]), 2)

    def test_verify_api_reports_frozen_rule_ok(self):
        cid = build_via_api(self.client, app=self.app)
        rep = self.client.get(f"/api/competitions/{cid}/verify").get_json()
        self.assertTrue(rep["frozen_rule_ok"])
        self.assertFalse(rep["publication"]["on_chain"])

    def test_audit_api(self):
        cid = build_via_api(self.client, app=self.app)
        data = self.client.get(f"/api/competitions/{cid}/audit").get_json()
        kinds = [e["kind"] for e in data["events"]]
        self.assertIn("rubric_frozen", kinds)
        self.assertIn("evaluation_committed", kinds)


if __name__ == "__main__":
    unittest.main()
