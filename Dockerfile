# uv-based Python image; dependencies are locked by uv.lock.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /srv

# Install dependencies first so code edits don't bust the layer cache.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app

# Run as a non-root user (parity with the old node image).
RUN useradd -m bot && chown -R bot:bot /srv
USER bot

ENV PATH="/srv/.venv/bin:$PATH"
EXPOSE 3000
# ONE worker, deliberately. The per-sender queues, webhook dedupe, session store,
# blocklist and follow-up timers are process-local; a second worker would double-reply to
# redelivered webhooks and interleave one customer's messages. Scale by making the single
# process faster, never by adding workers.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3000", "--workers", "1"]
