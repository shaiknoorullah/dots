---
name: task-discipline
description: Declare the task before editing files or running commands. Use at the start of every session, and whenever the user's request changes to different work. Covers dots-task list, declare, new, switch and none.
---

# Task discipline

Every session works on a declared task. A hook refuses edits and shell commands
(Edit, Write, MultiEdit, NotebookEdit, Bash) until one is declared.
Reading, searching and `dots-task` itself are always allowed.

1. **Pick** an existing task: `dots-task list` (add `--grep word` or `--project p`).
2. **Declare** it: `dots-task declare <ref>`.
3. **No match?** Create a local task: `dots-task new "<short title>" --project <p>`.
   Add `--parent <ref>` when it is a step of a bigger ticket.
4. **Different work later?** `dots-task switch <ref>`, or `dots-task new ...`.
   If unsure whether the user wants it logged, ask: "log this as a ticket?"
5. **Trivial and not work** (a quick question, no files touched):
   `dots-task none "<reason>"`. It is logged, so use it honestly.

Rules:

- Titles say what, in a few words. No secrets, no customer names.
- New tasks are LOCAL only. Never file a GitHub issue or ERP ticket for it.
  The user promotes local tasks later (`dots-task promote <local-ref> <ref>`).
- `dots-task current` shows what this session declared.
- If a Stop hook says the turn looks like different work, do what it says:
  switch, create, or ask the user. Do not argue with it twice.
