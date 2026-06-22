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
