> Active phase file for P5. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P5`.

# P5 — GOOGLE AUTHENTICATION FOUNDATION

## Objective

Secure Google account integration.

## Tasks

- OAuth authorization code flow
- encrypted refresh token storage
- credential refresh
- scopes
- common client factories
- integration status
- disconnect
- scope validation

Endpoints:

```text
GET /auth/google/start
GET /auth/google/callback
GET /auth/google/status
DELETE /auth/google/disconnect
```

## Tests

- OAuth state
- encryption
- refresh
- revocation
- missing scopes
- disconnect
- no token leakage

## Gate

STOP.

---
