# Linux container image for the Celery worker/beat process. The FastAPI app
# itself keeps running directly on the host (uvicorn) as before -- only the
# background job processing runs in containers, alongside a containerized
# Redis broker (see ../docker-compose.yml).
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Overridden by docker-compose.yml's `command:` for the worker vs beat
# services; this default runs a worker if the image is used standalone.
CMD ["celery", "-A", "celery_app", "worker", "--loglevel=info", "--concurrency=2"]
