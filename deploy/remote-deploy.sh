#!/usr/bin/env bash
# Runs on the server for each deploy (the deploy workflow sends it over SSH): fetch the commit
# that was pushed, rebuild what changed and restart. Docker's build cache keeps it quick.
set -euo pipefail
cd "${DIR:-$HOME/rtd}"
git fetch --quiet origin main
git reset --quiet --hard "${1:-origin/main}"
sudo docker compose -f compose.yaml -f deploy/compose.prod.yaml --profile feeds up -d --build --wait --wait-timeout 900 --remove-orphans
sudo docker image prune -f >/dev/null
echo "deployed $(git rev-parse --short HEAD)"
