# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.12
ARG POETRY_VERSION=2.4.1

FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ARG POETRY_VERSION

ENV PIP_NO_CACHE_DIR=1 \
    POETRY_NO_INTERACTION=1

WORKDIR /app

RUN pip install "poetry==${POETRY_VERSION}" \
    && python -m venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}" \
    VIRTUAL_ENV=/opt/venv \
    POETRY_VIRTUALENVS_CREATE=false

COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root --no-ansi

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv

WORKDIR /app

RUN groupadd --gid 10001 app \
    && useradd \
        --create-home \
        --gid app \
        --shell /usr/sbin/nologin \
        --uid 10001 \
        app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app . .

USER app

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
