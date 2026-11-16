# Build from the repository root: the image needs proto/, the codegen script and mdcommon.
#   docker build -f services/ingest/Dockerfile .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /app

COPY proto proto
COPY scripts/gen_proto.sh scripts/gen_proto.sh
COPY services/common services/common
RUN bash scripts/gen_proto.sh

COPY services/ingest services/ingest
WORKDIR /app/services/ingest
RUN uv sync --frozen --no-dev --no-editable

ENV PATH="/app/services/ingest/.venv/bin:$PATH"
ENTRYPOINT ["python", "-m", "ingest.service"]
CMD ["--bus", "nats://nats:4222", "kraken:BTC-USD,ETH-USD"]
