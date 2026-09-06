# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS builder
RUN pip install --no-cache-dir poetry==2.4.1 && python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" VIRTUAL_ENV=/opt/venv POETRY_VIRTUALENVS_CREATE=false POETRY_NO_INTERACTION=1
WORKDIR /build
COPY inference/pyproject.toml inference/poetry.lock ./
RUN poetry install --only main --no-root --no-ansi

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install --yes --no-install-recommends ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app
ENV PATH="/opt/venv/bin:${PATH}" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 HF_HUB_DISABLE_PROGRESS_BARS=1 TORCH_NUM_THREADS=2 MODEL_DIR=/app/models
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app backend/app/genre_classification /app/app/genre_classification
COPY --chown=app:app inference /app/inference
COPY --from=model-artifacts --chown=app:app / /app/models/
USER app
EXPOSE 8080
CMD ["uvicorn", "inference.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
