# Action Policy & Safety Architecture Contract

## 1. Overview
The assistant must never execute dangerous, sensitive, or irreversible actions without explicit policy validation and Human-in-the-Loop (HITL) approval when required.

Technical capability (having an API token with write scopes) does **NOT** grant the agent autonomous write authority.

---

## 2. Canonical Action Classification

```text
enum ActionClass:
    READ                    # Safe, non-mutating queries
    SAFE_WRITE              # Reversible, low-risk local mutations
    SENSITIVE_WRITE         # Modifies user calendar, dispatches communication
    DESTRUCTIVE             # Deletes data permanently, trashes messages/events
    EXTERNAL_COMMUNICATION  # Sends emails to external third-party recipients
    PERMISSION_CHANGE       # Modifies sharing settings, OAuth grants, ACLs
```

---

## 3. Action Execution & Approval Matrix

| Operation | Action Class | Default Policy | Justification |
|---|---|:---:|---|
| Read/Search Gmail | `READ` | **AUTO** | Pure read operation |
| Read Calendar / FreeBusy | `READ` | **AUTO** | Pure read operation |
| Search Drive / Ingest RAG | `READ` | **AUTO** | Pure read operation |
| Web Search | `READ` | **AUTO** | Pure read operation |
| Create Email Draft | `SAFE_WRITE` | **AUTO** | Non-destructive; requires manual send to deliver |
| Add / Remove Labels | `SAFE_WRITE` | **AUTO** / Configurable | Low impact organizational change |
| Create Calendar Event | `SENSITIVE_WRITE` | **APPROVAL** | Directly alters user schedule and sends invites |
| Update Calendar Event | `SENSITIVE_WRITE` | **APPROVAL** | Modifies existing commitments |
| Delete Calendar Event | `DESTRUCTIVE` | **APPROVAL** | Deletes events; alerts attendees |
| Send Email | `EXTERNAL_COMMUNICATION`| **APPROVAL** | Irreversible external message delivery |
| Trash / Delete Email | `DESTRUCTIVE` | **APPROVAL** | Potential permanent data loss |
| Drive Move / Rename | `SAFE_WRITE` | **AUTO** / Configurable | Non-destructive file organization |
| Drive Upload | `SAFE_WRITE` | **AUTO** / Configurable | Creates new file version |
| Drive Delete | `DESTRUCTIVE` | **APPROVAL** | Deletes documents |
| Permission / Sharing Changes | `PERMISSION_CHANGE` | **APPROVAL** | Security & privacy boundary change |

---

## 4. Human-In-The-Loop (HITL) Durable Approval Flow

When an operation requires `APPROVAL`:
1. `PolicyEngine` detects `APPROVAL` status and constructs an `ApprovalRequest` record with exact payload, diff preview, and expiration timestamp.
2. The runtime checkpoints workflow state to the PostgreSQL graph checkpointer and pauses execution (see `orchestration-substrate.md` §5).
3. The UI presents the approval prompt with a clear preview:
   - Recipient, Subject, Body (for emails).
   - Date, Time, Title, Attendees (for calendar events).
4. **User Actions**:
   - `APPROVE`: Runtime resumes execution and triggers tool invocation.
   - `REJECT`: Runtime cancels tool execution and returns user feedback to agent.
   - `TIMEOUT`: State transitions to `EXPIRED`; tool execution aborted.
