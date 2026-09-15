"""Reproducible demo mode: the real FastAPI app + Celery eager mode, with
every external provider (website fetch, "LLM" extraction/drafting, email)
replaced by deterministic fixture-backed fakes (see fake_providers.py) drawn
from the SAME synthetic dataset as app/evaluation/. No OpenAI key, no
Docker, no Redis, and no real network access are required.

Uses an ISOLATED sqlite database (demo/demo_data/demo.db) -- your real
development database (DATABASE_URL in .env, typically Postgres) is never
read or written by this script. Reset demo data at any time with:

    python -m demo.reset

Usage:
    cd app
    ../venv/Scripts/python.exe -m demo.run_demo

Then open the dashboard (npm run dev in dashboard/) and import
demo/demo_leads.csv to drive: import -> research -> qualification ->
draft -> human review. Sending is blocked server-side (DEMO_MODE=1) even if
you click "send" with dry_run off.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from demo.config import DEMO_DIR, DEMO_DATABASE_URL  # noqa: E402

os.makedirs(DEMO_DIR, exist_ok=True)
os.environ["DEMO_MODE"] = "1"
os.environ["DATABASE_URL"] = DEMO_DATABASE_URL
# Placeholder values so client construction in ai_generator.py/draft_generator.py
# doesn't error -- fake_providers.apply() replaces every actual call before
# any of these would ever be used.
os.environ.setdefault("OPENAI_API_KEY", "demo-mode-not-a-real-key")

import celery_app  # noqa: E402

celery_app.app.conf.task_always_eager = True
celery_app.app.conf.task_eager_propagates = True

from demo import fake_providers  # noqa: E402

fake_providers.apply()

import database  # noqa: E402

database.init_db()

print(f"DEMO MODE: DATABASE_URL={DEMO_DATABASE_URL}", flush=True)
print("DEMO MODE: all external providers are fixture-backed; real email sending is blocked server-side.", flush=True)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=False)
