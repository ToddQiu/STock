FROM node:22-bookworm-slim AS translation
WORKDIR /translation_runtime
COPY translation_runtime/package.json translation_runtime/package-lock.json ./
RUN npm ci --omit=dev

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 STOCK_RESOURCE_ROOT=/app
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libstdc++6 ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home stock \
    && mkdir -p /var/lib/stock && chown stock:stock /var/lib/stock
COPY --from=translation /usr/local/bin/node /usr/local/bin/node
COPY --from=translation /translation_runtime/node_modules /app/translation_runtime/node_modules
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir '.[translation]'
COPY translation_runtime/worker.mjs translation_runtime/prepare.mjs ./translation_runtime/
COPY scripts/prepare_hosted_translation.py ./scripts/
RUN python scripts/prepare_hosted_translation.py --root /app
COPY config.example.json ./
USER stock
EXPOSE 8080
CMD ["python", "-m", "quant_news_monitor.hosted", "--host", "0.0.0.0"]
