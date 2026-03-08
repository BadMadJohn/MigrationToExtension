---
name: session-retro
description: >
  Run a quick session retrospective: what worked, what didn't, lessons learned.
---

# session-retro

Run a quick session retrospective: what worked, what didn't, lessons learned.

## Trigger
User says: "session-retro", "ретроспектива", "retro", "итоги"

## Steps
1. Review the current session:
   - What tasks were completed?
   - What approaches worked well?
   - What failed or took too long?
   - Any patterns discovered?
2. Write retro to `session-notes.md` (append "## Retrospective" section if file exists, or create new)
3. If any lessons are universal (not project-specific), suggest adding to project CLAUDE.md
4. Report summary to user

## Rules
- Keep it brief — 5-10 bullet points max
- Focus on actionable insights, not descriptions of what happened
- Don't over-analyze one-off issues
