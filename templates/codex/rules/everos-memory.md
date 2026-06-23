## EverOS Memory Rule

EverOS is the primary local long-term memory. Use it when a task may depend on
prior work, project history, local conventions, user preferences, repeated
mistakes, or reusable procedures.

Default command:

```bash
everos-memory search "<task keywords>" --project auto --limit 3
```

Project routing:

- Use `--project auto` unless the current workspace has a known project id.
- Set `EVEROS_MEMORY_PROJECTS` or `EVEROS_CODEX_PROJECT_RULES` if you want
  custom project routing.

Priority:

1. Prefer high-confidence EverOS hits.
2. Treat memory as context, not as proof. Current files, current prompt, and
   live evidence win over stale memory.
3. Fall back to other memory sources only when EverOS is unavailable,
   low-confidence, empty, or conflicting.

Fast path:

- For direct command/template lookups, run one scoped query with `--limit 1`.
- If a hit directly answers the request and is high confidence, answer directly.
- Do not explain the retrieval process unless it affects trust or correctness.

Output contract:

- Use EverOS silently. Do not narrate steps such as "checking memory",
  "loading a skill", "reading MEMORY.md", or "searched for ...".
- For direct command/template lookups, lead with the command or concrete
  answer. Add at most one short source/confidence line after the answer.
- Do not run ad-hoc `rg` over `MEMORY.md` before or after `everos-memory`
  unless EverOS is unavailable, empty, low-confidence, or conflicts with live
  evidence.
- If the memory hit is not enough, say the uncertainty briefly and continue
  with current evidence. Do not expose query mechanics.
