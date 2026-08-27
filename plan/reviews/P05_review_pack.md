# P5 Review Pack — Google Authentication Foundation

## 1. Executive Summary

P5 adds a provider-agnostic Google OAuth authorization-code foundation with PKCE state
protection, encrypted local token storage, refresh/revocation, exact scope validation,
connection status, disconnect, and a common authorized client factory. The implementation
does not add Gmail, Contacts, Calendar, or Drive business operations; those remain P6–P8.

## 2. Implemented Scope

### 2.1. Credential configuration

- Extended typed Google settings with OAuth endpoints, scopes, redirect URI, state TTL,
  refresh skew, and token-key configuration.
- Added the user's `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` values to the local `.env`
  from the downloaded JSON. The `.env` and source JSON are both ignored locally, and the JSON
  path remains configured as a fallback.
- Added `GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback`, matching the
  redirect URI registered in the user's downloaded Google client configuration.
- Added `.secrets/google_token.key` as the local Fernet key location and ignored `.env`,
  `.secrets/`, and `client_secret*.json` in `.gitignore`.
- Flat `GOOGLE_*` names remain supported while the typed nested settings stay the runtime
  API. Secret fields are excluded from settings serialization.

### 2.2. OAuth flow (`app/services/google_auth.py`)

- Authorization URL builder with offline access, consent, exact configured scopes, and PKCE
  S256 challenge.
- Single-use, TTL-bound, user-bound state records through `OAuthStateStore` and the
  deterministic `InMemoryOAuthStateStore` implementation.
- Authorization-code exchange and optional OIDC userinfo lookup through an injected HTTP
  transport, allowing deterministic mocks without provider SDK coupling.
- Generic provider errors never include authorization codes or token values.

### 2.3. Encrypted token lifecycle

- Added `FernetTokenCipher` with generated private local key-file support and generic
  decryption errors.
- Added `google_integrations` ORM storage and Alembic revision `0004`.
- Refresh and access tokens are stored only as encrypted ciphertext; provider token models
  exclude plaintext token fields from serialization.
- Expired access tokens are refreshed, re-encrypted, and never returned by status endpoints.
- Invalid refresh authorization marks the local integration revoked.

### 2.4. Scopes, status, factory, and disconnect

- Exact scope normalization and missing-scope validation.
- Authorization responses with omitted scopes are treated as missing, not implicitly granted;
  refresh responses may reuse the previously validated stored scope set when Google omits it.
- Safe status response reports connection, local health, email, granted scopes, missing scopes,
  expiry, and revocation time without token material.
- `GoogleClientFactory` creates an authorized common client for later deterministic Google
  adapters.
- Disconnect revokes the refresh token and deletes local encrypted storage; an already-invalid
  token is treated idempotently, while unrelated revocation errors are preserved.

### 2.5. API endpoints

Both the configured local callback path and the API-prefixed path are available:

```text
GET    /auth/google/start
GET    /auth/google/callback
GET    /auth/google/status
DELETE /auth/google/disconnect

GET    /api/v1/auth/google/start
GET    /api/v1/auth/google/callback
GET    /api/v1/auth/google/status
DELETE /api/v1/auth/google/disconnect
```

The start endpoint returns a redirect to Google. Callback, status, and disconnect responses
contain no OAuth token fields.

## 3. Verification

### Automated tests

- **155 tests passed** across the full suite.
- Added tests for OAuth state/PKCE/single-use behavior, Fernet encryption, credential-file
  loading, token serialization redaction, callback persistence, refresh, revocation,
  disconnect, missing scopes, status leakage, migration `0004`, and the configured start route.
- The full suite retains one existing SQLite `ResourceWarning` from a pre-existing Redis/
  persistence test; it does not fail the suite.

### Quality gates

- Ruff: passed with 0 errors.
- Pyright: passed with 0 errors, 0 warnings, 0 informations.
- Full pytest suite: 155 passed.
- Provider network calls were mocked; the real Google consent screen was not completed in this
  agent run because it requires interactive user sign-in and consent.

## 4. Review Findings and Fix Verification

### HIGH

**0 open findings.** No plaintext token persistence, token response leakage, scope widening,
or OAuth-state bypass remains in the reviewed P5 path.

### MEDIUM

**M-01 — Missing authorization-response scope was previously treated as requested — FIXED.**

The token parser originally fell back to the requested scope list whenever Google omitted the
`scope` field. That could incorrectly mark a grant as complete. Authorization-code responses
now require explicit returned scopes; only refresh responses may reuse an already validated
stored scope set. A regression test covers the missing-scope callback.

**M-02 — Token-model/config serialization could expose plaintext secrets — FIXED.**

Plaintext token fields and client-secret settings are now excluded from Pydantic serialization,
while internal service access remains explicit. Tests assert that serialized models and status
responses contain neither access nor refresh token values.

**M-03 — Status health did not detect a corrupted encryption key/token — FIXED.**

Status now verifies that the encrypted refresh-token material can be decrypted and reports local
health false if it cannot, without exposing the underlying error or ciphertext.

**M-04 — Any HTTP 400 from revocation was previously accepted — FIXED.**

Disconnect treats only an empty/`invalid_token` 400 as an idempotent already-revoked result;
other provider errors remain failures and local storage is retained for retry.

**M-05 — Existing migration assertions still expected P3 revision `0003` — FIXED.**

Unit and PostgreSQL migration contract tests now assert P5 revision `0004` and the new
`google_integrations` table.

### LOW

**L-01 — Default OAuth state store is process-local — ACCEPTED for P5.**

The storage contract is injectable and the default is deterministic in-memory storage. A
multi-worker deployment should supply a shared Redis-backed implementation before relying on
multiple application workers; the current consequence is an expired/unknown state failure, not
scope or token exposure.

**L-02 — Local key-file permission hardening is best-effort on Windows — ACCEPTED for local P5.**

The generated key is kept under ignored `.secrets/` and chmod is applied where supported. A
production deployment should provide `GOOGLE_TOKEN_ENCRYPTION_KEY` or a managed secret store and
use host-level filesystem ACLs.

## 5. Security / Architecture Self-Review

- OAuth state is opaque, user-bound, TTL-bound, single-use, and PKCE-backed.
- Client credentials are stored in the ignored local `.env`; the source JSON is also ignored and
  neither credential value is printed by the application checks or review output.
- Refresh/access tokens are encrypted at rest and excluded from API/status/serialized output.
- Provider errors, logs, and domain errors do not include token material.
- Scope checks are exact and fail closed for missing authorization-response scopes.
- No Gmail, Contacts, Calendar, Drive, PolicyEngine, or future-phase business operation was
  implemented.

## 6. Final Verdict

**PASS — HIGH: 0 open, MEDIUM: 0 open, LOW: 2 accepted.**

P5 requirements are implemented and verified with mocked provider calls. A real browser OAuth
consent is the remaining manual integration action for the user environment.

## 7. Gate Status

**WAITING FOR USER REVIEW — P5**

Do not begin P6 or later until the user explicitly sends `APPROVED P5`.
