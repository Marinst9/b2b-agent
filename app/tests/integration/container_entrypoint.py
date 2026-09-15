"""Entrypoint for the worker-integration container (see
docker-compose.integration.yml). Applies safe fakes for every external
provider BEFORE the real Celery worker starts consuming tasks, then runs an
actual worker -- this is what lets the real-broker integration tests
exercise a genuine, separate Linux worker process (not an in-process
pytest thread, not eager mode) without ever hitting a real website, LLM, or
SMTP server.

Run as: python -m tests.integration.container_entrypoint
"""
from tests.integration import container_fakes

container_fakes.apply()

import celery_app  # noqa: E402  (must come after container_fakes.apply())

if __name__ == "__main__":
    celery_app.app.worker_main(["worker", "--loglevel=info", "--concurrency=2"])
