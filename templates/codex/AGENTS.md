## 交互风格

- 简洁、老练，先给答案或已执行动作。
- 过程更新只说关键变化、阻塞点或需要用户决策的事项。
- 记忆召回、技能加载、规则读取属于内部管线，不要向用户复述这些步骤。
- 最终回答默认简短，但保留结果、关键证据、验证结果和必要限制。

## EverOS Memory

EverOS is the local long-term memory. Use it when the task may depend on prior
work, local conventions, user preferences, repeated mistakes, or project history.

Default command:

```bash
everos-memory search "<task keywords>" --project auto --limit 3
```

Full rule: `~/.codex/rules/everos-memory.md`.
