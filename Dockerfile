# One image, one process: `portolan serve` serves the API and the built frontend on port 8000.
#   docker compose up --build        (see docker-compose.yml)

# --- build the frontend ------------------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# --- the app -----------------------------------------------------------------------------
FROM python:3.12-slim
# git: Portolan shallow-clones a paper's code to index it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/
RUN cd backend && uv sync --frozen --no-dev --no-install-project
COPY backend/ backend/
RUN cd backend && uv sync --frozen --no-dev
COPY --from=web /web/dist frontend/dist
COPY portolan.toml.example .env.example ./

# Everything Portolan stores (database, cloned code, uploads) lives in /data: mount a volume there.
ENV PORTOLAN_HOME=/data \
    PATH="/app/backend/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
RUN useradd --create-home portolan && mkdir /data && chown portolan /data
USER portolan
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

WORKDIR /app/backend
CMD ["portolan", "serve", "--host", "0.0.0.0", "--port", "8000"]
