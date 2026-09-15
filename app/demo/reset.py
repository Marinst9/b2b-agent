"""Resets demo mode data ONLY -- deletes demo/demo_data/demo.db (if it
exists) and recreates an empty schema. Never touches DATABASE_URL from
.env (the real development database); this script never even imports it,
it operates purely on the hardcoded demo db file path from demo/config.py.

Usage:
    cd app
    ../venv/Scripts/python.exe -m demo.reset
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from demo.config import DEMO_DIR, DEMO_DB_PATH, DEMO_DATABASE_URL  # noqa: E402


def main() -> int:
    os.makedirs(DEMO_DIR, exist_ok=True)
    if os.path.exists(DEMO_DB_PATH):
        os.remove(DEMO_DB_PATH)
        print(f"Removed {DEMO_DB_PATH}")
    else:
        print(f"No existing demo database at {DEMO_DB_PATH} (nothing to remove)")

    os.environ["DATABASE_URL"] = DEMO_DATABASE_URL
    import database

    database.init_db()
    print(f"Recreated empty demo schema at {DEMO_DB_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
