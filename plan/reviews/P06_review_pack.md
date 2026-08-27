# P6 Review Pack — Communication Tools: Gmail + Google Contacts

## 1. Executive Summary

P6 adds deterministic Gmail and Google Contacts capabilities behind a provider-neutral
domain boundary. Google wire responses are converted into typed models before they reach
the communication service or tool wrapper. The implementation has no LLM dependency and
does not broaden the P6 scope into Calendar, Drive, RAG, or specialist-agent runtime work.

## 2. Implemented Scope

### 2.1. Typed domain contracts (`app/domain/models/communication.py`)

- Added generic `Page[T]` pagination envelopes and typed Gmail/Contacts page models.
- Added normalized `EmailAddress`, `GmailMessage`, `GmailThread`, draft, send, mutation,
  label, and contact models.
- Message models expose normalized headers, sender/recipients, timestamps, plain text,
  optional HTML, attachment presence, and thread relationships.
- `ContactResolution` encodes `exact`, `ambiguous`, and `not_found` outcomes and rejects
  a shape that silently attaches a contact to a non-exact result.

### 2.2. Shared Google adapter boundary (`app/integrations/google_common.py`)

- Added bounded retry policy with capped exponential delay and safe `Retry-After` parsing.
- Normalized provider responses into domain errors for authentication, permission, not found,
  validation, rate limit, and external-service failures.
- Provider error details contain operation/status/retry metadata only; raw response bodies are
  not copied into domain errors.
- Retries default to idempotent HTTP methods. Non-idempotent POST operations are not retried
  automatically; explicitly idempotent Gmail label/trash mutations opt in.

### 2.3. Gmail adapter (`app/integrations/google_gmail.py`)

Implemented deterministic operations:

```text
search_messages
get_message
get_thread
list_threads
create_draft
update_draft
delete_draft
send_draft
reply
forward
archive
trash
add_label
remove_label
```

- Gmail search/thread pages preserve continuation tokens.
- Full and raw RFC 822 responses are normalized into the same message model.
- MIME multipart traversal selects plain text when available, retains HTML, detects
  attachments, and converts malformed/untrusted HTML to readable text without exposing tags.
- Outbound messages use RFC 2822-compatible MIME generation and base64url encoding.
- Reply recipient, thread ID, subject, `In-Reply-To`, and `References` are derived
  deterministically from the original message; no contact or LLM guess is used.
- Recipient/header validation rejects blank addresses and line-break injection.

### 2.4. Contacts adapter and domain service

`app/integrations/google_contacts.py` implements typed `search` and `get` operations with
People API pagination. `app/services/communication.py` adds:

- `resolve_person` with bounded page traversal and stable de-duplication;
- exact email/name matching;
- explicit ambiguity when multiple candidates remain;
- no guessed contact when the result is not an exact deterministic match;
- OAuth-backed construction through `GoogleOAuthService.for_user` scope validation.

The People search page-size boundary is validated at 30, matching the provider contract.

### 2.5. Tool wrappers and action metadata

`app/tools/google_communication.py` provides 17 registered tool definitions and a typed
`GoogleCommunicationTools` wrapper. Every mutation has a canonical action class:

| Operation class | Gmail operations |
|---|---|
| `SAFE_WRITE` | create/update draft, archive, add/remove label |
| `EXTERNAL_COMMUNICATION` | send draft, reply, forward |
| `DESTRUCTIVE` | delete draft, trash |

Read operations are explicitly `READ`, Gmail tools require the P5 Gmail scope, and Contacts
tools require the P5 Contacts read-only scope. Read-only runtime views reject mutation
execution as a defense-in-depth check; approval orchestration remains the explicitly planned
P18 concern.

## 3. Verification

### Focused P6 tests

- **12 tests passed** in `tests/unit/integrations/test_google_communication.py`.
- Covered exact and ambiguous people, multi-page resolution, Gmail message/thread
  normalization, malformed HTML, raw RFC 822 normalization, retries, no-retry sends,
  provider authentication errors, Contacts page limits, all Gmail write request paths,
  registry action classification, and read-only mutation denial.

### Repository checks

- **167 tests passed** in the full suite.
- Ruff: passed with 0 errors.
- Pyright: passed with 0 errors, warnings, or informations.
- Python compilation: passed for `app` and `tests`.
- Provider calls were fully mocked; no real Gmail or Contacts data was accessed.
- One non-blocking SQLite connection finalizer warning remains in the existing test
  environment; it does not cause a test failure.

## 4. Review Findings and Remediation

### HIGH

**0 open findings.** No token material is returned by P6 models/tools, provider payloads
are not copied into errors, mutation metadata cannot be omitted from registry declarations,
and ambiguous contacts are never silently selected.

### MEDIUM

**M-01 — Automatic retries could duplicate non-idempotent Gmail writes — FIXED.**

The initial adapter helper retried transient statuses for all methods. That was unsafe for
draft creation and especially send/reply/forward when Google may have accepted a request
before the response was lost. The helper now retries idempotent methods by default, disables
automatic retry for non-idempotent communication sends/creation, and explicitly enables it
only for state-idempotent trash/label mutations. A regression test proves `send_draft` makes
one request after a transient 503.

**M-02 — Contacts page-size declaration exceeded the provider boundary — FIXED.**

The adapter and tool schemas originally advertised 1000 results for `people.searchContacts`.
The boundary is now validated and advertised as 30, with synchronized service defaults and
a regression test for page size 31.

### LOW

**L-01 — People API search warm-up is not automatic — ACCEPTED for P6.**

The [People API `searchContacts` contract](https://developers.google.com/people/api/rest/v1/people/searchContacts)
recommends an empty-query warm-up before `searchContacts` so its contact cache is fresh.
The deterministic adapter does not add that extra provider call yet; a later
production hardening pass can add a shared, bounded warm-up gate. Search pagination and
resolution remain deterministic in the current implementation.

**L-02 — Retry count is adapter-instance telemetry — ACCEPTED for P6.**

The wrapper reports the adapter's latest retry count. Under concurrent calls on the same
service instance, this diagnostic field could be less precise than a per-request trace,
but it cannot change provider behavior, returned data, or action authorization. P3/P11
observability hardening can move it into request-local execution metadata.

## 5. Security / Architecture Self-Review

- Gmail and Contacts wire payloads do not cross directly into agents; adapters normalize them.
- External email content is treated as data; no content is interpreted as instructions or
  capability changes.
- Authorization headers remain inside the common authorized client; P6 response models and
  errors contain no access/refresh token fields.
- MIME body generation validates recipient/header boundaries and does not log raw messages.
- Retry policy avoids duplicate external communication.
- Contact resolution is deterministic, bounded, de-duplicated, and ambiguity-preserving.
- No Calendar, Drive, RAG, LLM, Supervisor, or PolicyEngine implementation was added.

## 6. Final Verdict

**PASS — HIGH: 0 open, MEDIUM: 0 open, LOW: 2 accepted.**

All P6 requirements are implemented and verified with deterministic provider mocks.

## 7. Gate Status

**WAITING FOR USER REVIEW — P6**

Do not begin P7 or later until the user explicitly sends `APPROVED P6`.
