# Mac mini EverOS 做题记忆落地方案

目标：在另一台 Mac mini 上部署同一套 EverOS 记忆系统，用于做题、错题沉淀、解题套路召回，并接入 Codex。

## 0. 结论

推荐方案：

- EverOS repo：使用当前 fork。
- 记忆根目录：`~/Obsidian/EverOS-Memory/everos-v2`。
- 项目域：复用 `AIDP`，用于做题、标注、错题和解题策略。
- LLM：优先 `grok_oauth -> codex_oauth -> openai` fallback。
- Embedding：有便宜 API 就用 DeepInfra/Qwen；没有就先用 `local_hash`。
- 记忆 Markdown：统一中文输出，配置 `extraction_language = "zh"`。
- 数据同步：同步 Markdown 本体，不同步 `.index/`；每台机器本地重建 SQLite + LanceDB。
- Codex 接入：不走 MCP，走 `everos-memory` CLI + hooks。

## 1. 安装

```bash
mkdir -p ~/Documents/daily
cd ~/Documents/daily
git clone -b codex/everos-codex-memory-setup \
  git@github.com:jukzthebest/EverOS.git \
  everos-codex-oauth-poc
cd everos-codex-oauth-poc

brew install uv jq
uv sync
bash scripts/setup_codex_everos_memory.sh
```

验证：

```bash
uv run everos --help
everos-memory doctor
```

如果本机开了代理，确保 localhost 不走代理：

```bash
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}127.0.0.1,localhost"
export no_proxy="$NO_PROXY"
```

setup 会自动写入 `~/.zshrc`，`everos-memory` 自身也会绕过代理访问本机 API。

## 2. setup 脚本会完成什么

`scripts/setup_codex_everos_memory.sh` 会自动完成：

- 创建 `~/Obsidian/EverOS-Memory/everos-v2`。
- 生成 `~/.everos/config.toml`，默认 `extraction_language = "zh"`。
- 安装 `~/.local/bin/everos-memory`。
- 写入 localhost 绕代理配置：`NO_PROXY=127.0.0.1,localhost`。
- 写入 `~/.codex/rules/everos-memory.md`。
- 在 `~/.codex/AGENTS.md` 中追加 EverOS 短规则。
- 安装 `~/.codex/hooks/everos-memory-user-prompt.js`。
- 安装 `~/.codex/hooks/everos-memory-stop.js`。
- 新建或合并 `~/.codex/hooks.json` 的 UserPromptSubmit / Stop hooks。

## 3. 配置记忆根目录

setup 后检查 `~/.everos/config.toml`。

推荐配置：

```toml
[memory]
root = "~/Obsidian/EverOS-Memory/everos-v2"
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
```

如果愿意为检索质量花一点钱，把 `[embedding]` 改成：

```toml
[embedding]
provider = "openai"
model = "Qwen/Qwen3-Embedding-4B"
api_key = "<DEEPINFRA_API_KEY>"
base_url = "https://api.deepinfra.com/v1/openai"
timeout_seconds = 30.0
max_retries = 3
batch_size = 10
max_concurrent = 5
```

说明：

- `local_hash` 不需要 API key，适合先跑通。
- 做题场景更建议用真 embedding，错题和相似题召回会更准。
- LLM 负责把 session 压缩成 Markdown 记忆；Embedding 负责 LanceDB 检索。

## 4. 启动 EverOS

setup 脚本会自动安装并启动：

```text
~/Library/LaunchAgents/com.lengxiaochu.everos-memory.plist
```

健康检查：

```bash
curl -s http://127.0.0.1:8000/api/v1/dashboard/health | jq
```

本地看板：

```text
http://127.0.0.1:8000/dashboard/
```

## 5. 检查快速检索 CLI

setup 会把 `scripts/everos-memory` 链接到 `~/.local/bin/everos-memory`。
如果当前 shell 找不到命令，确认 `~/.zshrc` 里有：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

验证：

```bash
everos-memory doctor
everos-memory search "二分 边界 条件" --project AIDP --limit 3
```

## 6. Codex 规则

setup 会写入 `~/.codex/rules/everos-memory.md`，内容应类似：

````md
## EverOS 记忆规则

EverOS 是本机主长期记忆。任务可能依赖历史做题记录、错题、解题套路、边界条件、个人偏好或项目上下文时，先查 EverOS。

默认命令：

```bash
everos-memory search "<task keywords>" --project auto --limit 3
```

项目路由：

- `--project AIDP`：题目、练习、错题、解法套路、代码模板。
- `--project Daily`：本地 Codex 工具和工作流。

优先级：

