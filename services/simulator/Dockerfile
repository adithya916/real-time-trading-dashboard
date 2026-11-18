# Build from the repository root: the simulator needs the schema, mdcommon, ingest and the C++
# core, which is compiled here (scikit-build-core, CMake, g++) and left behind in the build stage.
#   docker build -f services/simulator/Dockerfile .
FROM python:3.12-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends g++ make \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /app

COPY proto proto
COPY scripts/gen_proto.sh scripts/gen_proto.sh
COPY services/common services/common
RUN bash scripts/gen_proto.sh

COPY core/CMakeLists.txt core/pyproject.toml core/uv.lock core/
COPY core/include core/include
COPY core/src core/src
COPY core/python core/python
COPY services/ingest services/ingest
COPY services/simulator services/simulator
WORKDIR /app/services/simulator
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
COPY --from=build /app/services/simulator/.venv /app/services/simulator/.venv
ENV PATH="/app/services/simulator/.venv/bin:$PATH"
ENTRYPOINT ["python", "-m", "simulator"]
CMD ["--bus", "nats://nats:4222", "BTC-SIM", "ETH-SIM"]
