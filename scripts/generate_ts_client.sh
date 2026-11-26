#!/usr/bin/env bash
# Generate the TypeScript API types from the committed OpenAPI contract into clients/ts/
# (not committed; CI uploads them as an artifact). The guzo-mobile app generates the same
# types with `npm run api:types`; this checks the contract still produces valid TypeScript.
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf clients/ts
mkdir -p clients/ts
# --default-non-nullable false: a field with a default stays optional in request bodies.
npx --yes openapi-typescript@7.13.0 openapi/guzo-v1.json \
  --default-non-nullable false -o clients/ts/schema.d.ts
npx --yes -p typescript@5.9 tsc --noEmit --strict clients/ts/schema.d.ts
