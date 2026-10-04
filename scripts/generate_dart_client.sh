#!/usr/bin/env bash
# Generate the Dart API client from the committed OpenAPI contract into clients/dart/
# (not committed; CI uploads it as an artifact).
# Uses Docker when it is available, otherwise a local Java 11+ and a cached generator jar.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=7.10.0
ARGS=(generate -g dart-dio
  --additional-properties=pubName=guzo_api,pubDescription="Guzo API client")

rm -rf clients/dart
if docker info >/dev/null 2>&1; then
  docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" \
    "openapitools/openapi-generator-cli:v$VERSION" \
    "${ARGS[@]}" -i /work/openapi/guzo-v1.json -o /work/clients/dart
else
  jar="${XDG_CACHE_HOME:-$HOME/.cache}/openapi-generator/openapi-generator-cli-$VERSION.jar"
  if [[ ! -f "$jar" ]]; then
    mkdir -p "$(dirname "$jar")"
    curl -fsSL -o "$jar" \
      "https://repo1.maven.org/maven2/org/openapitools/openapi-generator-cli/$VERSION/openapi-generator-cli-$VERSION.jar"
  fi
  java -jar "$jar" "${ARGS[@]}" -i openapi/guzo-v1.json -o clients/dart
fi

# dart-dio models are built_value classes; they need a codegen pass before they compile.
if command -v dart >/dev/null 2>&1; then
  (cd clients/dart && dart pub get && dart run build_runner build)
fi
