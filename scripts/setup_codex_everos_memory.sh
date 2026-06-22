#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MEMORY_ROOT="${EVEROS_MEMORY_ROOT:-$HOME/Obsidian/EverOS-Memory/everos}"
CONFIG_FILE="${EVEROS_CONFIG_FILE:-$HOME/.everos/config.toml}"
BASE_URL="${EVEROS_MEMORY_BASE_URL:-http://127.0.0.1:8000}"

mkdir -p "$HOME/.everos" "$HOME/.local/bin" "$HOME/.codex/hooks" \
  "$HOME/.codex/rules" "$HOME/.codex/log" "$MEMORY_ROOT"

ln -sf "$REPO_DIR/scripts/everos-memory" "$HOME/.local/bin/everos-memory"
chmod +x "$REPO_DIR/scripts/everos-memory"

if ! grep -q 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.zshrc" 2>/dev/null; then
  printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.zshrc"
fi

if ! grep -q 'NO_PROXY=.*127.0.0.1' "$HOME/.zshrc" 2>/dev/null; then
  cat >> "$HOME/.zshrc" <<'EOF'
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}127.0.0.1,localhost"
export no_proxy="$NO_PROXY"
EOF
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
  cat > "$CONFIG_FILE" <<EOF
[memory]
root = "$MEMORY_ROOT"
timezone = "Asia/Shanghai"

[llm]
provider_chain = ["grok_oauth", "codex_oauth", "openai"]
grok_model = "grok-build"
codex_model = "gpt-5.5"
openai_model = "gpt-4o-mini"
openai_base_url = "https://api.openai.com/v1"
grok_base_url = "https://cli-chat-proxy.grok.com/v1"
codex_auth_file = "~/.codex/auth.json"
grok_auth_file = "~/.grok/auth.json"
extraction_language = "zh"

[embedding]
provider = "local_hash"
model = ""
api_key = ""
base_url = ""
EOF
fi

cp "$REPO_DIR/templates/codex/rules/everos-memory.md" \
  "$HOME/.codex/rules/everos-memory.md"
cp "$REPO_DIR/templates/codex/hooks/everos-memory-stop.js" \
  "$HOME/.codex/hooks/everos-memory-stop.js"
chmod +x "$HOME/.codex/hooks/everos-memory-stop.js"

if [[ ! -f "$HOME/.codex/AGENTS.md" ]]; then
  cp "$REPO_DIR/templates/codex/AGENTS.md" "$HOME/.codex/AGENTS.md"
elif ! grep -q 'EverOS 记忆' "$HOME/.codex/AGENTS.md"; then
  {
    printf '\n'
    cat "$REPO_DIR/templates/codex/AGENTS.md"
  } >> "$HOME/.codex/AGENTS.md"
fi

if [[ ! -f "$HOME/.codex/hooks.json" ]]; then
  cat > "$HOME/.codex/hooks.json" <<'EOF'
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "node \"$HOME/.codex/hooks/everos-memory-stop.js\""
          }
        ]
      }
    ]
  }
}
EOF
else
  HOOKS_JSON="$HOME/.codex/hooks.json" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["HOOKS_JSON"])
data = json.loads(path.read_text())
hooks = data.setdefault("hooks", {})
stop = hooks.setdefault("Stop", [])
command = 'node "$HOME/.codex/hooks/everos-memory-stop.js"'

for group in stop:
    for hook in group.get("hooks", []):
        if hook.get("command") == command:
            break
    else:
        continue
    break
else:
    stop.append({"hooks": [{"type": "command", "command": command}]})
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
PY
fi

echo "EverOS Codex memory setup complete."
echo "Repo: $REPO_DIR"
echo "Memory root: $MEMORY_ROOT"
echo "Config: $CONFIG_FILE"
echo "API: $BASE_URL"
echo
echo "Next:"
echo "  cd '$REPO_DIR'"
echo "  EVEROS_CONFIG_FILE='$CONFIG_FILE' uv run everos server start --host 127.0.0.1 --port 8000"
echo "  everos-memory doctor"
