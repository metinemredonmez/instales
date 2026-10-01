# API, Swagger and generic webhooks

Open **API & Webhook** in the signed-in app (`/integrations`). The Swagger tab loads our bundled Swagger UI and uses the current session. **Try it out executes real API requests**, including writes. Swagger itself remains in English; the surrounding integration page supports Turkish and English. The large Swagger bundle loads only on this page.

The schema is available at `GET /api/v1/openapi.json` with a Bearer JWT in all environments. Anonymous `/docs` and `/openapi.json` remain development-only. Swagger requests are restricted to the configured InstiLens API origin; remote schema validation and persistent Swagger authorization are disabled. Plan and admin checks still apply. Log in through `/api/v1/auth/login`, completing MFA if enabled, to obtain a JWT for an external client. This release does not introduce permanent API keys.

## Setup

Use Node 22+ for the frontend. In the backend, run `uv run alembic upgrade head` before starting the updated API and scheduler. The migration adds only `webhook_endpoints`, `webhook_messages`, and `webhook_attempts`; these are private transport records, excluded from warehouse exports. They never create financial facts, scores or disclosures.

Optionally set `INSTILENS_WEBHOOK_SIGNING_KEY` to a stable random secret of at least 32 characters, **identically on the API and worker**. Without it, the existing JWT secret is used as the master key. Endpoint secrets are derived from this master and a random database seed; plaintext secrets are not stored. Changing the master key invalidates every endpoint's current signing secret. Back up the master securely alongside the database.

Run the existing worker with `uv run instilens scheduler`. It drains up to 10 due events every 30 seconds. A one-off drain is available with `uv run instilens webhooks-dispatch --limit 10` (maximum 50). HTTP 202 means queued, not delivered; check message history for the outcome. Deployments must run the worker as well as the API.

## Connections and API

All paths below start with `/api/v1/webhooks`. Management routes require your session JWT and only expose your connections. Up to 10 connections per account; writes are limited to 30/minute. Create a connection with its name and optional target URL. Save `signing_secret` immediately: only creation and rotation return it. The same secret authenticates incoming and outgoing events for that connection.

| Method | Path | Behavior |
| --- | --- | --- |
| GET / POST | `/endpoints` | List / create a connection |
| PATCH | `/endpoints/{id}` | Set `enabled` to pause or resume |
| POST | `/endpoints/{id}/rotate` | Replace the signing secret immediately |
| POST | `/endpoints/{id}/send` | Queue an event envelope |
| POST | `/endpoints/{id}/test` | Queue a `webhook.test` event |
| GET | `/endpoints/{id}/messages` | Newest messages; `limit` 1–100, `before` cursor |
| GET | `/messages/{id}` | Stored envelope and all delivery attempts |
| POST | `/messages/{id}/retry` | Requeue a failed outgoing event |
| POST | `/incoming/{id}` | Receive an HMAC-authenticated envelope, without a JWT |

There are no automatic subscriptions to market alerts in this release. Your client explicitly queues each outgoing event. Incoming events are saved in the inbox for reading through the API/UI; they do not execute business actions. Target URLs are fixed per connection; create another connection to use a different destination. Pause unused connections. Pausing blocks incoming requests and new sends and holds pending deliveries; an in-flight delivery may finish.

## Event and signature contract

```json
{"id":"order-123-updated","type":"custom.updated","data":{"reference":"external-123"}}
```

`id` (1–128) and `type` (1–80) use letters, digits, `.`, `_`, `:`, `-`. `data` is a JSON object (defaults to `{}`); extra top-level fields are rejected. Requests require `Content-Type: application/json`, no compression, and at most 64 KiB. Financial decimals in generic payloads should be strings. Envelopes are stored as canonical JSON; the incoming signature is always verified against the original bytes first.

The headers are `X-Instilens-Timestamp` (Unix seconds) and `X-Instilens-Signature` (`sha256=` plus hex HMAC-SHA256). Sign the bytes `timestamp + "." + raw_body` using the UTF-8 signing secret. Incoming timestamps must be within 300 seconds. Outgoing requests also contain `X-Instilens-Id` equal to the event id. Receivers must verify the signature, timestamp and deduplicate the event id.

Python sender example (environment variables hold your URL and secret):

```python
import hashlib, hmac, json, os, time, urllib.request

body = json.dumps({"id": "external-123", "type": "custom.updated", "data": {"value": "12.45"}}).encode()
ts = str(int(time.time()))
sig = hmac.new(os.environ["WEBHOOK_SECRET"].encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
request = urllib.request.Request(os.environ["WEBHOOK_URL"], data=body, method="POST", headers={
    "Content-Type": "application/json",
    "X-Instilens-Timestamp": ts,
    "X-Instilens-Signature": "sha256=" + sig,
})
with urllib.request.urlopen(request, timeout=10) as response:
    print(response.status)
```

Repeating an id with the same canonical envelope is successful with `duplicate: true`; different content under that id returns 409. Deduplication is per connection and direction. Incoming limits: 240 requests/minute/IP, 120/minute/connection, 10,000 authenticated requests/day/account (including repeats). At most 500 pending/delivering outgoing events per connection.

## Delivery behavior

Outgoing destinations must use HTTPS on port 443, without credentials or fragments. Every attempt resolves DNS and rejects any non-public result. The TLS connection is pinned to a validated address and verifies the original hostname. Redirects are not followed; system proxy settings are not used. Requests time out after 10 seconds of socket inactivity. Response bodies are neither read nor stored.

2xx marks delivered. Network errors, 408, 429 and 5xx retry after 60, 300, 900 and 3600 seconds, up to five recorded attempts. Other statuses (including redirects) and disallowed targets fail immediately. Manual retry keeps the same id and history; it uses any remaining automatic retry budget, or makes one additional attempt after that budget is exhausted. An inactive account's deliveries are held.

Each delivery uses a database claim with a three-minute lease. Another worker cannot claim an active lease; an expired lease is recoverable after a crash. Delivery is **at least once**: a crash after remote acceptance but before recording success can cause a duplicate. Receiver idempotency is mandatory. No automatic retention/deletion is currently applied to message history; monitor database storage.

Implementation references: [FastAPI custom docs](https://fastapi.tiangolo.com/how-to/custom-docs-ui-assets/), [Swagger UI configuration](https://swagger.io/docs/open-source-tools/swagger-ui/usage/configuration/).
