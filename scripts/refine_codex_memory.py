#!/usr/bin/env python3
"""LLM refine Codex session memory into the canonical EverOS markdown root."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from everos.component.llm.client import get_llm_client
from everos.component.llm.protocol import ChatMessage
from everos.entrypoints.cli.commands.import_cmd import (
    _clean_import_text,
    _file_sha256,
    _frontmatter,
    _is_meta_text,
    _safe_id,
    _slugify,
)

DEFAULT_OUTPUT_ROOT = Path("~/Obsidian/EverOS-Memory/everos").expanduser()
DEFAULT_SOURCE_ROOT = DEFAULT_OUTPUT_ROOT
DEFAULT_APP_ID = "codex"
DEFAULT_PROJECTS = ("Yundun", "CSPM", "AIDP", "Daily")

SYSTEM_PROMPT = """\
你是 EverOS/Codex 长期记忆提炼器。

目标：把 Codex session 提炼成少量可复用的中文长期记忆。

硬性规则：
- 只输出 JSON，不要 Markdown fence。
- 所有自然语言用简体中文。
- 保留命令、路径、文件名、版本号、SQL、环境变量、ID 的原文，不要改写。
- 不要记录过程废话、寒暄、工具调用流水、AGENTS 注入、memory citation。
- 如果 session 没有长期价值，返回 {"records": []}。
- 每个 session 最多输出 3 条 records；宁可少，不要泛化。
- 直接命令/模板类内容必须把可复制命令放在 body 前部。

JSON schema：
{
  "records": [
    {
      "type": "episode|case|playbook",
      "project": "Yundun|CSPM|AIDP|Daily|Misc",
      "domain": "短英文域，例如 ops/db_migration/aidp_qc",
      "artifact_type": "command|incident|migration|workflow|template|note",
      "answer_shape": "command|checklist|explanation|procedure|summary",
      "title": "中文短标题，保留关键英文标识",
      "summary": "1-2 句中文摘要",
      "entities": ["关键实体"],
      "confidence": "high|medium|low",
      "visibility": "active|needs_review",
      "dedupe_key": "稳定英文 key，同一问题跨 session 必须相同",
      "body": "中文 Markdown 正文"
    }
  ]
}
"""

MERGE_PROMPT = """\
你是 EverOS 长期记忆合并器。

输入是多条相同 dedupe_key 的候选记忆。请合并为一条更准确、更短、更完整的中文记忆。

硬性规则：
- 只输出 JSON 对象，不要 Markdown fence。
- 保留所有关键命令、路径、版本号、SQL、环境变量。
- 删除重复说法和过程废话。
- 如果候选之间冲突，在 body 里用“## 注意”指出冲突和来源 session。
- 输出字段沿用输入第一条的 schema。

