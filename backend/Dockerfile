FROM python:3.11-slim AS base

WORKDIR /app

# Build-time deps needed by cryptography, hiredis, asyncpg wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libssl-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry into a fixed path so it doesn't pollute site-packages
ENV POETRY_VERSION=2.4.1 \
    POETRY_HOME=/opt/poetry \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"

# Install dependencies first (layer-cached unless lock file changes)
COPY pyproject.toml poetry.lock* ./
RUN poetry install --no-root --only main

# Copy application source
COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
