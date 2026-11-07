"""The offline demo is the first thing a newcomer runs; it must not rot.

`demo/run_demo.py` builds its own chain client and an in-memory store, so the
service and web tests never exercise it. A stale stub interface once made the
run crash at the publication step. This pins the whole offline run end to end.
"""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestOfflineDemo(unittest.TestCase):
    def test_offline_demo_completes_honestly(self):
        proc = subprocess.run(  # noqa: S603 - fixed interpreter and script path
            [sys.executable, os.path.join(ROOT, "demo", "run_demo.py")],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
            env={**os.environ, "PYTHONPATH": ROOT},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        out = proc.stdout
        self.assertIn("frozen rule intact: True", out)
        self.assertIn("published on chain : False", out)
        self.assertIn("All data above is SYNTHETIC", out)


if __name__ == "__main__":
    unittest.main()
