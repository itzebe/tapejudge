"""Tests for the production debris-purge helper (``scripts/purge_demo_competitions.py``).

The deletion itself runs against the deployed Postgres store and is exercised by
hand; these tests pin the two decisions that must never go wrong — the exact-name
match and the refuse-to-empty guard — which are the whole safety of the tool.
"""
from __future__ import annotations

import importlib.util
import os
import unittest

_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "scripts", "purge_demo_competitions.py")


def _load():
    spec = importlib.util.spec_from_file_location("purge_demo_competitions", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


purge = _load()


class TestSelectByName(unittest.TestCase):
    def test_exact_match_only(self):
        comps = [
            {"id": "a", "name": "Journey Probe Round"},
            {"id": "b", "name": "SYNTHETIC — Demo Judging Round"},
            {"id": "c", "name": "Journey Probe Round (copy)"},
            {"id": "d", "name": "journey probe round"},
        ]
        got = [c["id"] for c in purge.select_by_name(comps, "Journey Probe Round")]
        self.assertEqual(got, ["a"])

    def test_no_match_is_empty(self):
        comps = [{"id": "a", "name": "SYNTHETIC — Demo Judging Round"}]
        self.assertEqual(purge.select_by_name(comps, "Journey Probe Round"), [])


class TestGuardNotEverything(unittest.TestCase):
    def test_refuses_to_delete_every_competition(self):
        matched = [{"id": "a", "name": "Journey Probe Round"},
                   {"id": "b", "name": "Journey Probe Round"}]
        with self.assertRaises(SystemExit):
            purge._guard_not_everything(matched, total=2)

    def test_allows_a_strict_subset(self):
        matched = [{"id": "a", "name": "Journey Probe Round"}]
        purge._guard_not_everything(matched, total=6)  # must not raise

    def test_empty_match_is_not_guarded(self):
        purge._guard_not_everything([], total=0)  # must not raise


if __name__ == "__main__":
    unittest.main()
