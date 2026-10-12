# Aditya BioNova Rural Smart HMS — API image (prebuilt source, no secrets).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY apps/api/requirements.txt .
RUN pip install -r requirements.txt

COPY apps/api/ .

EXPOSE 8000
# Migrations run at start (idempotent) so a fresh container self-provisions.
# Production deployments should run `alembic upgrade head` as a controlled
# release step instead (see DEPLOYMENT.md).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
