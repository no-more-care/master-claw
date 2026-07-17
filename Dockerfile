FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY prompts ./prompts
RUN pip install --no-cache-dir uv==0.9.26 \
    && uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    OPENHANDS_SUPPRESS_BANNER=1

RUN useradd --create-home --uid 10001 masterclaw \
    && mkdir -p /app/data /app/backups \
    && chown -R masterclaw:masterclaw /app
USER masterclaw

CMD ["masterclaw", "serve"]
