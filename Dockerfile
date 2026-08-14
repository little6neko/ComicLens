FROM node:22-bookworm-slim AS web-builder

WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime

ARG VERSION=0.1.3

LABEL org.opencontainers.image.version="${VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    COMICLENS_HOST=0.0.0.0 \
    PORT=8233 \
    COMICLENS_DATA_DIR=/app/data \
    COMICLENS_STATIC_DIR=/app/web/dist

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates curl fonts-noto-cjk gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 comiclens \
    && useradd --system --uid 10001 --gid comiclens --home-dir /app comiclens

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY app ./app
RUN pip install .
COPY --from=web-builder /build/web/dist ./web/dist
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /app/data \
    && chown -R comiclens:comiclens /app

EXPOSE 8233
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl --fail --silent http://127.0.0.1:8233/health >/dev/null || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn app.main:app --host \"${COMICLENS_HOST:-0.0.0.0}\" --port \"${PORT:-8233}\" --workers 1 --proxy-headers --forwarded-allow-ips=*"]
