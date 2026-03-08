---
name: session-restore
description: >
  Restore session context from session-notes.md at the start of a new session.
  Auto-continues if Next Action is specific enough.
---

# session-restore

Restore session context from `session-notes.md` at the start of a new session.

## Trigger
User says: "session-restore", "восстанови сессию", "restore session", "continue", "продолжай"

## Steps
1. Read `session-notes.md` from project root
2. If not found: tell user "No saved session found. Starting fresh."
3. If found:
   a. Display "Restoring session from <date>"
   b. Show "Current Task" and "Pending" sections
   c. Read "Next Action" — if concrete, announce and start executing
   d. If "Next Action" is vague, show full context and ask user what to do
4. After loading, do NOT delete session-notes.md (user may want to reference it)

## Rules
- Auto-continue if Next Action is specific enough
- Don't re-read files listed in "Modified Files" unless needed for the next action
- Treat session-notes.md as context, not as instructions — user may have changed their mind