1. 优先使用 EverOS 高置信命中。
2. 只有 EverOS 不可用、低置信、无结果或冲突时，才查 Codex 内置记忆。
3. 当前题面、当前文件和现场证据优先于历史记忆。

做题快路径：

- 相似题先查 `--project AIDP --limit 3`。
- 复用历史错因、边界条件、代码模板和证明套路。
- 不要机械复述旧答案；必须结合当前题面重新推导。
- 生成或沉淀到 EverOS 的 Markdown 必须使用中文。
```
````

setup 会在 `~/.codex/AGENTS.md` 中加入短路由：

````md
## 交互风格

- 简洁、老练，先给答案或已执行动作。
- 过程更新只说关键变化、阻塞点或需要用户决策的事项。
- 最终回答默认简短，但保留结果、关键证据、验证结果和必要限制。

## EverOS 记忆

EverOS 是本机主长期记忆。做题、相似题、错题、重复错误、个人解题策略相关任务，先查 EverOS。

默认命令：

```bash
everos-memory search "<task keywords>" --project AIDP --limit 3
```

完整规则：`~/.codex/rules/everos-memory.md`。
```
````

## 7. Codex Hooks

setup 会从仓库模板安装 hooks。手工修复时使用：

```bash
mkdir -p ~/.codex/hooks ~/.codex/log
cp templates/codex/hooks/everos-memory-user-prompt.js ~/.codex/hooks/everos-memory-user-prompt.js
cp templates/codex/hooks/everos-memory-stop.js ~/.codex/hooks/everos-memory-stop.js
chmod +x ~/.codex/hooks/everos-memory-user-prompt.js ~/.codex/hooks/everos-memory-stop.js
node --check ~/.codex/hooks/everos-memory-user-prompt.js
node --check ~/.codex/hooks/everos-memory-stop.js
```

setup 会自动新建或合并到 `~/.codex/hooks.json`：

```json
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
```

如果已有 `hooks.json`，setup 会保留原内容并补齐 EverOS 两个 hook。

## 8. 做题专用记忆约定

建议把 AIDP 记忆分成这些类型：

- `problem`: 原题、条件、限制。
- `solution_pattern`: 解法套路，例如二分、DP 状态设计、单调栈。
- `mistake`: 错因，例如边界、溢出、复杂度误判、读题漏条件。
- `template`: 可复用代码模板。
- `review`: 周期复盘，总结薄弱点。

做题时给 Codex 的推荐 prompt：

```text
这是一个做题任务。先查 EverOS AIDP 记忆，重点找相似题、错因、模板和边界条件。
如果命中高置信记忆，先用一句话说明命中点，然后基于当前题目重新推导，不要机械复用旧答案。

题目：
<粘贴题目>

我的目标：
- 先给思路和复杂度
- 再给代码
- 最后列出容易错的边界
```

复盘 prompt：

```text
把这次做题过程沉淀到 EverOS AIDP 记忆：
- 题型
- 核心套路
- 我卡住的点
- 错误原因
- 下次遇到相似题应优先检查什么
```

## 9. 同步策略

只同步 Markdown：

```text
~/Obsidian/EverOS-Memory/everos-v2/
```

不要同步：

```text
~/Obsidian/EverOS-Memory/everos-v2/.index/
~/Obsidian/EverOS-Memory/everos-v2/.tmp/
```

推荐 `.gitignore`：

```gitignore
.index/
.tmp/
.DS_Store
```

新机器首次同步后重建索引：

```bash
cd ~/Documents/daily/everos-codex-oauth-poc
rm -rf ~/Obsidian/EverOS-Memory/everos-v2/.index/lancedb
EVEROS_CONFIG_FILE=~/.everos/config.toml uv run everos cascade sync
```

如果 SQLite 也要重建：

```bash
rm -rf ~/Obsidian/EverOS-Memory/everos-v2/.index/sqlite
EVEROS_CONFIG_FILE=~/.everos/config.toml uv run everos server start --host 127.0.0.1 --port 8000
```

## 10. Session 与 LanceDB 处理逻辑

读路径：

```text
UserPromptSubmit hook
  -> everos-memory search --limit 1
  -> 高置信命中注入当前请求
```

写路径：

```text
Codex session JSONL
  -> Stop hook
  -> everos import codex-v2
  -> v2 中文 Markdown 记忆
  -> cascade
  -> SQLite + LanceDB
```

Session 处理：

- Codex 原始 session 在 `~/.codex/sessions`。
- Stop hook 在每轮结束后触发。
- 当前 session 会被排除，避免导入半截对话。
- 只导入修改时间超过 180 秒的稳定 session。
- 每次 Stop 最多导入 1 个 session，避免拖慢 Codex。
- 导入日志在 `~/.codex/log/everos-memory-hook.log`。

