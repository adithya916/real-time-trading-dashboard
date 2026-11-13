#!/usr/bin/env bash
# Generate Python code from proto/ into services/common.
# Generated files are not committed (see .gitignore); CI and builds run this first.
set -euo pipefail
# Override with UV="python -m uv" if uv is not on PATH.
cd "$(dirname "$0")/.."

out=services/common/src/mdcommon/gen
rm -rf "$out"
mkdir -p "$out"

# Pin the interpreter: grpcio-tools only ships wheels for some Python versions, and on a
# newer one uv would quietly compile it from source for twenty minutes. UV_NO_BUILD turns
# that into an immediate error instead.
UV_NO_BUILD=1 ${UV:-uv} run --no-project --python "${PROTO_PYTHON:-3.12}" --with grpcio-tools==1.68.1 \
  python -m grpc_tools.protoc -I proto --python_out="$out" --pyi_out="$out" \
  proto/marketdata/v1/marketdata.proto

echo "generated $(find "$out" -name '*.py' | wc -l | tr -d ' ') Python module(s) in $out"
