FROM node:20.19.1-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.9.11 AS uv
FROM python:3.12.10-slim-bookworm AS runtime
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY backend/ backend/
ARG MODELDock_SPEECH=false
RUN if [ "$MODELDock_SPEECH" = true ]; then uv sync --frozen --no-dev --no-editable --extra speech-asr --extra speech-tts --extra speech-edge; else uv sync --frozen --no-dev --no-editable; fi \
    && groupadd -g 10001 modeldock && useradd -u 10001 -g modeldock -M modeldock \
    && mkdir /data /models && chown modeldock:modeldock /data /models
COPY alembic.ini ./
COPY --from=web /web/dist /app/web/dist
ENV PATH="/app/.venv/bin:$PATH" MODELDock_DATA_DIR=/data MODELDock_STATIC_DIR=/app/web/dist
USER modeldock
EXPOSE 8000
CMD ["modeldock-api"]
