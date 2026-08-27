> Active phase file for P7. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P7`.

# P7 — CALENDAR TOOLS

## Objective

Create deterministic Calendar layer.

## Tools

```text
list_events
search_events
get_event
get_free_busy
find_free_slots
create_event
update_event
delete_event
add_attendee
remove_attendee
```

## Requirements

- timezone aware
- no LLM arithmetic
- deterministic slot finder
- conflict checks
- mutation classification

## Tests

- overlapping meetings
- all-day
- timezones
- 30/45/60 minute slots
- create/update/delete classification

## Gate

STOP.

---
