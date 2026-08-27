# P07 Review Pack — Calendar Tools

## Scope

P07 implements the deterministic Calendar layer only:

```text
Google Calendar adapter -> CalendarService -> Calendar tools
```

No CalendarAgent, Supervisor, approval workflow, or later-phase behavior was added.

## Delivered

- Provider-neutral Calendar models for timed and all-day events, attendees, busy intervals, free/busy responses, and slots.
- Timezone validation at the boundary and UTC normalization for arithmetic.
- Google Calendar adapter with:
  - event listing/search/get;
  - free/busy query with pagination-compatible event reads;
  - create/update/delete;
  - attendee add/remove;
  - separate read-only and mutation scope declarations, with scope-aware client construction;
  - fail-closed handling for per-calendar free/busy errors and missing response entries;
  - ETag-guarded attendee PATCH requests with `If-Match`;
  - normalized provider errors;
  - bounded retries for safe reads, including the POST-based free/busy query;
  - no automatic retries for external mutations.
- Deterministic slot finder with fixed duration/step, overlap merging, clipping, and max-result bounds.
- Ten `calendar.*` tool definitions with read-only enforcement and explicit mutation metadata.
- P07 exports and focused tests.

## Requirement matrix

| Requirement | Evidence | Result |
|---|---|---|
| All ten tools | `app/tools/google_calendar.py`, registry test | PASS |
| Timezone aware | `CalendarEventTime`, `normalize_aware_datetime`, timezone tests | PASS |
| No LLM arithmetic | `find_deterministic_free_slots` uses interval arithmetic only | PASS |
| Deterministic slot finder | fixed step, sorted/merged busy intervals, bounded output | PASS |
| Conflict checks | overlapping busy intervals and all-day conversion tests | PASS |
| Mutation classification | create/update/add/remove = external communication; delete = destructive | PASS |
| Least-scope declarations | read tools use `calendar.readonly`; mutation tools use `calendar` | PASS |
| Logical-operation telemetry | retry metadata aggregates every retry across one tool action | PASS |
| Provider contract | RFC3339 bounds, `sendUpdates`, PUT/PATCH/POST paths match Google Calendar API contracts | PASS |

## Severity review

### HIGH

Open findings: **0**

- No unguarded Calendar mutation path was found in the tool wrapper.
- Read-only tool views reject create/update/delete/add/remove before any provider request.
- Mutation payloads are not retried automatically, preventing duplicate invitations or cancellations.

### MEDIUM

Open findings: **0**

The review fixes included:

1. POST-based free/busy reads now opt into the bounded retry policy.
2. Timed event serialization now emits the instant in the declared IANA timezone instead of combining a UTC `Z` value with a different `timeZone` field.
3. Free/busy requests reject more than Google’s 50-calendar expansion limit before making a provider call.
4. Attendee email validation rejects malformed or whitespace-containing addresses.
5. Free/busy slot finding now fails closed when any requested calendar reports an error or is absent from the provider response.
6. Attendee read-modify-write now carries the fetched event ETag in `If-Match`; Google’s 412 precondition response is surfaced for retry/review.

### LOW

Open findings: **0**

The two requested low-severity findings are fixed:

1. Read tools now declare `https://www.googleapis.com/auth/calendar.readonly`; mutation tools declare the full `calendar` scope. `GoogleCalendarTools.for_user(tool_name=...)` and `CalendarService.for_user(required_scopes=...)` can construct a client for the selected scope, while a full Calendar grant is accepted as covering the read-only requirement but not vice versa.
2. Retry metadata is collected per logical Calendar tool operation, so retries from a read-modify-write GET are retained when the subsequent attendee PATCH succeeds or fails. The collector is async-context local to avoid cross-request telemetry mixing.

Design note, intentionally unchanged: free/busy input is modeled as direct calendar identifiers; Google group-expansion response data is not exposed as a separate domain contract. Direct calendar IDs remain fully supported and bounded.

## Verification

- Focused P07 tests: **20 passed**.
- Full test suite: **188 passed, 1 non-P07 test-environment resource warning**.
- Ruff: **All checks passed**.
- Pyright: **0 errors, 0 warnings, 0 informations**.
- `compileall -q app tests`: passed.
- Live write calls were not sent to the user’s calendar; provider behavior was verified with deterministic transports to avoid creating, changing, or deleting real events during review.

Provider contract references:

- [Events list](https://developers.google.com/calendar/api/v3/reference/events/list)
- [Events insert](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert)
- [Events update/patch](https://developers.google.com/workspace/calendar/api/v3/reference/events/update)
- [Events patch](https://developers.google.com/workspace/calendar/api/v3/reference/events/patch)
- [Freebusy query](https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query)

## Verdict

**PASS after all requested changes — HIGH: 0, MEDIUM: 0, LOW: 0 open.**

P07 is complete and ready for user review. Do not begin P08 until the user sends `APPROVED P7`.
