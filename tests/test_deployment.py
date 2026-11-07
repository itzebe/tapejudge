"""Deployment-artifact tests.

These do not run the container (CI has no Docker); they assert that the
production artifacts stay consistent with the application's real requirements,
so a later edit cannot quietly ship an image that starts insecurely or binds to
a store the app cannot write.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestDockerfile(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "Dockerfile").read_text()

    def test_runs_as_non_root(self):
        self.assertIn("USER tapejudge", self.text)
        self.assertRegex(self.text, r"useradd[^\n]*--uid 10001")

    def test_installs_from_pyproject(self):
        # The image must install the package (so `tapejudge` is importable), not
        # copy files and hope the path is right.
        self.assertIn("pip install", self.text)
        self.assertIn(".", self.text)

    def test_entrypoint_is_copied_and_executable(self):
        self.assertIn("COPY scripts/docker-entrypoint.sh", self.text)
        self.assertRegex(self.text, r"chmod 755 .*docker-entrypoint\.sh")
        self.assertIn('ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]', self.text)

    def test_data_volume_and_healthcheck(self):
        self.assertIn("VOLUME", self.text)
        self.assertIn("/api/health", self.text)
        self.assertIn("HEALTHCHECK", self.text)


class TestEntrypoint(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "scripts" / "docker-entrypoint.sh").read_text()

    def test_refuses_missing_secret(self):
        self.assertIn("TAPEJUDGE_SECRET is not set", self.text)
        self.assertIn("exit 1", self.text)

    def test_refuses_dev_secret_escape(self):
        self.assertIn("TAPEJUDGE_ALLOW_DEV_SECRET", self.text)

    def test_runs_gunicorn_on_the_wsgi_app(self):
        self.assertIn("gunicorn", self.text)
        self.assertIn("tapejudge.web.app:app", self.text)

    def test_defaults_to_one_worker(self):
        # SQLite is a single-writer store; more than one worker by default would
        # be a write-contention bug, not a scaling win.
        self.assertRegex(self.text, r"TAPEJUDGE_WORKERS:-1")


class TestProductionPlanConsistency(unittest.TestCase):
    """The documented plan and the shipped artifacts must agree."""

    def test_plan_names_the_same_wsgi_target(self):
        plan = (ROOT / "docs" / "TAPEJUDGE_PRODUCTION_DEPLOYMENT_PLAN.md").read_text()
        self.assertIn("tapejudge.web.app:app", plan)

    def test_compose_file_matches_entrypoint_contract(self):
        compose = ROOT / "docker-compose.yml"
        self.assertTrue(compose.exists(), "docker-compose.yml is missing")
        text = compose.read_text()
        self.assertIn("TAPEJUDGE_SECRET", text)
        self.assertIn("/data", text)
        self.assertNotIn("TAPEJUDGE_ALLOW_DEV_SECRET", text)

    def test_env_example_lists_the_required_vars(self):
        env = (ROOT / ".env.example").read_text()
        for key in ("TAPEJUDGE_SECRET", "TAPEJUDGE_DB", "TAPEJUDGE_HOST", "TAPEJUDGE_PORT"):
            self.assertIn(key, env)
        # The insecure escape hatches must be documented as dev-only.
        self.assertIn("TAPEJUDGE_ALLOW_DEV_SECRET", env)


if __name__ == "__main__":
    unittest.main()
