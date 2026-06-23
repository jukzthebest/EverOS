# Codex Memory Integration

EverOS can be used as a local long-term memory layer for Codex without MCP.
The integration uses a small CLI helper plus optional Codex hooks.

## Install

```bash
bash scripts/setup_codex_everos_memory.sh
```

The setup script installs:

- `everos-memory` in `~/.local/bin`
- `everos-dashboard` in `~/.local/bin`
- Codex hook templates under `~/.codex/hooks`
- Codex memory rules under `~/.codex/rules`
- a macOS LaunchAgent for the local EverOS API

Defaults are intentionally generic:

- memory root: `~/.everos/memory`
- API: `http://127.0.0.1:8000`
- LaunchAgent label: `com.everos.memory`
- project id: `default`

Override these with environment variables before running setup:

```bash
export EVEROS_MEMORY_ROOT="$HOME/.everos/my-memory"
export EVEROS_LAUNCH_AGENT_LABEL="com.example.everos-memory"
export EVEROS_MEMORY_PROJECTS="default"
export EVEROS_CODEX_PROJECT_RULES="<repo-regex>=<project-id>"
bash scripts/setup_codex_everos_memory.sh
```

## Read Path

Codex rules call:

```bash
everos-memory search "<query>" --project auto --limit 3
```

The helper talks to the running local API. It avoids importing the full Python
package on the hot path.

Useful commands:

```bash
everos-memory doctor
everos-memory search "database migration checklist" --project auto --limit 3
everos-dashboard
```

## Hook Path

The `UserPromptSubmit` hook injects at most one high-confidence EverOS hit
before the model runs.

The hook is conservative:

- it skips long prompts;
- it searches only when the prompt looks history-dependent or procedural;
- it uses `--project auto` by default;
- it can be routed with `EVEROS_CODEX_PROJECT_RULES`;
- it suppresses output so the user does not see retrieval chatter.

The `Stop` hook has two write paths:

- current session: import complete message chunks from the active session file;
  the growing tail is left for a later Stop event so partial chunks are not
  frozen too early.
- stable sessions: import one older session after the age threshold as a
  backfill path.

This makes long-running sessions usable: a large session is split into part
records instead of being discarded as oversized.

## Configuration

Common environment variables:

```bash
export EVEROS_MEMORY_BASE_URL="http://127.0.0.1:8000"
export EVEROS_MEMORY_USER_ID="$(whoami)"
export EVEROS_MEMORY_AGENT_ID="codex"
export EVEROS_MEMORY_APP_ID="codex"
export EVEROS_MEMORY_PROJECTS="default"
export EVEROS_MEMORY_PROJECT="auto"
export EVEROS_MEMORY_RECALL_TERMS="previous,last,before,memory,remember,command,config,path,file,template,how,why"
export EVEROS_MEMORY_DIRECT_LOOKUP_TERMS="command,cmd,connect,login,shell,cli"
export EVEROS_MEMORY_CHUNK_MESSAGES="40"
export EVEROS_MEMORY_STABLE_MIN_AGE_SECONDS="180"
```

Project routing syntax:

```bash
export EVEROS_CODEX_PROJECT_RULES="<repo-regex>=<project-id>,<repo-regex>=<project-id>"
```

Each rule is `regex=project_id`. The first regex matching the prompt or current
working directory wins.

## Limits

- Memory is context, not proof. Current files and live evidence win over stale
  memory.
- `keyword` search is the safest automatic path. Use `hybrid` or `vector` only
  after your embedding index is built and verified.
- Hooks are optional; the CLI can be used manually without installing Codex
  hooks.
