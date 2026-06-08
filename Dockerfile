# ── Stage 1: dependency resolver ─────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install uv --no-cache-dir

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-install-project

# ── Stage 2: runtime ──────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Non-root user for security
RUN addgroup --system app && adduser --system --ingroup app app

# Copy installed packages from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application source
COPY app/       ./app/
COPY config/    ./config/
COPY data/      ./data/
COPY database/  ./database/
COPY llm_layer/ ./llm_layer/
COPY services/  ./services/
COPY src/       ./src/
COPY utils/     ./utils/
COPY main.py    ./main.py

# Persistent log volume mount point
RUN mkdir -p /app/logs && chown app:app /app/logs

USER app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8002", \
     "--workers", "2", \
     "--timeout-keep-alive", "75", \
     "--log-level", "info"]
