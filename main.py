"""Vercel entrypoint for TapeJudge.

Vercel's Python runtime detects the Flask framework preset and looks for a WSGI
``app`` at a supported entrypoint; ``main.py`` is one of them, so this module is
the deployment's entrypoint. It only re-exports the single app
:func:`tapejudge.web.app.create_app` builds — no Flask route, template or
behaviour is re-implemented here, so the Vercel deployment and the container run
the same code.

The store is chosen by the same environment contract as every other host: a
``TAPEJUDGE_DATABASE_URL`` selects the durable PostgreSQL store (required on
Vercel — a function has no persistent filesystem), with ``TAPEJUDGE_DB_SCHEMA``
naming a stable schema so instances built per request share one database.
"""
from __future__ import annotations

import os
import sys

# Vercel runs this file from the project root; ensure the local package is
# importable whether or not the build pip-installed it.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tapejudge.web.app import app  # noqa: E402

# Vercel resolves `app` by name; keep the name explicit.
__all__ = ["app"]
