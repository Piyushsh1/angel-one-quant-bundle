# ==============================================================================
#  Barbell auth API
# ==============================================================================
# Python 3.12: the pinned pydantic-core / psycopg wheels are published for it,
# and it matches the deployment target. (3.14 on the host has no prebuilt
# wheels yet, which is exactly why the app runs in a container.)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first for layer caching.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app/ ./app/

# Run unprivileged.
RUN useradd --create-home --uid 10002 api && chown -R api:api /app
USER api

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
