#!/usr/bin/env bash
# Copy the Wasm client's production build into api/src/main/webpack, which the server serves at `/`.
# The bundle is committed so a server checkout works without a JDK; CI rebuilds it and fails when the
# committed copy differs (`.github/workflows/app-tests.yml`, job web-bundle).
#
#   ./gradlew :app:wasmJsBrowserDistribution && tools/sync-web-bundle.sh
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
dist="$root/app/build/dist/wasmJs/productionExecutable"
dest="$root/api/src/main/webpack"
[ -d "$dist" ] || { echo "no build at $dist; run ./gradlew :app:wasmJsBrowserDistribution first" >&2; exit 1; }
rm -rf "$dest"
mkdir -p "$dest"
cp -R "$dist"/. "$dest"/
echo "synced $(find "$dest" -type f | wc -l | tr -d ' ') files into api/src/main/webpack"
