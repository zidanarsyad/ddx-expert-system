"""Entry point for Vercel: the DDX-ES web interface as an ASGI application.

Vercel's Python runtime looks for a top-level `app` in index.py. To run the interface locally, use `ddxes serve`.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

from ddxes.web.app import app  # noqa: E402,F401
