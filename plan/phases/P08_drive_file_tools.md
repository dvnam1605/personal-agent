> Active phase file for P8. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P8`.

# P8 — DRIVE FILE TOOLS

## Objective

Treat Drive as a file system, separate from RAG.

## Tools

```text
search_files
list_folder
get_metadata
download_file
upload_file
create_folder
move_file
rename_file
delete_file
update_permissions
```

## Requirements

- Google-native formats
- export handling
- checksum/version metadata
- idempotency where possible
- mutation action classification

## Gate

STOP.

---
