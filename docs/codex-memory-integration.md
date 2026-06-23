# Codex Memory Integration

This machine integrates Codex with the local EverOS memory root without MCP.

## Read Path

Codex rules call the lightweight helper:

```bash
everos-memory search "<query>" --project auto --limit 3
```

The helper is installed at:

```text
/Users/lengxiaochu/.local/bin/everos-memory
```

It is a symlink to:

```text
/Users/lengxiaochu/Documents/daily/everos-codex-oauth-poc/scripts/everos-memory
```

For search and health checks it does not import the EverOS Python package. It
uses the running local API at `http://127.0.0.1:8000`, so the hot path is one
local HTTP request plus LanceDB/keyword search on the server side.

Useful commands:

```bash
everos-memory doctor
everos-memory search "CSPM upgrade flow" --project CSPM --limit 3
everos-memory search "Yundun scanner_main schema_version" --project Yundun --limit 3
everos-memory search "AIDP same QC" --project AIDP --limit 3
```

## Rule Path

Global Codex rules were updated in:

```text
/Users/lengxiaochu/.codex/AGENTS.md
```

The dedicated skill is installed at:

```text
/Users/lengxiaochu/.codex/skills/everos-memory/SKILL.md
```

The rule is intentionally conservative: recall is required for non-trivial
history-dependent tasks, but memory hits are treated as stale context until
cheaply verified from the current repo or live system.

Recall priority:

1. EverOS is the primary project-history memory.
2. Codex built-in memory under `~/.codex/memories` is fallback only.
3. If EverOS returns relevant high-confidence hits, do not query built-in memory
   for the same fact.
4. Query built-in memory only when EverOS is unavailable, returns no useful
   hits, returns low-confidence hits, or a conflict needs arbitration.

Fast path:

- For direct command/template lookups, use one scoped EverOS query with
  `--limit 1`.
- If the result is relevant and `confidence=high`, answer directly.
- Avoid checking unrelated projectless directories just to verify a matched
  memory.
- Live-verify only in the relevant repo, on user request, or for
  destructive/high-risk commands.
- Do not send an intermediate commentary message before the recall. Run the
  single recall silently, then answer in the final response.

## Hook Path

The Codex hooks are registered in:

```text
/Users/lengxiaochu/.codex/hooks.json
```

They run:

```text
/Users/lengxiaochu/.codex/hooks/everos-memory-user-prompt.js
/Users/lengxiaochu/.codex/hooks/everos-memory-stop.js
```

The UserPromptSubmit hook injects one high-confidence hit before the model
starts. The Stop hook:

- checks whether the local EverOS API is reachable;
- excludes the current session id to avoid partial imports;
- imports only session files whose mtime is at least 180 seconds old;
- imports at most one stable session per Stop event;
- uses a lock file under `/tmp` to prevent concurrent import jobs;
- logs to `/Users/lengxiaochu/.codex/log/everos-memory-hook.log`.

## Known Limits

- The read path currently defaults to `keyword` search. `hybrid` and `vector`
  need a separate fix before they are safe for automatic Codex rules.
- The write path is eventually consistent. The current session is usually
  imported by a later Stop event, not during the same turn.
- If the EverOS API is not running, recall is skipped and the hook does not
  import sessions.
