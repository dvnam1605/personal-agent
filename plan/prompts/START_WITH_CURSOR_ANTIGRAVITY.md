# Coding Agent Start Prompt

Read `MASTER_PLAN.md` completely.

Then read ONLY the currently authorized phase file under `phases/`.

Current authorized phase: P0.

Rules:
- Implement only the authorized phase.
- Do not read/implement later phase files unless the active phase explicitly references a contract from them.
- Follow all HARD/MUST/MUST NOT rules in MASTER_PLAN.md.
- Run focused tests while implementing.
- At phase completion run lint, typecheck and required tests.
- Produce the exact Phase Review Pack.
- End with `GATE STATUS: WAITING FOR USER REVIEW — P0`.
- STOP.

Do not start P1 until the user explicitly sends `APPROVED P0`.
