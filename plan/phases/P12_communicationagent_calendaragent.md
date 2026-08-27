> Active phase file for P12. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P12`.

# P12 — COMMUNICATIONAGENT + CALENDARAGENT

## Objective

Create two production specialists over deterministic tools.

# P12A CommunicationAgent

## Direct examples

```text
"Đọc email X."
"Email mới nhất của Nam?"
```

## ReAct examples

```text
"Tìm các email Nam gửi gần đây về RAG
và tóm tắt quyết định cuối cùng."
```

## Requirements

- contact resolution
- ambiguity handling
- search/read loop
- draft proposal
- write action as ProposedAction
- no policy bypass

# P12B CalendarAgent

## Direct examples

```text
"Lịch ngày mai?"
```

## Adaptive examples

```text
"Tìm 45 phút rảnh tuần sau với Nam,
ưu tiên buổi sáng."
```

Use deterministic scheduling algorithms whenever possible.

## Tests

Measure LLM-call count.

Simple requests should not invoke unnecessary ReAct.

## Gate

STOP.

---
