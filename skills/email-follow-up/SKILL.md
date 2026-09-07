# Email Follow-Up

Use this skill as procedural guidance only. Draft creation is a mutation:
the runtime must hold an approval token and must not be in a read-only view.

## Step 1: Retrieve email thread history

Load the full thread with `gmail.get_thread`. If only a message id is known,
fetch that message first, then the parent thread. Do not guess missing ids.

- tool: gmail.get_thread
- capability: gmail.read
- outputs: thread, messages, participants

## Step 2: Extract sender questions and explicit commitments

List questions the counterpart asked and any commitments the user already
made. Quote enough context to keep later draft sentences grounded.

- capability: gmail.read
- outputs: questions, commitments

## Step 3: Query relevant internal knowledge for answers

Use `retrieval.retrieve` / `retrieval.synthesize` for internal answers with
citations. If evidence is insufficient, leave an explicit gap instead of
filling from memory.

- tool: retrieval.retrieve
- capability: retrieval.read
- outputs: evidence_ids, cited_answers

## Step 4: Formulate a draft reply with citations

Compose a draft via `gmail.create_draft` only after approval is present.
Never call `gmail.send_draft`, `gmail.reply`, or `gmail.forward` from this
skill. Outstanding questions stay listed in the draft.

- tool: gmail.create_draft
- capability: gmail.drafts
- outputs: draft_id, outstanding_questions, citations
