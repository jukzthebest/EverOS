# Codex + EverOS 本地记忆落地说明

这套改造把 EverOS 作为 Codex 的本地长期记忆，不接 MCP，使用本地 HTTP API、轻量 CLI 和 Codex hooks。

## 包含什么

- OAuth / fallback LLM：`grok_oauth -> codex_oauth -> openai`。
- 本地 embedding 兜底：`local_hash`，没有 API key 也能启动和建索引。
- Dashboard：`http://127.0.0.1:8000/dashboard/`。
- Codex session 导入：`everos import codex-structured`，直接写 结构化中文 Markdown。
- 快速检索 CLI：`scripts/everos-memory`。
- Codex hooks 模板：提交前预召回，结束后异步导入稳定 session。
- 一键 setup 脚本：`scripts/setup_codex_everos_memory.sh`。

## 快速安装

```bash
git clone -b codex/everos-codex-memory-setup \
  git@github.com:jukzthebest/EverOS.git \
  ~/Documents/daily/everos-codex-oauth-poc
cd ~/Documents/daily/everos-codex-oauth-poc
brew install uv jq
uv sync
bash scripts/setup_codex_everos_memory.sh
```

默认记忆根目录：

```text
~/Obsidian/EverOS-Memory/everos
```

默认配置文件：

```text
~/.everos/config.toml
```

## 启动服务

`scripts/setup_codex_everos_memory.sh` 会自动安装并启动 launchd 服务：
`~/Library/LaunchAgents/com.lengxiaochu.everos-memory.plist`。

验证：

```bash
everos-memory doctor
everos-memory search "二分 边界 条件" --project AIDP --limit 3
```

如果本机开了代理，确保 localhost 不走代理：

```bash
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}127.0.0.1,localhost"
export no_proxy="$NO_PROXY"
```

`setup_codex_everos_memory.sh` 会自动把这两行写入 `~/.zshrc`。`everos-memory`
自身也会绕过代理访问 `127.0.0.1:8000`。

如果需要手动排障，可以前台启动一次：

```bash
cd ~/Documents/daily/everos-codex-oauth-poc
EVEROS_CONFIG_FILE=~/.everos/config.toml \
uv run everos server start --host 127.0.0.1 --port 8000 --log-level info
```

## 后续步骤

1. 打开 Dashboard：`http://127.0.0.1:8000/dashboard/`。
2. 在 Codex 中做一次题目测试，让它先查 EverOS AIDP 记忆。
3. 一个 session 结束后等 3 分钟，再看导入日志：

```bash
tail -50 ~/.codex/log/everos-memory-hook.log
```

4. 同步记忆时只同步 Markdown 本体，不同步索引：

```text
~/Obsidian/EverOS-Memory/everos/
```

不要同步：

```text
~/Obsidian/EverOS-Memory/everos/.index/
~/Obsidian/EverOS-Memory/everos/.tmp/
```

5. 新设备同步 Markdown 后，本地重建索引：

```bash
rm -rf ~/Obsidian/EverOS-Memory/everos/.index/lancedb
EVEROS_CONFIG_FILE=~/.everos/config.toml uv run everos cascade sync
```

## Session 与 LanceDB 处理逻辑

Codex 读路径：

```text
UserPromptSubmit hook
  -> everos-memory search --limit 1
  -> 高置信命中注入当前请求
```

Codex session 的写入链路：

```text
Codex session JSONL
  -> Stop hook
  -> everos import codex-structured
  -> 结构化中文 Markdown 记忆
  -> cascade
  -> SQLite + LanceDB
```

Stop hook 不导入当前正在写入的 session。它会：

- 监听 Codex `Stop` 事件。
- 检查 `http://127.0.0.1:8000` 是否可用。
- 跳过当前 session id，避免导入半截对话。
- 只导入 mtime 超过 180 秒的稳定 session。
- 每次最多导入 1 个 session，避免 Stop 阶段卡顿。
- 使用 `/tmp/everos-memory-import.lock` 防并发。
- 写入后自动执行 `everos cascade sync`，让 SQLite/LanceDB 跟上 Markdown。

去重规则：

- 默认开启 `--skip-existing`。
- 已导入 session 会写入 `<memory-root>/codex/.system/import-manifest.jsonl`。
- 补导时会先读取这个 manifest，按 `project + session_id` 跳过重复 session。
- 文件名包含日期、可读标题和 session 短 id，避免同标题覆盖。
- 超大 session 先落到 `codex/inbox/oversized/`，不直接污染正式记忆。

去噪规则：

- 跳过没有用户消息或没有助手回复的 session。
- 跳过 Codex 注入的元信息，例如 `AGENTS.md instructions`、`environment_context`、内部上下文和 aborted 标记。
- 默认启用低价值过滤。
- 跳过纯确认类低价值 session，例如 `ok`、`好的`、`继续`。
- 跳过语义字符数少于 24 的极短 session。

手工补导历史 session：

```bash
EVEROS_CONFIG_FILE=~/.everos/config.toml \
uv run everos import codex-structured \
  --sessions-dir ~/.codex/sessions \
  --newest \
  --limit 20 \
  --min-age-seconds 180 \
  --skip-existing \
  --defer-oversized
```

全量 LLM 精炼：

```bash
EVEROS_CONFIG_FILE=~/.everos/config.toml \
uv run python scripts/refine_codex_memory.py \
  --output-root ~/Obsidian/EverOS-Memory/everos \
  --concurrency 3
```

精炼脚本会读取 manifest 中的原始 session 路径，重新生成更短的中文
Markdown，并按 `dedupe_key` 做跨 session 合并。失败前不会覆盖正在使用的
Markdown；成功后再重建索引。

LanceDB 的处理逻辑：

- Markdown 是记忆本体。
- SQLite 保存队列、状态、审计和系统元数据。
- LanceDB 保存检索索引，包括向量、BM25 和过滤字段。
- `.index/` 是本机派生数据，不跨设备同步。
- 新设备拿到 Markdown 后，本地跑 `everos cascade sync` 重建索引。

重建 LanceDB：

```bash
rm -rf ~/Obsidian/EverOS-Memory/everos/.index/lancedb
EVEROS_CONFIG_FILE=~/.everos/config.toml uv run everos cascade sync
```

检查状态：

```bash
everos-memory doctor
EVEROS_CONFIG_FILE=~/.everos/config.toml uv run everos cascade status
```

## 做题场景规则

- 项目域使用 `AIDP`。
- 所有沉淀到 EverOS 的 Markdown 使用中文。
- 优先沉淀相似题、错题原因、边界条件、代码模板和证明套路。
- 召回旧题时不机械复述旧答案，必须结合当前题面重新推导。

详细方案见：

```text
docs/macmini-everos-aidp-setup.md
```
