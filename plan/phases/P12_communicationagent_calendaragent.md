> Active phase specification for P12. Read `../MASTER_PLAN.md` first.
> Do not implement any downstream phase (P13+) until the user sends `APPROVED P12`.

# P12 — COMMUNICATIONAGENT + CALENDARAGENT SPECIFICATION

## 1. Objective & Architectural Scope

Phase 12 builds the first two production domain specialists on top of the generic Specialist Agent Runtime delivered in P11:
1. **CommunicationAgent**: Specializes in email triage, thread summarization, contact resolution, and draft composition over Gmail and Google Contacts.
2. **CalendarAgent**: Specializes in schedule querying, conflict detection, and deterministic multi-attendee free-slot calculation over Google Calendar.

Both agents operate strictly within the **least-privilege boundaries** established in P04/P07/P08/P11:
- Tools are injected dynamically via `CapabilityGate.for_agent()`.
- Unambiguous read queries execute via **Direct Mode** (0 LLM calls) or 1-shot tool execution.
- Complex or ambiguous tasks use **Bounded ReAct Mode** bounded by `LatencyBudgetManager` and `RepeatGuard`.
- All mutation operations (`gmail.send_draft`, `gmail.trash`, `calendar.create_event`, `calendar.delete_event`, etc.) are gated fail-closed: without a validated `approval_token` or `approval_id`, they MUST return a `ProposedAction` or fail with `PermissionDeniedError`.

---

## 2. Entry Criteria

Before P12 implementation begins, all of the following MUST be satisfied:
- [x] **APPROVED P11**: Generic Specialist Agent Runtime (`SpecialistRuntime`, `BoundedReActLoop`, `RepeatGuard`, `ReportTool`) is merged and verified green.
- [x] **Tool Suites Operational**: P06 (`GoogleCommunicationTools`), P07 (`GoogleCalendarTools`), and P08 (`GoogleDriveTools`) registries are registered and tested with deterministic mock transports.
- [x] **Mutation Safety Gating (H3)**: Tool wrappers fail closed on mutations when invoked without `approval_token` or `approval_id`.
- [x] **State Persistence**: PostgreSQL run persistence and audit logging verified.

---

## 3. CommunicationAgent Specification (P12A)

### 3.1. Tool Visibility & Capabilities
`CommunicationAgent` is assigned category `"communication"` and exposed to:
- **Read / Search Tools**:
  - `gmail.search_messages`: Search messages matching Gmail query syntax.
  - `gmail.get_message`: Retrieve full message payload, headers, and clean body text.
  - `gmail.get_thread`: Retrieve full conversation thread with message chronology.
  - `gmail.list_threads`: List conversation threads matching query.
  - `contacts.search`: Search Google Contacts by name or keyword.
  - `contacts.get`: Fetch person details by resource name.
  - `contacts.resolve_person`: Fuzzy/deterministic resolution of contact name to email and metadata.
- **Drafting & Mutation Tools** (Requires approval token for real execution, otherwise emits `ProposedAction`):
  - `gmail.create_draft`: Draft a new email message.
  - `gmail.update_draft`: Modify an existing draft.
  - `gmail.send_draft`: Send an approved draft.
  - `gmail.reply`: Reply to an existing message thread.
  - `gmail.forward`: Forward a message thread to new recipients.
  - `gmail.archive`, `gmail.trash`: Mailbox cleanup.
  - `gmail.add_label`, `gmail.remove_label`: Label management.

### 3.2. Execution Modes & Flow

```text
User Request: "Email mới nhất của Nam?"
  │
  ├─► Deterministic Exact Check:
  │     1. contacts.resolve_person("Nam") -> single match: nam@example.com
  │     2. gmail.search_messages("from:nam@example.com", page_size=1)
  │     3. gmail.get_message(id)
  │     └── Return message summary (Direct Mode: 0 or 1 LLM call)
  │
User Request: "Tìm các email Nam gửi gần đây về RAG và tóm tắt quyết định cuối cùng."
  │
  ├─► Bounded ReAct Mode:
  │     Iteration 1: contacts.resolve_person("Nam")
  │     Iteration 2: gmail.search_messages("from:nam@example.com RAG")
  │     Iteration 3: gmail.get_thread(thread_id) -> fetch full decision context
  │     Iteration 4: Synthesize summary & call report tool
```

### 3.3. Ambiguity & Safety Invariants
- If contact search returns multiple candidates (e.g. "Nam Nguyen", "Nam Tran"), the agent MUST NOT guess. It must return a clarification prompt listing candidate names and emails.
- Drafting emails is considered non-destructive, but `send_draft`, `reply`, and `forward` MUST produce a `ProposedAction` containing recipient, subject, sanitized body, and risk classification (`EXTERNAL_COMMUNICATION`).

