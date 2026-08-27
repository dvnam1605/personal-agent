> Active phase file for P18. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P18`.

# P18 — POLICY + APPROVAL + FULL WRITE ACCESS

## Objective

Enable actual write actions safely.

## P18-01 PolicyEngine & Approval Plane

The user approval seam gates write / high-risk actions with a strict fail-closed contract.

### Outcomes (Closed & Fail-Closed)
```text
ApprovalOutcome:
  - allowed-once   (only the exact asked-about action is authorized once)
  - rejected       (explicit user denial)
  - cancelled      (request aborted/timed out)
  - unavailable    (no active UI/human answerer connected -> fails closed)
```

### Per-Session Approval Policy
```text
ApprovalPolicy:
  - 'ask'   (default for interactive UI: dispatches modal/prompt to user)
  - 'never' (headless/unattended runs: auto-rejects all approval asks deterministically)
```

## P18-02 ApprovalRequest

## P18-01A Delegation policy pinning

When PolicyEngine evaluates an action from a delegated specialist:

```text
1. Check DelegationContext.approval_policy
2. If approval_policy == NEVER:
   -> action is auto-denied
   -> specialist receives structured rejection
   -> DelegationResult.needs_approval = True
   -> orchestration layer surfaces to user/approval flow

3. Approval decisions are ONLY made at the orchestration layer:
   -> Supervisor / Workflow collects needs_approval flags
   -> presents consolidated approval request to user
   -> resumes execution after approval
```

The delegation policy is captured at delegation time (before first await)
and frozen for the child's entire execution. A parent policy change
during child execution does NOT affect the child.

Include in ApprovalRequest:

```text
target
action
important arguments
risk
expiry
status
```

*DeepSeek Harness Reference (Approval):*
- Package: `deepseek-harness/packages/interaction/user-approval/`, `deepseek-harness/packages/interaction/permission-presets/`
- Docs: `deepseek-harness/docs/subsystems/approval.md`, `deepseek-harness/docs/subsystems/permission-presets.md`

## P18-02A Question Plane (`tool_ask_user` / `UserQuestions`)

Separate from binary permission gating, provide a structured question tool for semantic clarification / options:

```text
AskUserQuestionRequest:
  questions:
    - id: str
      question: str
      detail: str | None
      options: list[{label: str, description: str | None}] | None
      multi_select: bool (default False)
      intent: 'plan-review' | None (special UI presentation)
```

Enables the model to ask structured multiple-choice questions or request user feedback on draft plans instead of generating unstructured conversational prompts.

*DeepSeek Harness Reference (User Questions):*
- Package: `deepseek-harness/packages/interaction/user-questions/`, `deepseek-harness/packages/interaction/tool-ask-user/`
- Docs: `deepseek-harness/docs/subsystems/user-questions.md`

## P18-03 LangGraph interrupt/resume

Pause before approval-required execution using `interrupt()`.

Durability is provided by `AsyncPostgresSaver` (psycopg3 DSN derived in P11-00), not
by `assistant_runs.state_snapshot`, which stays a sanitized read-only projection, and
not by Redis, which is cache/rate-limit only. See `MASTER_PLAN.md` §18A.4.

A pending approval MUST survive process restart and MUST resume exactly once.

## P18-04 APIs

```text
GET /approvals/pending
POST /approvals/{id}/approve
POST /approvals/{id}/deny
POST /questions/{id}/answer
```

## P18-05 Stale action check

Revalidate before executing approved mutations.

## P18-06 Idempotency

Prevent duplicate sends/events/uploads after retries.

## P18-07 Audit

Record:

```text
who
what
when
target
policy decision
approval
result
```

## Security tests

- send cannot bypass approval
- delete cannot bypass approval
- replay safe
- stale target
- expired approval
- denied action
- capability exposure still enforced
- interrupted run resumes after restart and executes the mutation exactly once
- checkpointed state contains no unredacted secrets or external raw content
- delegated specialist cannot self-approve (approval_policy=NEVER)
- delegation policy frozen at delegation time
- consolidated approval from Supervisor path works
- unavailable answerer defaults to fail-closed (`unavailable` outcome)
- headless run with `approval_policy=never` rejects mutation deterministically without hanging
- structured user question flow delivers multi-choice and free-text answers correctly

## Gate

STOP.

---
