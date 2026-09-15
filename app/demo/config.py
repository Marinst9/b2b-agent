"""Shared constants for demo mode -- imported by both run_demo.py and
reset.py so the two scripts can never drift apart on which file is the
demo database.
"""
import os

DEMO_DIR = os.path.join(os.path.dirname(__file__), "demo_data")
DEMO_DB_PATH = os.path.join(DEMO_DIR, "demo.db")
DEMO_DATABASE_URL = f"sqlite:///{DEMO_DB_PATH}"
