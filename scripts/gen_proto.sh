#!/usr/bin/env bash
# Generate Python code from proto/ into services/common.
# Generated files are not committed (see .gitignore); CI and builds run this first.
set -euo pipefail
# Override with UV="python -m uv" if uv is not on PATH.
cd "$(dirname "$0")/.."

out=services/common/src/mdcommon/gen
rm -rf "$out"
mkdir -p "$out"

${UV:-uv} run --no-project --with grpcio-tools==1.68.1 \
  python -m grpc_tools.protoc -I proto --python_out="$out" --pyi_out="$out" \
  proto/marketdata/v1/marketdata.proto

echo "generated $(find "$out" -name '*.py' | wc -l | tr -d ' ') Python module(s) in $out"
