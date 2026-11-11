FROM python:3.14-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    postgresql-client \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./

RUN uv sync --locked --no-install-project --no-cache

COPY main.py ./
COPY etl/ ./etl/
COPY ml/ ./ml/
COPY sql/ ./sql/
COPY tests/ ./tests/

CMD ["python", "main.py", "run"]