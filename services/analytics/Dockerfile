# Build from the repository root: analytics needs mdcommon, ingest (for PyBook) and the schema.
#   docker build -f services/analytics/Dockerfile .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /app

COPY proto proto
COPY scripts/gen_proto.sh scripts/gen_proto.sh
COPY services/common services/common
RUN bash scripts/gen_proto.sh

COPY services/ingest services/ingest
COPY services/analytics services/analytics
WORKDIR /app/services/analytics
RUN uv sync --frozen --no-dev --no-editable

ENV PATH="/app/services/analytics/.venv/bin:$PATH"
ENTRYPOINT ["python", "-m", "analytics.service"]
CMD ["--bus", "nats://nats:4222"]
