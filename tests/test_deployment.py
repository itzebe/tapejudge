"""Deployment-artifact tests.

These do not run the container (CI has no Docker); they assert that the
production artifacts stay consistent with the application's real requirements,
so a later edit cannot quietly ship an image that starts insecurely or binds to
a store the app cannot write.
"""
from __future__ import annotations

import os
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


class TestVercelDeployment(unittest.TestCase):
    """The Vercel artifacts must match the app's real entrypoint and contract."""

    def test_root_entrypoint_exports_the_wsgi_app(self):
        # Vercel's Flask preset looks for a WSGI `app` at main.py.
        main = ROOT / "main.py"
        self.assertTrue(main.exists(), "main.py entrypoint is missing")
        text = main.read_text()
        self.assertIn("from tapejudge.web.app import app", text)
        self.assertIn('__all__ = ["app"]', text)

    def test_vercel_json_selects_the_flask_framework(self):
        import json

        cfg = json.loads((ROOT / "vercel.json").read_text())
        self.assertEqual(cfg["framework"], "flask")
        self.assertIn("main.py", cfg.get("functions", {}))

    def test_vercelignore_excludes_heavy_non_runtime_dirs(self):
        text = (ROOT / ".vercelignore").read_text()
        for d in ("tests/", "evidence/", "docs/", "scripts/", "demo/"):
            self.assertIn(d, text)

    def test_serverless_start_refuses_missing_secret_and_dsn(self):
        """A hosted start must fail loudly, never degrade to a weak secret or SQLite.

        Simulated by setting the ``VERCEL`` marker the platform provides; the
        guard must refuse, so a misconfigured deploy cannot silently lose data.
        """
        from tapejudge.web.app import create_app

        base = dict(os.environ)
        try:
            os.environ["VERCEL"] = "1"
            os.environ.pop("TAPEJUDGE_SECRET", None)
            os.environ.pop("TAPEJUDGE_DATABASE_URL", None)
            os.environ.pop("TAPEJUDGE_DB_SCHEMA", None)
            with self.assertRaises(RuntimeError):
                create_app(db_path=":memory:", testing=False)

            os.environ["TAPEJUDGE_SECRET"] = "x" * 32
            with self.assertRaises(RuntimeError):
                create_app(db_path=":memory:", testing=False)

            os.environ["TAPEJUDGE_DATABASE_URL"] = "postgresql://u:p@h/db"
            with self.assertRaises(RuntimeError):
                create_app(db_path=":memory:", testing=False)
        finally:
            os.environ.clear()
            os.environ.update(base)

    def test_stable_schema_env_wins_over_random(self):
        """On Postgres the schema must be stable across instances when asked."""
        from tapejudge.store import _pg_schema

        base = dict(os.environ)
        try:
            os.environ["TAPEJUDGE_DB_SCHEMA"] = "tapejudge-prod"
            first = _pg_schema(":memory:")
            second = _pg_schema(":memory:")
            self.assertEqual(first, second, "schema must be stable, not random")
            self.assertTrue(first.startswith("t_"))
        finally:
            os.environ.clear()
            os.environ.update(base)

    def test_default_schema_is_random_for_in_memory(self):
        from tapejudge.store import _pg_schema

        base = dict(os.environ)
        try:
            os.environ.pop("TAPEJUDGE_DB_SCHEMA", None)
            self.assertNotEqual(_pg_schema(":memory:"), _pg_schema(":memory:"))
        finally:
            os.environ.clear()
            os.environ.update(base)

    def test_pyproject_declares_the_postgres_driver_as_runtime(self):
        """Vercel installs only ``[project.dependencies]``, not an extra.

        The durable store needs ``psycopg`` at runtime, so it must be a base
        dependency: as an optional extra it is absent on the host and every
        request fails at import (the observed FUNCTION_INVOCATION_FAILED).
        """
        import tomllib

        data = tomllib.loads((ROOT / "pyproject.toml").read_text())
        deps = data["project"]["dependencies"]
        self.assertTrue(
            any(d.startswith("psycopg") for d in deps),
            "psycopg must be a base dependency for a serverless deploy")


if __name__ == "__main__":
    unittest.main()
