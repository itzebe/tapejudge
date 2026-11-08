"""Tests for the production reset helper (``scripts/reset_production_store.py``).

The destructive part runs by hand against the deployed store; these tests pin the
pure decision — the keep/delete partition — and the guard that a reset must name
a real baseline, which is the whole safety of the tool.
"""
from __future__ import annotations

import importlib.util
import os
import unittest

_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "scripts", "reset_production_store.py")


def _load():
    spec = importlib.util.spec_from_file_location("reset_production_store", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


reset = _load()


class TestPartition(unittest.TestCase):
    def test_splits_by_key(self):
        rows = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        to_delete, to_keep = reset.partition(rows, {"b"}, "id")
        self.assertEqual([r["id"] for r in to_keep], ["b"])
        self.assertEqual([r["id"] for r in to_delete], ["a", "c"])

    def test_empty_keep_deletes_everything(self):
        rows = [{"id": "a"}, {"id": "b"}]
        to_delete, to_keep = reset.partition(rows, set(), "id")
        self.assertEqual(len(to_delete), 2)
        self.assertEqual(to_keep, [])


class TestMainGuards(unittest.TestCase):
    def test_requires_keep_competition(self):
        rc = reset.main(["--dsn", "postgresql://x/y"])
        self.assertEqual(rc, 2)

    def test_requires_dsn(self):
        rc = reset.main(["--keep-competition", "comp_x", "--dsn", ""])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
