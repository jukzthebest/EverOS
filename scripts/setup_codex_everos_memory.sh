#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MEMORY_ROOT="${EVEROS_MEMORY_ROOT:-$HOME/Obsidian/EverOS-Memory/everos}"
CONFIG_FILE="${EVEROS_CONFIG_FILE:-$HOME/.everos/config.toml}"
BASE_URL="${EVEROS_MEMORY_BASE_URL:-http://127.0.0.1:8000}"
LAUNCH_AGENT_LABEL="com.lengxiaochu.everos-memory"
LAUNCH_AGENT_FILE="$HOME/Library/LaunchAgents/$LAUNCH_AGENT_LABEL.plist"
UV_BIN="$(command -v uv || true)"

if [[ -z "$UV_BIN" ]]; then
  echo "error: uv is required. Install it first, for example: brew install uv" >&2
  exit 1
fi

mkdir -p "$HOME/.everos" "$HOME/.local/bin" "$HOME/.codex/hooks" \
  "$HOME/.codex/rules" "$HOME/.codex/log" "$HOME/Library/LaunchAgents" "$MEMORY_ROOT"

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
cp "$REPO_DIR/templates/codex/hooks/everos-memory-user-prompt.js" \
  "$HOME/.codex/hooks/everos-memory-user-prompt.js"
cp "$REPO_DIR/templates/codex/hooks/everos-memory-stop.js" \
  "$HOME/.codex/hooks/everos-memory-stop.js"
chmod +x "$HOME/.codex/hooks/everos-memory-user-prompt.js" \
  "$HOME/.codex/hooks/everos-memory-stop.js"

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
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "node \"$HOME/.codex/hooks/everos-memory-user-prompt.js\""
          }
        ]
      }
    ],
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
changed = False

def ensure_command(event: str, suffix: str, command: str) -> None:
    global changed
    groups = hooks.setdefault(event, [])
    for group in groups:
        for hook in group.get("hooks", []):
            if suffix in str(hook.get("command", "")):
                return
    groups.append({"hooks": [{"type": "command", "command": command}]})
    changed = True

ensure_command(
    "UserPromptSubmit",
    "everos-memory-user-prompt.js",
    'node "$HOME/.codex/hooks/everos-memory-user-prompt.js"',
)
ensure_command(
    "Stop",
    "everos-memory-stop.js",
    'node "$HOME/.codex/hooks/everos-memory-stop.js"',
)

if changed:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
PY
fi

cat > "$LAUNCH_AGENT_FILE" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LAUNCH_AGENT_LABEL</string>

  <key>WorkingDirectory</key>
  <string>$REPO_DIR</string>

  <key>ProgramArguments</key>
  <array>
    <string>$UV_BIN</string>
    <string>run</string>
    <string>everos</string>
    <string>server</string>
    <string>start</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>8000</string>
    <string>--log-level</string>
    <string>info</string>
  </array>

  <key>EnvironmentVariables</key>
  <dict>
    <key>EVEROS_CONFIG_FILE</key>
    <string>$CONFIG_FILE</string>
    <key>NO_PROXY</key>
    <string>127.0.0.1,localhost</string>
    <key>no_proxy</key>
    <string>127.0.0.1,localhost</string>
  </dict>

  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>$HOME/.everos/server.out.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/.everos/server.err.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)" "$LAUNCH_AGENT_FILE" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$LAUNCH_AGENT_FILE"
launchctl enable "gui/$(id -u)/$LAUNCH_AGENT_LABEL"
launchctl kickstart -k "gui/$(id -u)/$LAUNCH_AGENT_LABEL"

echo "EverOS Codex memory setup complete."
echo "Repo: $REPO_DIR"
echo "Memory root: $MEMORY_ROOT"
echo "Config: $CONFIG_FILE"
echo "API: $BASE_URL"
echo "LaunchAgent: $LAUNCH_AGENT_FILE"
echo
echo "Next:"
echo "  cd '$REPO_DIR'"
echo "  everos-memory doctor"
