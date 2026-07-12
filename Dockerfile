FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY prompts ./prompts
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 masterclaw \
    && mkdir -p /app/data \
    && chown -R masterclaw:masterclaw /app
USER masterclaw

CMD ["masterclaw", "serve"]