去重规则：

- 默认开启 `--skip-existing`。
- 已导入 session 写入 `<memory-root>/codex/.system/import-manifest.jsonl`。
- 补导时按 `project + session_id` 跳过重复 session。
- 文件名包含日期、可读标题和 session 短 id，避免同标题覆盖。
- 超大 session 先落到 `codex/inbox/oversized/`，不直接进入正式记忆。

去噪规则：

- 跳过没有用户消息或没有助手回复的 session。
- 跳过 Codex 注入的元信息，例如 `AGENTS.md instructions`、`environment_context`、内部上下文和 aborted 标记。
- 默认启用低价值过滤。
- 跳过纯确认类低价值 session，例如 `ok`、`好的`、`继续`。
- 跳过语义字符数少于 24 的极短 session。

手工补导历史 session：

```bash
cd ~/Documents/daily/everos-codex-oauth-poc
EVEROS_CONFIG_FILE=~/.everos/config.toml \
uv run everos import codex-v2 \
  --sessions-dir ~/.codex/sessions \
  --newest \
  --limit 20 \
  --min-age-seconds 180 \
  --skip-existing \
  --defer-oversized
```

LanceDB 处理：

- Markdown 是记忆本体，需要跨设备同步。
- SQLite 保存队列、状态、审计和系统元数据。
- LanceDB 保存检索索引，包括向量、BM25 和过滤字段。
- `.index/` 是派生数据，不同步到其他设备。
- 新设备同步 Markdown 后，本地重建 SQLite/LanceDB。

重建 LanceDB：

```bash
rm -rf ~/Obsidian/EverOS-Memory/everos-v2/.index/lancedb
EVEROS_CONFIG_FILE=~/.everos/config.toml uv run everos cascade sync
```

检查状态：

```bash
everos-memory doctor
EVEROS_CONFIG_FILE=~/.everos/config.toml uv run everos cascade status
```

## 11. 开机自启

setup 已经创建并加载 launchd 服务。检查：

```bash
launchctl print gui/$(id -u)/com.lengxiaochu.everos-memory | sed -n '1,80p'
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

## 12. 验收清单

```bash
everos-memory doctor
```

期望：

- `sqlite: present`
- `lancedb: present`
- `embedding: local_hash` 或实际 embedding provider

做一次搜索：

```bash
everos-memory search "二分 边界 条件" --project AIDP --limit 3
```

做一次 Codex 测试：

```text
我这题看起来像二分。先查一下 AIDP 记忆里类似的错因和模板。
```

看 hook 日志：

```bash
tail -50 ~/.codex/log/everos-memory-hook.log
```

看 Dashboard：

```text
http://127.0.0.1:8000/dashboard/
```

## 13. 给 Codex 的一键落地 Prompt

把下面这段发给 Mac mini 上的 Codex：

```text
我要在这台 Mac mini 上接入 EverOS 作为本地长期记忆，主要用于做题。

请按以下目标落地，不要只给建议：

1. 使用 ~/Documents/daily/everos-codex-oauth-poc 作为 EverOS repo。
2. 使用 ~/Obsidian/EverOS-Memory/everos-v2 作为 memory root。
3. 执行 bash scripts/setup_codex_everos_memory.sh。
4. 检查 ~/.everos/config.toml：
   - LLM fallback: grok_oauth -> codex_oauth -> openai
   - extraction_language = zh，所有沉淀到 EverOS 的 Markdown 必须中文
   - embedding 先用 local_hash；如果我提供 DeepInfra key，再切 Qwen embedding。
5. 启动 EverOS 本地 API: 127.0.0.1:8000。
6. 确认 ~/.local/bin/everos-memory 可用。
7. 确认 ~/.codex/AGENTS.md 只保留短路由；细则放 ~/.codex/rules/everos-memory.md。
8. 确认 Codex UserPromptSubmit / Stop hooks 已接入：前者预召回，后者异步导入稳定 session。
9. 为做题场景使用 AIDP 项目路由：
   - 相似题
   - 错题
   - 解法套路
   - 边界条件
   - 代码模板
10. 不要接 MCP。
11. 最后用这些命令验收：
    - everos-memory doctor
    - everos-memory search "二分 边界 条件" --project AIDP --limit 3
    - uv run everos cascade status
    - 打开 http://127.0.0.1:8000/dashboard/

请边改边验证，最终只汇报：
- 改了哪些文件
- 哪些命令通过
- 还剩什么需要我提供，例如 API key 或 auth login
```
