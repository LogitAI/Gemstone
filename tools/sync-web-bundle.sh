#!/usr/bin/env bash
# Copy the Wasm client's production build into api/src/main/webpack, which the server serves at `/`.
# The bundle is committed so a server checkout works without a JDK. Do not commit a local build: it never
# matches CI's Linux build byte for byte. The develop run of `.github/workflows/test.yml` (job web-bundle)
# rebuilds it and commits it when it differs (#197). Locally this script is for trying a client change.
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