JSON schema：
{
  "record": {
    "type": "episode|case|playbook",
    "project": "...",
    "domain": "...",
    "artifact_type": "...",
    "answer_shape": "...",
    "title": "...",
    "summary": "...",
    "entities": ["..."],
    "confidence": "high|medium|low",
    "visibility": "active|needs_review",
    "dedupe_key": "...",
    "body": "..."
  }
}
"""


@dataclass(frozen=True)
class WorkItem:
    session_id: str
    project: str
    path: Path
    source_hash: str
    oversized: bool = False


@dataclass
class Message:
    role: str
    content: str
    timestamp_ms: int = 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--app-id", default=DEFAULT_APP_ID)
    parser.add_argument("--project", action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--max-chars", type=int, default=22000)
    parser.add_argument("--max-messages", type=int, default=420)
    parser.add_argument("--max-message-chars", type=int, default=2400)
    parser.add_argument("--include-oversized", action="store_true", default=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--replace", action="store_true", default=True)
    parser.add_argument("--no-switch", action="store_true")
    args = parser.parse_args(argv)

    source_root = args.source_root.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    projects = tuple(args.project or DEFAULT_PROJECTS)
    items = load_work_items(
        source_root,
        app_id=args.app_id,
        projects=projects,
        include_oversized=args.include_oversized,
    )
    if args.limit:
        items = items[: args.limit]
    print(
        f"refine plan: sessions={len(items)} source={source_root} "
        f"output={output_root} projects={','.join(projects)}"
    )
    if args.dry_run:
        for item in items[:30]:
            print(
                f"- {item.project} {item.session_id} "
                f"{item.path.stat().st_size / 1024 / 1024:.1f}MiB {item.path}"
            )
        return 0

    started = time.time()
    result = asyncio.run(
        refine_all(
            items,
            max_chars=args.max_chars,
            max_messages=args.max_messages,
            max_message_chars=args.max_message_chars,
            concurrency=max(1, args.concurrency),
        )
    )
    records = result["records"]
    failures = result["failures"]
    merged = asyncio.run(merge_records(records, concurrency=max(1, args.concurrency)))
    write_root(
        output_root,
        app_id=args.app_id,
        records=merged,
        items=items,
        failures=failures,
        replace=args.replace,
    )
    elapsed = time.time() - started
    print(
        "refine complete: "
        f"sessions={len(items)} records={len(records)} merged={len(merged)} "
        f"failures={len(failures)} elapsed={elapsed:.1f}s output={output_root}"
    )
    if not args.no_switch:
        print(f"canonical memory root ready: {output_root}")
    return 0 if not failures else 1


def load_work_items(
    source_root: Path,
    *,
    app_id: str,
    projects: tuple[str, ...],
    include_oversized: bool,
) -> list[WorkItem]:
    allowed = set(projects)
    items: list[WorkItem] = []
    seen: set[Path] = set()
    manifest = source_root / app_id / ".system" / "import-manifest.jsonl"
    if manifest.exists():
        with manifest.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                project = str(entry.get("project") or "Misc")
                path = Path(str(entry.get("session_path") or "")).expanduser()
                if project not in allowed or not path.exists() or path in seen:
                    continue
                seen.add(path)
                items.append(
                    WorkItem(
                        session_id=_safe_id(str(entry.get("session_id") or path.stem)),
                        project=project,
                        path=path,
                        source_hash=str(entry.get("source_hash") or _file_sha256(path)),
                    )
                )
    refine_manifest = source_root / app_id / ".system" / "refine-manifest.jsonl"
    if refine_manifest.exists():
        for source in iter_refine_manifest_sources(refine_manifest):
            project = str(source.get("project") or "Misc")
            path = Path(str(source.get("path") or "")).expanduser()
            if project not in allowed or not path.exists() or path in seen:
                continue
            seen.add(path)
            source_hash = str(source.get("source_hash") or _file_sha256(path))
            items.append(
                WorkItem(
                    session_id=_safe_id(str(source.get("session_id") or path.stem)),
                    project=project,
                    path=path,
                    source_hash=source_hash,
                )
            )
    if include_oversized:
        for stub in sorted((source_root / app_id / "inbox" / "oversized").glob("*.md")):
            meta = read_frontmatter(stub)
            source = meta.get("source") if isinstance(meta.get("source"), dict) else {}
            project = str(meta.get("project") or "Misc")
            path = Path(str(source.get("path") or "")).expanduser()
            if project not in allowed or not path.exists() or path in seen:
                continue
            seen.add(path)
            items.append(
                WorkItem(
                    session_id=_safe_id(str(source.get("session_id") or path.stem)),
                    project=project,
                    path=path,
                    source_hash=str(meta.get("source_hash") or _file_sha256(path)),
                    oversized=True,
                )
            )
    return items


def iter_refine_manifest_sources(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            project = entry.get("project")
            source = (
                entry.get("source")
                if isinstance(entry.get("source"), dict)
                else {}
            )
            sessions = source.get("sessions")
            if isinstance(sessions, list):
                for session in sessions:
                    if isinstance(session, dict):
                        yield {**session, "project": project}
            elif source:
                yield {**source, "project": project}


def read_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---\n"):
        return {}
    try:
        raw = text.split("---\n", 2)[1]
    except IndexError:
        return {}
    data = yaml.safe_load(raw) or {}
    return data if isinstance(data, dict) else {}


async def refine_all(
    items: list[WorkItem],
    *,
    max_chars: int,
    max_messages: int,
    max_message_chars: int,
    concurrency: int,
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(concurrency)
    records: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    async def run_one(index: int, item: WorkItem) -> None:
        async with semaphore:
            try:
                item_records = await refine_item(
                    item,
                    max_chars=max_chars,
                    max_messages=max_messages,
                    max_message_chars=max_message_chars,
                )
                records.extend(item_records)
                print(
                    f"[{index}/{len(items)}] ok {item.project} "
                    f"{item.session_id} records={len(item_records)}"
                )
            except Exception as exc:
                failures.append(
                    {
                        "session_id": item.session_id,
                        "project": item.project,
                        "path": str(item.path),
                        "error": str(exc),
                    }
                )
                print(
                    f"[{index}/{len(items)}] fail {item.project} "
                    f"{item.session_id}: {exc}",
                    file=sys.stderr,
                )

    await asyncio.gather(*(run_one(i, item) for i, item in enumerate(items, 1)))
    return {"records": records, "failures": failures}


async def refine_item(
    item: WorkItem,
    *,
    max_chars: int,
    max_messages: int,
    max_message_chars: int,
) -> list[dict[str, Any]]:
    messages, session_date = parse_session_messages(
        item.path,
        max_messages=max_messages,
        max_message_chars=max_message_chars,
    )
    transcript = transcript_text(messages, max_chars=max_chars)
    if not transcript.strip():
        return []
    payload = {
        "project": item.project,
        "session_id": item.session_id,
        "session_date": session_date,
        "source_path": str(item.path),
        "source_hash": item.source_hash,
        "oversized": item.oversized,
        "transcript": transcript,
    }
    response = await llm_json(
        SYSTEM_PROMPT,
        "请提炼这个 session：\n"
        + json.dumps(payload, ensure_ascii=False, indent=2),
    )
    raw_records = response.get("records") if isinstance(response, dict) else []
    if not isinstance(raw_records, list):
        return []
    records: list[dict[str, Any]] = []
    for raw in raw_records[:3]:
        if not isinstance(raw, dict):
            continue
        record = normalize_record(raw, item, session_date)
        if record is not None:
            records.append(record)
    return records


def parse_session_messages(
    path: Path,
    *,
    max_messages: int,
    max_message_chars: int,
) -> tuple[list[Message], str]:
    first: list[Message] = []
    last: deque[Message] = deque(maxlen=max_messages // 2)
    timestamps: list[int] = []
    keep_first = max_messages // 2
    total = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = message_from_event(event, max_message_chars=max_message_chars)
            if msg is None:
                continue
            total += 1
            if msg.timestamp_ms:
                timestamps.append(msg.timestamp_ms)
            if len(first) < keep_first:
                first.append(msg)
            else:
                last.append(msg)
    if total <= max_messages:
        messages = first + list(last)
    else:
        omitted = Message(
            role="assistant",
            content=f"[中间省略 {total - len(first) - len(last)} 条低优先级消息]",
        )
        messages = first + [omitted] + list(last)
    if timestamps:
        session_date = datetime.fromtimestamp(min(timestamps) / 1000, UTC).date()
    else:
        session_date = datetime.fromtimestamp(path.stat().st_mtime, UTC).date()
    return messages, session_date.isoformat()


def message_from_event(
    event: dict[str, Any],
    *,
    max_message_chars: int,
) -> Message | None:
    if event.get("type") != "response_item":
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "message":
        return None
    role = payload.get("role")
    if role not in {"user", "assistant"}:
        return None
    texts: list[str] = []
    for item in payload.get("content") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        text = item.get("text") if kind in {"input_text", "output_text"} else None
        if isinstance(text, str):
            cleaned = _clean_import_text(text.strip())
            if cleaned and not _is_meta_text(cleaned):
                texts.append(cleaned)
    content = "\n\n".join(texts).strip()
    if not content:
        return None
    return Message(
        role=role,
        content=clip_for_llm(content, max_message_chars),
        timestamp_ms=timestamp_ms(event.get("timestamp")),
    )


def timestamp_ms(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value * 1000) if value < 10_000_000_000 else int(value)
    if isinstance(value, str) and value:
        try:
            timestamp = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            ).timestamp()
            return int(timestamp * 1000)
        except ValueError:
            return 0
    return 0


def transcript_text(messages: list[Message], *, max_chars: int) -> str:
    parts: list[str] = []
    total = 0
    for msg in messages:
        block = f"[{msg.role}]\n{msg.content.strip()}\n"
        if total + len(block) > max_chars:
            remaining = max_chars - total
            if remaining > 500:
                parts.append(block[:remaining])
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts)


async def llm_json(system: str, user: str) -> dict[str, Any]:
    client = get_llm_client()
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = await client.chat(
                [
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="user", content=user),
                ],
                temperature=0,
                max_tokens=5000,
            )
            return parse_json_object(response.content)
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(1 + attempt)
    raise RuntimeError(f"LLM JSON failed: {last_error}")


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end < start:
            raise
        data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM output is not a JSON object")
    return data


def normalize_record(
    raw: dict[str, Any],
    item: WorkItem,
    session_date: str,
) -> dict[str, Any] | None:
    body = str(raw.get("body") or "").strip()
    title = str(raw.get("title") or "").strip()
    if not body or not title:
        return None
    kind = str(raw.get("type") or "episode").strip()
    if kind not in {"episode", "case", "playbook"}:
        kind = "episode"
    project = _safe_id(str(raw.get("project") or item.project))
    dedupe_key = str(raw.get("dedupe_key") or "").strip()
    if not dedupe_key:
        dedupe_key = generated_dedupe_key(project, kind, title, raw.get("entities"))
    dedupe_key = _safe_id(dedupe_key.lower())
    record_id = make_record_id(kind, session_date, dedupe_key, item.session_id)
    return {
        "schema_version": 2,
        "type": kind,
        "id": record_id,
        "project": project,
        "domain": safe_text(raw.get("domain"), "general"),
        "artifact_type": safe_text(raw.get("artifact_type"), "note"),
        "answer_shape": safe_text(raw.get("answer_shape"), "summary"),
        "title": title[:140],
        "summary": safe_text(raw.get("summary"), "")[:500],
        "entities": normalize_entities(raw.get("entities")),
        "confidence": normalize_choice(
            raw.get("confidence"),
            {"high", "medium", "low"},
            "medium",
        ),
        "visibility": normalize_choice(
            raw.get("visibility"),
            {"active", "needs_review"},
            "active",
        ),
        "dedupe_key": dedupe_key,
        "source": {
            "agent": "codex",
            "session_id": item.session_id,
            "session_date": session_date,
            "path": str(item.path),
            "source_hash": item.source_hash,
            "refined_at": datetime.now(UTC).isoformat(),
            "oversized": item.oversized,
        },
        "source_hash": item.source_hash,
        "body": body.rstrip() + "\n",
    }


def safe_text(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text or default


def normalize_choice(value: Any, allowed: set[str], default: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else default


def normalize_entities(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    entities: list[str] = []
    for item in value[:30]:
        text = str(item).strip()
        if text and text not in entities:
            entities.append(text)
    return entities


def generated_dedupe_key(
    project: str,
    kind: str,
    title: str,
    entities: Any,
) -> str:
    entity_text = "_".join(normalize_entities(entities)[:5])
    base = f"{project}_{kind}_{entity_text}_{title}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def make_record_id(
    kind: str,
    session_date: str,
    dedupe_key: str,
    session_id: str,
) -> str:
    prefix = {"episode": "ep", "case": "case", "playbook": "pb"}[kind]
    short_session = session_id.replace("-", "")[:8] + session_id.replace("-", "")[-4:]
    slug = _slugify(dedupe_key)[:48]
    return _safe_id(f"{prefix}_{session_date.replace('-', '')}_{short_session}_{slug}")


async def merge_records(
    records: list[dict[str, Any]],
    *,
    concurrency: int,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[(record["project"], record["type"], record["dedupe_key"])].append(record)

    semaphore = asyncio.Semaphore(concurrency)
    merged: list[dict[str, Any]] = []

    async def merge_group(group: list[dict[str, Any]]) -> None:
        if len(group) == 1:
            merged.append(group[0])
            return
        async with semaphore:
            try:
                merged.append(await llm_merge_group(group))
            except Exception:
                merged.append(fallback_merge_group(group))

    await asyncio.gather(*(merge_group(group) for group in groups.values()))
    merged.sort(key=lambda r: (r["project"], r["type"], r["title"]))
    return merged


async def llm_merge_group(group: list[dict[str, Any]]) -> dict[str, Any]:
    compact = []
    for record in group[:12]:
        compact.append(
            {
                key: record.get(key)
                for key in (
                    "type",
                    "project",
                    "domain",
                    "artifact_type",
                    "answer_shape",
                    "title",
                    "summary",
                    "entities",
                    "confidence",
                    "visibility",
                    "dedupe_key",
                    "body",
                )
            }
            | {"source": record.get("source")}
        )
    data = await llm_json(
        MERGE_PROMPT,
        json.dumps({"records": compact}, ensure_ascii=False, indent=2),
    )
    raw = data.get("record") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        raise ValueError("missing merged record")
    base = group[0]
    merged = normalize_record(
        raw,
        WorkItem(
            session_id=str(base["source"].get("session_id") or "merged"),
            project=str(base["project"]),
            path=Path(str(base["source"].get("path") or ".")),
            source_hash=str(base.get("source_hash") or ""),
        ),
        str(base["source"].get("session_date") or today()),
    )
    if merged is None:
        raise ValueError("invalid merged record")
    merged["id"] = base["id"]
    merged["source"] = merged_source(group)
    merged["source_hash"] = merged_source_hash(group)
    return merged


def fallback_merge_group(group: list[dict[str, Any]]) -> dict[str, Any]:
    best = group[0].copy()
    best["source"] = merged_source(group)
    best["source_hash"] = merged_source_hash(group)
    return best


def merged_source(group: list[dict[str, Any]]) -> dict[str, Any]:
    sessions = []
    for record in group:
        source = record.get("source") if isinstance(record.get("source"), dict) else {}
        sessions.append(
            {
                "session_id": source.get("session_id"),
                "session_date": source.get("session_date"),
                "path": source.get("path"),
                "source_hash": source.get("source_hash") or record.get("source_hash"),
            }
        )
    return {
        "agent": "codex",
        "merged": True,
        "source_count": len(group),
        "sessions": sessions,
        "refined_at": datetime.now(UTC).isoformat(),
    }


def merged_source_hash(group: list[dict[str, Any]]) -> str:
    raw = "|".join(str(record.get("source_hash") or "") for record in group)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def write_root(
    root: Path,
    *,
    app_id: str,
    records: list[dict[str, Any]],
    items: list[WorkItem],
    failures: list[dict[str, str]],
    replace: bool,
) -> None:
    if root.exists() and replace:
        backup = (
            root.parent
            / f"{root.name}-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.move(str(root), str(backup))
        print(f"backed up existing canonical root: {backup}")
    ensure_root(root, app_id)
    manifest = root / app_id / ".system" / "refine-manifest.jsonl"
    for record in records:
        output_path = write_record(root, app_id, record)
        entry = {
            "record_id": record["id"],
            "project": record["project"],
            "type": record["type"],
            "dedupe_key": record["dedupe_key"],
            "output_path": str(output_path),
            "source": record.get("source"),
            "source_hash": record.get("source_hash"),
            "refined_at": datetime.now(UTC).isoformat(),
        }
        with manifest.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    write_quality_report(root, app_id, records, items, failures)


def ensure_root(root: Path, app_id: str) -> None:
    (root / app_id / ".system").mkdir(parents=True, exist_ok=True)
    (root / app_id / "inbox" / "needs-review").mkdir(parents=True, exist_ok=True)
    (root / ".index").mkdir(parents=True, exist_ok=True)
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(".index/\n.tmp/\n", encoding="utf-8")
    migrations = root / app_id / ".system" / "schema-migrations.jsonl"
    migrations.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "created_at": datetime.now(UTC).isoformat(),
                "note": "Canonical LLM-refined Codex memory layout.",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def write_record(root: Path, app_id: str, record: dict[str, Any]) -> Path:
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    session_date = source.get("session_date")
    if not isinstance(session_date, str):
        sessions = (
            source.get("sessions")
            if isinstance(source.get("sessions"), list)
            else []
        )
        if sessions:
            session_date = str((sessions[0] or {}).get("session_date") or today())
        else:
            session_date = today()
    year, month, *_ = session_date.split("-")
    kind_dir = {
        "episode": "episodes",
        "case": "cases",
        "playbook": "playbooks",
    }[record["type"]]
    filename = (
        f"{session_date}-{_slugify(record['title'])[:64]}-"
        f"{record['id'][-12:]}.md"
    )
    path = (
        root
        / app_id
        / "projects"
        / record["project"]
        / kind_dir
        / year
        / month
        / filename
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {k: v for k, v in record.items() if k != "body"}
    path.write_text(
        _frontmatter(meta) + record["body"].rstrip() + "\n",
        encoding="utf-8",
    )
    ensure_project_file(root, app_id, record["project"])
    return path


def ensure_project_file(root: Path, app_id: str, project: str) -> None:
    path = root / app_id / "projects" / project / "PROJECT.md"
    if path.exists():
        return
    meta = {
        "schema_version": 2,
        "type": "profile",
        "id": f"profile_{project}",
        "project": project,
        "visibility": "active",
        "updated_at": datetime.now(UTC).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _frontmatter(meta)
        + f"# {project} 项目索引\n\n"
        + "这个文件只记录项目级长期边界和别名，不承载具体问题答案。\n",
        encoding="utf-8",
    )


def write_quality_report(
    root: Path,
    app_id: str,
    records: list[dict[str, Any]],
    items: list[WorkItem],
    failures: list[dict[str, str]],
) -> None:
    by_project: dict[str, int] = defaultdict(int)
    by_type: dict[str, int] = defaultdict(int)
    for record in records:
        by_project[str(record["project"])] += 1
        by_type[str(record["type"])] += 1
    lines = [
        "# EverOS 精炼质量报告",
        "",
        f"- source_sessions: {len(items)}",
        f"- refined_records: {len(records)}",
        f"- failures: {len(failures)}",
        "",
        "## Project 分布",
        "",
    ]
    for project, count in sorted(by_project.items()):
        lines.append(f"- {project}: {count}")
    lines.extend(["", "## Type 分布", ""])
    for kind, count in sorted(by_type.items()):
        lines.append(f"- {kind}: {count}")
    if failures:
        lines.extend(["", "## Failures", ""])
        for failure in failures[:50]:
            lines.append(
                f"- {failure['project']} {failure['session_id']}: {failure['error']}"
            )
    path = root / app_id / ".system" / "quality-report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def today() -> str:
    return datetime.now(UTC).date().isoformat()


def clip_for_llm(text: str, limit: int) -> str:
    compact = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if len(compact) <= limit:
        return compact
    head = compact[: int(limit * 0.7)]
    tail = compact[-int(limit * 0.25) :]
    return f"{head}\n\n[中间截断]\n\n{tail}"


if __name__ == "__main__":
    os.environ.setdefault(
        "EVEROS_CONFIG_FILE",
        str(Path("~/.everos/config.toml").expanduser()),
    )
    raise SystemExit(main())
