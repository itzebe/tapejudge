"""TapeJudge web layer (Flask app + templates + static assets)."""
from .app import app, create_app

__all__ = ["app", "create_app"]
