# Guzo API

Backend for Guzo: pre-booked rides in Ethiopia, starting with Bole airport pickups.
FastAPI, Beanie and MongoDB as a modular monolith, with Redis for sign-in codes, rate
limits and background jobs.

## Run it

```sh
docker compose up -d --wait mongo redis      # Mongo runs as a single-node replica set
uv sync
uv run python -m guzo.catalog.seed --example-prices
uv run uvicorn guzo.main:app --reload         # http://localhost:8000/docs
uv run arq guzo.worker.WorkerSettings         # outbox delivery and expiry
```

`docker compose --profile app up --build` runs the API and worker in containers instead.

Create an operations account (prints a TOTP secret for an authenticator app):

```sh
uv run python -m guzo.identity.cli ops@example.com "Your Name"
```

## Check it

```sh
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

Tests need the Mongo replica set (transactions) and use an in-memory Redis. Point them
at another Mongo with `GUZO_TEST_MONGO_URL`. `tests/test_booking_lifecycle.py` is the
M0 exit test: it books, pays through the fake provider and reaches every booking state.

## Layout

| Module | Owns |
|---|---|
| `identity` | Users, phone sign-in codes, ops login with password and TOTP |
| `catalog` | Cities, zones, places, products and their policies, zone prices, vehicle capacity. Ops edit these under `/v1/ops/catalog` |
| `pricing` | `PricingStrategy` interface and quotes. Only `zone_fixed` is implemented |
| `bookings` | Booking model, the transition table (`state_machine.py`), refund policies |
| `payments` | Payments, refunds, double-entry ledger, provider webhooks |
| `events` | Transactional outbox and the handlers that react to events |
| `partners` | Referral codes for hotels, hosts and agencies, and commission attribution |
| `fleet` | Driver vehicles |
| `audit` | Append-only log of operations actions |
| `providers` | Interfaces for payments, messaging, flight status and identity checks, plus fakes |

Rules worth knowing before changing things:

- Money is `Money(amount_minor: int, currency)`. No floats anywhere.
- `bookings.service.apply_transition` is the only code that changes a booking's status. It
  checks the table, appends `status_history` and writes the outbox event in one transaction.
- Booking code never calls a provider for side effects. It emits an event; a handler in
  `events/handlers.py` sends the message or refund. Handlers must be idempotent.
- All time goes through `guzo.common.clock.utcnow()` and is stored in UTC.

## API contract

Routes live under `/v1`. `openapi/guzo-v1.json` is the committed contract; regenerate it
with `uv run python scripts/export_openapi.py` after changing the API (a test fails if
it is stale). `scripts/generate_dart_client.sh` builds the Dart client from it into
`clients/dart/`, using Docker or, without it, a local Java and Dart.

With the fake providers, sign-in codes and other messages are written to the API and
worker logs (`fake sms to +251...`).