---

## 4. CalendarAgent Specification (P12B)

### 4.1. Tool Visibility & Capabilities
`CalendarAgent` is assigned category `"calendar"` and exposed to:
- **Read / Availability Tools**:
  - `calendar.list_events`: List events within a normalized UTC/Asia:Ho_Chi_Minh window.
  - `calendar.search_events`: Text search in summaries and descriptions.
  - `calendar.get_event`: Fetch full event details, recurrence rules, and attendee statuses.
  - `calendar.get_free_busy`: Query busy intervals for primary and attendee calendars.
  - `calendar.find_free_slots`: Deterministic algorithm calculating open slots given duration, window, and busy blocks.
- **Mutation Tools** (Gated by HITL approval):
  - `calendar.create_event`: Book a new event with summary, start, end, attendees.
  - `calendar.update_event`: Update time, location, or description.
  - `calendar.delete_event`: Cancel / remove calendar event.
  - `calendar.add_attendee`, `calendar.remove_attendee`: Manage guest lists.

### 4.2. Deterministic Slot Finder Integration
The agent MUST NOT attempt arithmetic in LLM prompt tokens to find free slots.
1. When user requests "Tìm 45 phút rảnh tuần sau với Nam, ưu tiên buổi sáng":
   - Fetch busy windows using `calendar.get_free_busy`.
   - Invoke `calendar.find_free_slots` with parameters:
     - `duration_minutes=45`
     - `time_min=start_of_next_week`, `time_max=end_of_next_week`
     - `working_hours_start="08:30"`, `working_hours_end="17:30"`
     - `preferred_time_of_day="morning"`
2. The deterministic code computes the exact available slots and returns them sorted by preference.
3. The agent formats the output for the user in clear, localized Vietnamese.

---

## 5. Failure Handling & Circuit Breakers

| Failure Mode | Agent Reaction | Protocol |
|---|---|---|
| Contact Not Found | Report unable to locate person, suggest manual email input | Return `SpecialistResult(status="NEEDS_INPUT")` |
| Ambiguous Contact | List all matched names and email domains | Request user selection |
| Google 401/403 Token Expired | Fail closed with clear re-auth prompt | Raise `ExternalServiceError("Google token expired")` |
| Rate Limit (429) | Backoff and retry via `RetryPolicy` (max 3 attempts) | Handled transparently by adapter |
| Mutation without Approval | Return structured `ProposedAction` with estimated impact | Fail closed, never call live API |
| Budget / Loop Limit | `RepeatGuard` halts execution after 5 repeated calls or token cap | Emit partial report with collected evidence |

---

## 6. Test Matrix & Validation Scenarios

### 6.1. Unit Tests (`tests/unit/agents/`)
- `test_communication_agent_direct_read`: Verify exact email lookup bypasses ReAct loop.
- `test_communication_agent_contact_disambiguation`: Verify prompt when multiple contacts match.
- `test_communication_agent_draft_generates_proposal`: Verify write intent produces `ProposedAction`.
- `test_communication_agent_mutation_without_approval_blocked`: Verify fail-closed behavior on `send_draft`.
- `test_calendar_agent_direct_schedule_query`: Verify "Lịch ngày mai" executes in 1 step without ReAct.
- `test_calendar_agent_slot_finding_deterministic`: Verify delegation to `find_free_slots` rather than LLM calculation.
- `test_calendar_agent_create_event_proposal`: Verify event booking generates `ProposedAction`.

### 6.2. Integration Tests (`tests/integration/`)
- End-to-end multi-turn simulated interaction:
  1. User asks for recent email -> agent retrieves message.
  2. User asks to schedule follow-up meeting based on email -> CalendarAgent finds open slot and prepares draft invitation.

---

## 7. Pass Criteria & Exit Gate

The phase is complete and ready for `APPROVED P12` review when:
1. **Direct Mode Verification**: At least 3 common query patterns ("Lịch hôm nay", "Email mới nhất", "Tìm người trong danh bạ") execute with 0 or 1 LLM call.
2. **ReAct Efficiency**: Complex queries finish within <= 4 iterations and <= 3,000 total prompt tokens.
3. **100% Mutation Fail-Closed**: No mutation tool executes without `approval_token`.
4. **Unit Test Coverage**: >= 85% line coverage on `app/agents/specialist/communication.py` and `calendar.py`.
5. **No Regressions**: Full test suite passes green (`pytest`, `ruff`, `pyright`).
