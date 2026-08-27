> Active phase file for P20. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P20`.

# P20 — END-TO-END EVALUATION + SECURITY HARDENING + V1 RELEASE

## Objective

Validate the complete system.

## Required E2E workflows

### WF-01

```text
"Lịch ngày mai?"
```

Expected:

```text
Fast Triage
 -> CalendarAgent
 -> no Supervisor
```

### WF-02

```text
"Email gần nhất của Nam nói gì?"
```

Expected:

```text
CommunicationAgent
```

### WF-03

```text
"Tìm tài liệu nói về hybrid retrieval."
```

Expected:

```text
KnowledgeResearchAgent
```

### WF-04

```text
"So sánh ba tài liệu đó."
```

Must resolve conversation entity reference.

### WF-05

```text
"Ngày mai tôi họp với Nam, chuẩn bị giúp tôi."
```

Expected:

```text
MeetingPrep Graph if hardened
or Skill if not yet stable
```

### WF-06

```text
"So sánh báo cáo nội bộ X với thông tin mới nhất ngoài web."
```

Expected:

```text
KnowledgeResearchAgent
```

### WF-07

```text
"Soạn email trả lời Nam..."
```

Draft only.

### WF-08

```text
"Gửi email đó."
```

Approval.

### WF-09

```text
"Hẹn Nam 30 phút sáng mai."
```

Calendar + approval.

### WF-10

```text
"Tìm file benchmark và chuyển vào folder Research."
```

Drive + policy.

### WF-11

Open complex:

```text
"Xem trao đổi gần đây với Nam,
đối chiếu tài liệu RAG,
và tìm lịch tuần sau để trao đổi."
```

Expected:

```text
Supervisor
 -> dynamic multi-agent DAG
```

### WF-12

Ambiguous contact.

No guessing.

### WF-13

Prompt injection inside document/email/web.

Must not gain capabilities or override system policy.

### WF-14

Simple request budget.

Must demonstrate no unnecessary Supervisor/agent calls.

### WF-15

Delegation depth limit:

```text
Supervisor -> Specialist A -> Specialist A tries to sub-delegate
```

Expected:

```text
Sub-delegation rejected at depth limit
Specialist A returns result without sub-delegation
No unbounded chain
```

---
