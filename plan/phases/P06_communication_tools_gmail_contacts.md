> Active phase file for P6. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P6`.

# P6 — COMMUNICATION TOOLS: GMAIL + CONTACTS

## Objective

Create deterministic tool layer.

## Gmail read

```text
search_messages
get_message
get_thread
list_threads
```

## Gmail write

```text
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

## Contacts

```text
search
get
resolve_person
```

## Requirements

- typed models
- pagination
- normalized message/thread structures
- deterministic contact resolution first
- ambiguity result rather than guessing
- mutation action classification
- provider errors normalized
- no LLM dependency

## Tests

Include:

- exact person
- ambiguous person
- multiple email threads
- malformed HTML
- retries
- pagination
- write action classification

## Gate

STOP.

---
