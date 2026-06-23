"""Import external conversation archives into EverOS."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
import yaml

from everos.core.persistence import MemoryRoot

app = typer.Typer(
    name="import",
    help="Import local agent/session archives into EverOS",
    no_args_is_help=True,
)

_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_.@+-]+")
_UUID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
_OAI_MEM_CITATION_RE = re.compile(
    r"\s*<oai-mem-citation>.*?</oai-mem-citation>\s*",
    re.S,
)
_META_PREFIXES = (
    "# AGENTS.md instructions",
    "<environment_context>",
    "<codex_internal_context",
    "<skill>",
    "<turn_aborted>",
)
_LOW_VALUE_TEXTS = frozenset(
    {
        "ok",
        "okay",
        "好的",
        "好",
        "hello",
        "hi",
        "你好",
        "继续",
        "继续吧",
        "go on",
        "how old are u",
        "how old are you",
        "你多大",
    }
)
_GENERIC_TITLE_TEXTS = _LOW_VALUE_TEXTS | frozenset(
    {
        "commit",
        "提交",
        "提交吧",
        "hello",
        "hi",
        "在",
        "改",
        "继续处理",
    }
)
_DEFAULT_CODEX_SESSIONS_DIR = Path("~/.codex/sessions")
_DEFAULT_USER_ID = os.environ.get(
    "EVEROS_MEMORY_USER_ID", os.environ.get("USER", "user")
)
_DEFAULT_AGENT_ID = os.environ.get("EVEROS_MEMORY_AGENT_ID", "codex")
_DEFAULT_APP_ID = os.environ.get("EVEROS_MEMORY_APP_ID", "codex")
_DEFAULT_PROJECT_ID = os.environ.get("EVEROS_MEMORY_PROJECT", "default")
_DEFAULT_MEMORY_ROOT = Path(os.environ.get("EVEROS_MEMORY_ROOT", "~/.everos/memory"))


def _parse_codex_project_rules(value: str) -> tuple[tuple[re.Pattern[str], str], ...]:
    rules: list[tuple[re.Pattern[str], str]] = []
    for rule in value.split(","):
        if "=" not in rule:
            continue
        pattern, project_id = (part.strip() for part in rule.split("=", 1))
        if not pattern or not project_id:
            continue
        try:
            rules.append((re.compile(pattern, re.I), project_id))
        except re.error:
            continue
    return tuple(rules)


_CODEX_PROJECT_RULES = _parse_codex_project_rules(
    os.environ.get("EVEROS_CODEX_PROJECT_RULES", "")
)


@dataclass
class CodexMessage:
    role: str
    content: str
    timestamp_ms: int


@dataclass
class CodexSession:
    path: Path
    session_id: str
    project_id: str
    agent_id: str
    messages: list[CodexMessage]
    cwd: str | None = None


@app.command("codex")
def import_codex(
    sessions_dir: Annotated[
        Path,
        typer.Option(
            "--sessions-dir",
            help="Root containing Codex rollout JSONL files.",
        ),
    ] = _DEFAULT_CODEX_SESSIONS_DIR,
    base_url: Annotated[
        str,
        typer.Option("--base-url", help="EverOS server base URL."),
    ] = "http://127.0.0.1:8000",
    user_id: Annotated[
        str,
        typer.Option("--user-id", help="EverOS sender_id for user messages."),
    ] = _DEFAULT_USER_ID,
    agent_id: Annotated[
        str,
        typer.Option("--agent-id", help="EverOS sender_id for assistant messages."),
    ] = _DEFAULT_AGENT_ID,
    app_id: Annotated[
        str,
        typer.Option("--app-id", help="EverOS app_id scope."),
    ] = _DEFAULT_APP_ID,
    project_id: Annotated[
        str | None,
        typer.Option(
            "--project-id",
            help="Override project_id for every imported session.",
        ),
    ] = None,
    exclude_session_id: Annotated[
        list[str] | None,
        typer.Option(
            "--exclude-session-id",
            help="Skip a session id. Repeat for multiple active or unwanted sessions.",
        ),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Import at most N sessions."),
    ] = None,
    max_messages: Annotated[
        int | None,
        typer.Option("--max-messages", help="Skip sessions with more than N messages."),
    ] = None,
    min_age_seconds: Annotated[
        int,
        typer.Option(
            "--min-age-seconds",
            help=(
                "Only import session files whose mtime is at least N seconds old. "
                "Useful for hooks so active sessions are not imported partially."
            ),
        ),
    ] = 0,
    chunk_messages: Annotated[
        int | None,
        typer.Option(
            "--chunk-messages",
            help="Split sessions longer than N messages into replayable part sessions.",
        ),
    ] = None,
    newest: Annotated[
        bool,
        typer.Option("--newest/--oldest", help="Import newest sessions first."),
    ] = False,
    flush: Annotated[
        bool,
        typer.Option("--flush/--no-flush", help="Flush each session after add."),
    ] = True,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Parse and report without posting."),
    ] = False,
    skip_existing: Annotated[
        bool,
        typer.Option(
            "--skip-existing/--include-existing",
            help="Skip sessions whose session_id already appears in Markdown memory.",
        ),
    ] = True,
    skip_low_value: Annotated[
        bool,
        typer.Option(
            "--skip-low-value/--include-low-value",
            help="Skip sessions that are too thin or mostly internal noise.",
        ),
    ] = True,
    continue_on_error: Annotated[
        bool,
        typer.Option(
            "--continue-on-error/--fail-fast",
            help="Continue importing later sessions when add/flush fails.",
        ),
    ] = True,
) -> None:
    """Import local Codex Desktop/CLI session JSONL into EverOS."""
    sessions = list(_iter_codex_sessions(sessions_dir, agent_id, project_id))
    sessions = [session for session in sessions if session.messages]
    if min_age_seconds > 0:
        cutoff = time.time() - min_age_seconds
        sessions = [
            session for session in sessions if session.path.stat().st_mtime <= cutoff
        ]
    if exclude_session_id:
        excluded_ids = {_safe_id(session_id) for session_id in exclude_session_id}
        sessions = [
            session for session in sessions if session.session_id not in excluded_ids
        ]
    if newest:
        sessions.reverse()
    if max_messages is not None:
        sessions = [
            session for session in sessions if len(session.messages) <= max_messages
        ]
    skipped_low_value = 0
    if skip_low_value:
        before = len(sessions)
        sessions = [session for session in sessions if _has_memory_value(session)]
        skipped_low_value = before - len(sessions)
    skipped_existing = 0
    existing_ids: set[str] = set()
    if skip_existing:
        existing_ids = _existing_memory_session_ids(app_id)
        before = len(sessions)
        sessions = [
            session for session in sessions if session.session_id not in existing_ids
        ]
        skipped_existing = before - len(sessions)
    if chunk_messages is not None:
        if chunk_messages <= 0:
            raise typer.BadParameter("--chunk-messages must be greater than 0")
        sessions = _chunk_long_sessions(sessions, chunk_messages)
        if skip_existing:
            before = len(sessions)
            sessions = [
                session
                for session in sessions
                if session.session_id not in existing_ids
            ]
            skipped_existing += before - len(sessions)
    if limit is not None:
        sessions = sessions[:limit]

    total_messages = sum(len(session.messages) for session in sessions)
    typer.echo(
        f"parsed {len(sessions)} session(s), {total_messages} message(s) "
        f"from {sessions_dir}"
    )
    if skipped_existing:
        typer.echo(f"skipped {skipped_existing} existing session(s)")
    if skipped_low_value:
        typer.echo(f"skipped {skipped_low_value} low-value session(s)")
    if dry_run:
        for session in sessions[:10]:
            typer.echo(
                f"- {session.session_id} project={session.project_id} "
                f"messages={len(session.messages)} path={session.path}"
            )
        return

    imported = 0
    failed: list[tuple[CodexSession, str]] = []
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=120.0) as client:
        for session in sessions:
            payload = {
                "session_id": session.session_id,
                "app_id": app_id,
                "project_id": session.project_id,
                "messages": [
                    {
                        "sender_id": (
                            user_id if msg.role == "user" else session.agent_id
                        ),
                        "sender_name": "User" if msg.role == "user" else "Codex",
                        "role": msg.role,
                        "timestamp": msg.timestamp_ms,
                        "content": msg.content,
                    }
                    for msg in session.messages
                ],
            }
            try:
                response = client.post("/api/v1/memory/add", json=payload)
                response.raise_for_status()
                if flush:
                    flush_response = client.post(
                        "/api/v1/memory/flush",
                        json={
                            "session_id": session.session_id,
                            "app_id": app_id,
                            "project_id": session.project_id,
                        },
                    )
                    flush_response.raise_for_status()
            except Exception as exc:
                failed.append((session, str(exc)))
                typer.echo(
                    f"failed {session.session_id} messages={len(session.messages)}: "
                    f"{exc}",
                    err=True,
                )
                if not continue_on_error:
                    raise
                continue
            _record_imported_session_id(app_id, session.session_id)
            imported += 1
            typer.echo(
                f"imported {imported}/{len(sessions)} "
                f"{session.session_id} messages={len(session.messages)}"
            )
    if failed:
            typer.echo(f"completed with {len(failed)} failed session(s)", err=True)


@app.command("codex-structured")
def import_codex_v2(
    sessions_dir: Annotated[
        Path,
        typer.Option("--sessions-dir", help="Root containing Codex JSONL files."),
    ] = _DEFAULT_CODEX_SESSIONS_DIR,
    output_root: Annotated[
        Path,
        typer.Option("--output-root", help="Structured memory root to write."),
    ] = _DEFAULT_MEMORY_ROOT,
    user_id: Annotated[str, typer.Option("--user-id")] = _DEFAULT_USER_ID,
    agent_id: Annotated[str, typer.Option("--agent-id")] = _DEFAULT_AGENT_ID,
    app_id: Annotated[str, typer.Option("--app-id")] = _DEFAULT_APP_ID,
    project_id: Annotated[
        str | None,
        typer.Option("--project-id", help="Override project_id for every session."),
    ] = None,
    project: Annotated[
        list[str] | None,
        typer.Option("--project", help="Only import selected projects."),
    ] = None,
    newest: Annotated[bool, typer.Option("--newest/--oldest")] = False,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    min_size_bytes: Annotated[int, typer.Option("--min-size-bytes")] = 0,
    max_size_mib: Annotated[
        int,
        typer.Option(
            "--max-size-mib",
            help="Send larger sessions to inbox/oversized unless --include-oversized.",
        ),
    ] = 20,
    include_oversized: Annotated[
        bool,
        typer.Option("--include-oversized/--defer-oversized"),
    ] = False,
    min_age_seconds: Annotated[
        int,
        typer.Option(
            "--min-age-seconds",
            help="Only import session files whose mtime is at least N seconds old.",
        ),
    ] = 0,
    exclude_session_id: Annotated[
        list[str] | None,
        typer.Option("--exclude-session-id", help="Skip a session id."),
    ] = None,
    skip_existing: Annotated[
        bool,
        typer.Option("--skip-existing/--include-existing"),
    ] = True,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    backup_existing: Annotated[
        bool,
        typer.Option("--backup-existing/--no-backup-existing"),
    ] = True,
    replace: Annotated[
        bool,
        typer.Option("--replace/--append", help="Replace output_root before writing."),
    ] = False,
) -> None:
    """Import Codex sessions into the canonical structured markdown layout.

    This command writes the source-of-truth Markdown directly. SQLite and
    LanceDB remain derived and can be rebuilt with ``everos cascade sync``.
    """
    root = output_root.expanduser().resolve()
    allowed_projects = {p for p in project or []}
    excluded_session_ids = {_safe_id(value) for value in exclude_session_id or []}
    existing_session_keys = (
        _existing_v2_session_keys(root, app_id)
        if skip_existing and not replace
        else set()
    )
    oversized_limit = max_size_mib * 1024 * 1024
    paths = sorted(sessions_dir.expanduser().rglob("*.jsonl"))
    if newest:
        paths.reverse()
    sessions: list[CodexSession] = []
    oversized: list[CodexSession] = []
    now = time.time()
    for path in paths:
        size = path.stat().st_size
        if min_age_seconds and now - path.stat().st_mtime < min_age_seconds:
            continue
        if min_size_bytes and size < min_size_bytes:
            continue
        if size > oversized_limit and not include_oversized:
            meta_session = _parse_codex_session_meta(
                path,
                default_agent_id=agent_id,
                project_override=project_id,
            )
            if meta_session.session_id in excluded_session_ids:
                continue
            meta_key = (meta_session.project_id, meta_session.session_id)
            if meta_key in existing_session_keys:
                continue
            if allowed_projects and meta_session.project_id not in allowed_projects:
                continue
            oversized.append(meta_session)
            continue
        session = _parse_codex_session(
            path,
            default_agent_id=agent_id,
            project_override=project_id,
        )
        if session is None or not session.messages:
            continue
        if session.session_id in excluded_session_ids:
            continue
        if (session.project_id, session.session_id) in existing_session_keys:
            continue
        if allowed_projects and session.project_id not in allowed_projects:
            continue
        sessions.append(session)

    selected: list[CodexSession] = []
    skipped_low_value = 0
    skipped_duplicate = 0
    seen_session_keys: set[tuple[str, str]] = set()
    for session in sessions:
        if not _has_memory_value(session):
            skipped_low_value += 1
            continue
        key = (session.project_id, session.session_id)
        if key in seen_session_keys:
            skipped_duplicate += 1
            continue
        seen_session_keys.add(key)
        selected.append(session)
    if limit is not None:
        selected = selected[:limit]

    typer.echo(
        "structured import plan: "
        f"selected={len(selected)} oversized={len(oversized)} "
        f"low_value={skipped_low_value} duplicate={skipped_duplicate} root={root}"
    )
    if dry_run:
        for session in selected[:20]:
            typer.echo(
                f"- {session.project_id} {session.session_id} "
                f"{session.path.stat().st_size / 1024 / 1024:.1f}MiB {session.path}"
            )
        return

    if root.exists() and replace:
        if backup_existing:
            backup = root.parent / f"{root.name}-backup-{_timestamp_slug()}"
            shutil.move(str(root), str(backup))
            typer.echo(f"backed up existing structured root: {backup}")
        else:
            shutil.rmtree(root)
    _ensure_v2_root(root)

    manifest_path = root / "codex" / ".system" / "import-manifest.jsonl"
    quality_report = root / "codex" / ".system" / "quality-report.md"
    imported = 0
    for session in selected:
        record = _session_to_v2_record(session, user_id=user_id, agent_id=agent_id)
        path = _write_v2_record(root, app_id, record)
        _append_manifest(manifest_path, session, path, record)
        imported += 1
        typer.echo(f"imported {imported}/{len(selected)} {record['id']} -> {path}")

    for session in oversized:
        _write_oversized_stub(root, app_id, session)

    _write_quality_report(
        quality_report,
        selected=selected,
        oversized=oversized,
        skipped_low_value=skipped_low_value,
        skipped_duplicate=skipped_duplicate,
    )
    typer.echo(
        f"structured import complete: imported={imported}, "
        f"oversized={len(oversized)}"
    )


def _iter_codex_sessions(
    sessions_dir: Path,
    default_agent_id: str,
    project_override: str | None,
) -> list[CodexSession]:
    root = sessions_dir.expanduser()
    if not root.exists():
        raise typer.BadParameter(f"sessions dir does not exist: {root}")
    sessions: list[CodexSession] = []
    for path in sorted(root.rglob("*.jsonl")):
        session = _parse_codex_session(
            path,
            default_agent_id=default_agent_id,
            project_override=project_override,
        )
        if session is not None:
            sessions.append(session)
    return sessions


def _parse_codex_session(
    path: Path,
    *,
    default_agent_id: str,
    project_override: str | None,
) -> CodexSession | None:
    session_id = _session_id_from_path(path)
    project_id = project_override
    agent_id = default_agent_id
    cwd: str | None = None
    messages: list[CodexMessage] = []

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "session_meta":
                payload = event.get("payload") or {}
                if isinstance(payload, dict):
                    session_id = _safe_id(str(payload.get("id") or session_id))
                    cwd_value = payload.get("cwd")
                    if isinstance(cwd_value, str) and cwd_value.strip():
                        cwd = cwd_value.strip()
                    if project_id is None:
                        project_id = _project_id_from_cwd(payload.get("cwd"))
                    nickname = payload.get("agent_nickname")
                    if isinstance(nickname, str) and nickname.strip():
                        agent_id = _safe_id(f"codex_{nickname.strip()}")
                continue
            message = _message_from_event(event)
            if message is not None:
                messages.append(message)

    if project_id is None:
        project_id = _project_id_from_cwd(None)
    return CodexSession(
        path=path,
        session_id=_safe_id(session_id),
        project_id=_safe_id(project_id),
        agent_id=_safe_id(agent_id),
        messages=messages,
        cwd=cwd,
    )


def _parse_codex_session_meta(
    path: Path,
    *,
    default_agent_id: str,
    project_override: str | None,
) -> CodexSession:
    session_id = _session_id_from_path(path)
    project_id = project_override
    agent_id = default_agent_id
    cwd: str | None = None
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "session_meta":
                continue
            payload = event.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            session_id = _safe_id(str(payload.get("id") or session_id))
            cwd_value = payload.get("cwd")
            if isinstance(cwd_value, str) and cwd_value.strip():
                cwd = cwd_value.strip()
            if project_id is None:
                project_id = _project_id_from_cwd(cwd)
            nickname = payload.get("agent_nickname")
            if isinstance(nickname, str) and nickname.strip():
                agent_id = _safe_id(f"codex_{nickname.strip()}")
            break
    if project_id is None:
        project_id = _project_id_from_cwd(cwd)
    return CodexSession(
        path=path,
        session_id=_safe_id(session_id),
        project_id=_safe_id(project_id),
        agent_id=_safe_id(agent_id),
        messages=[],
        cwd=cwd,
    )


def _message_from_event(event: dict[str, Any]) -> CodexMessage | None:
    if event.get("type") != "response_item":
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    if payload.get("type") != "message":
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
            cleaned = text.strip()
            if cleaned and not _is_meta_text(cleaned):
                texts.append(cleaned)
    content = _clean_import_text("\n\n".join(texts))
    if not content:
        return None
    return CodexMessage(
        role=role,
        content=content,
        timestamp_ms=_timestamp_ms(event.get("timestamp")),
    )


def _is_meta_text(text: str) -> bool:
    return any(text.startswith(prefix) for prefix in _META_PREFIXES)


def _clean_import_text(text: str) -> str:
    return _OAI_MEM_CITATION_RE.sub("\n\n", text).strip()


def _has_memory_value(session: CodexSession) -> bool:
    user_messages = [m for m in session.messages if m.role == "user"]
    assistant_messages = [m for m in session.messages if m.role == "assistant"]
    if not user_messages or not assistant_messages:
        return False

    user_text = "\n".join(m.content for m in user_messages).strip()
    all_text = "\n".join(m.content for m in session.messages).strip()
    if _normalized_low_value(user_text) in _LOW_VALUE_TEXTS:
        return False
    user_title_keys = [
        _normalized_title_key(_title_candidate(m.content) or m.content)
        for m in user_messages
    ]
    if user_title_keys and all(
        key in _GENERIC_TITLE_TEXTS or key in _LOW_VALUE_TEXTS
        for key in user_title_keys
    ):
        return False
    return _semantic_char_count(all_text) >= 24


def _chunk_long_sessions(
    sessions: list[CodexSession],
    chunk_messages: int,
) -> list[CodexSession]:
    chunked: list[CodexSession] = []
    for session in sessions:
        if len(session.messages) <= chunk_messages:
            chunked.append(session)
            continue
        for index, start in enumerate(
            range(0, len(session.messages), chunk_messages),
            1,
        ):
            chunked.append(
                CodexSession(
                    path=session.path,
                    session_id=_safe_id(f"{session.session_id}.part{index:03d}"),
                    project_id=session.project_id,
                    agent_id=session.agent_id,
                    messages=session.messages[start : start + chunk_messages],
                    cwd=session.cwd,
                )
            )
    return chunked


def _ensure_v2_root(root: Path) -> None:
    (root / "codex" / ".system").mkdir(parents=True, exist_ok=True)
    (root / "codex" / "inbox" / "needs-review").mkdir(parents=True, exist_ok=True)
    (root / "codex" / "inbox" / "low-value").mkdir(parents=True, exist_ok=True)
    (root / "codex" / "inbox" / "oversized").mkdir(parents=True, exist_ok=True)
    (root / ".index").mkdir(parents=True, exist_ok=True)
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(".index/\n.tmp/\n", encoding="utf-8")
    migrations = root / "codex" / ".system" / "schema-migrations.jsonl"
    if not migrations.exists():
        migrations.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "created_at": datetime.now(UTC).isoformat(),
                    "note": "Initial structured memory layout.",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )


def _session_to_v2_record(
    session: CodexSession,
    *,
    user_id: str,
    agent_id: str,
) -> dict[str, Any]:
    user_texts = [m.content for m in session.messages if m.role == "user"]
    assistant_texts = [m.content for m in session.messages if m.role == "assistant"]
    first_user = _first_meaningful_text(user_texts)
    final_assistant = _first_meaningful_text(reversed(assistant_texts))
    combined = "\n\n".join(m.content for m in session.messages)
    session_date = _session_date(session)
    title = _title_from_text(
        _title_source_text(user_texts, assistant_texts)
        or first_user
        or final_assistant
        or session.session_id
    )
    domain = _domain_for_session(session, combined)
    artifact_type, answer_shape = _artifact_shape_for_text(combined)
    entities = _entities_from_text(combined)
    visibility = "active" if _is_high_value_text(combined) else "needs_review"
    kind = "episode" if artifact_type in {"command", "doc", "preference"} else "case"
    slug = _slugify(title)[:48]
    prefix = "ep" if kind == "episode" else "case"
    short_session_id = _short_source_id(session.session_id)
    record_id = f"{prefix}_{session_date.replace('-', '')}_{short_session_id}_{slug}"
    summary = _summary_from_text(first_user, final_assistant)
    content = _record_body(
        kind=kind,
        title=title,
        summary=summary,
        first_user=first_user,
        final_assistant=final_assistant,
        messages=session.messages,
    )
    source_hash = _file_sha256(session.path)
    return {
        "schema_version": 2,
        "type": kind,
        "id": _safe_id(record_id),
        "project": session.project_id,
        "domain": domain,
        "artifact_type": artifact_type,
        "answer_shape": answer_shape,
        "title": title,
        "summary": summary,
        "entities": entities,
        "confidence": "medium",
        "visibility": visibility,
        "dedupe_key": _dedupe_key(session.project_id, domain, title, entities),
        "source": {
            "agent": "codex",
            "user_id": user_id,
            "agent_id": agent_id,
            "session_id": session.session_id,
            "session_date": session_date,
            "imported_at": datetime.now(UTC).isoformat(),
            "cwd": session.cwd or "",
            "path": str(session.path),
            "bytes": session.path.stat().st_size,
        },
        "source_hash": source_hash,
        "body": content,
    }


def _write_v2_record(root: Path, app_id: str, record: dict[str, Any]) -> Path:
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    session_date = str(source.get("session_date") or "1970-01-01")
    year, month, *_ = session_date.split("-")
    project = _safe_id(str(record["project"]))
    kind_dir = "episodes" if record["type"] == "episode" else "cases"
    path = (
        root
        / app_id
        / "projects"
        / project
        / kind_dir
        / year
        / month
        / _v2_filename(record)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {k: v for k, v in record.items() if k != "body"}
    body = str(record["body"]).rstrip() + "\n"
    path.write_text(_frontmatter(meta) + body, encoding="utf-8")
    _ensure_project_profile(root, app_id, project)
    return path


def _ensure_project_profile(root: Path, app_id: str, project: str) -> None:
    profile = root / app_id / "projects" / project / "PROJECT.md"
    if profile.exists():
        return
    meta = {
        "schema_version": 2,
        "type": "profile",
        "id": f"profile_{project}",
        "project": project,
        "visibility": "active",
        "updated_at": datetime.now(UTC).isoformat(),
    }
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(
        _frontmatter(meta)
        + f"# {project} 项目索引\n\n"
        + "这个文件只记录项目级长期边界和别名，不承载具体问题答案。\n\n"
        + "## 长期约定\n\n"
        + "- 这里记录长期稳定的项目约定、用户偏好和环境边界。\n",
        encoding="utf-8",
    )


def _append_manifest(
    manifest_path: Path,
    session: CodexSession,
    output_path: Path,
    record: dict[str, Any],
) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "schema_version": 2,
        "session_id": session.session_id,
        "session_path": str(session.path),
        "source_hash": record.get("source_hash"),
        "output_path": str(output_path),
        "record_id": record.get("id"),
        "project": record.get("project"),
        "type": record.get("type"),
        "imported_at": datetime.now(UTC).isoformat(),
    }
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _existing_v2_session_keys(root: Path, app_id: str) -> set[tuple[str, str]]:
    manifest_path = root / app_id / ".system" / "import-manifest.jsonl"
    if not manifest_path.exists():
        return set()
    keys: set[tuple[str, str]] = set()
    with manifest_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            session_id = entry.get("session_id") if isinstance(entry, dict) else None
            project = entry.get("project") if isinstance(entry, dict) else None
            if isinstance(session_id, str) and isinstance(project, str):
                keys.add((_safe_id(project), _safe_id(session_id)))
    return keys


def _write_oversized_stub(root: Path, app_id: str, session: CodexSession) -> None:
    session_date = _session_date(session)
    path = (
        root
        / app_id
        / "inbox"
        / "oversized"
        / f"{session_date}-{session.session_id}.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "schema_version": 2,
        "type": "oversized_session",
        "id": f"oversized_{session.session_id}",
        "project": session.project_id,
        "visibility": "needs_review",
        "source": {
            "session_id": session.session_id,
            "session_date": session_date,
            "path": str(session.path),
            "bytes": session.path.stat().st_size,
            "cwd": session.cwd or "",
        },
        "source_hash": _file_sha256(session.path),
    }
    path.write_text(
        _frontmatter(meta)
        + "# Oversized Session\n\n"
        + "这个 session 体积过大，已暂缓正式提炼。需要先做裁剪审计。\n",
        encoding="utf-8",
    )


def _write_quality_report(
    path: Path,
    *,
    selected: list[CodexSession],
    oversized: list[CodexSession],
    skipped_low_value: int,
    skipped_duplicate: int,
) -> None:
    by_project: dict[str, tuple[int, int]] = {}
    for session in selected:
        count, size = by_project.get(session.project_id, (0, 0))
        by_project[session.project_id] = (count + 1, size + session.path.stat().st_size)
    lines = [
        "# EverOS 结构化导入质量报告",
        "",
        f"- imported_sessions: {len(selected)}",
        f"- oversized_sessions: {len(oversized)}",
        f"- skipped_low_value: {skipped_low_value}",
        f"- skipped_duplicate: {skipped_duplicate}",
        "",
        "## Project 分布",
        "",
    ]
    for project, (count, size) in sorted(by_project.items()):
        lines.append(f"- {project}: {count} sessions, {size / 1024 / 1024:.1f} MiB")
    lines.extend(["", "## Oversized", ""])
    for session in oversized[:50]:
        lines.append(
            f"- {session.project_id} {session.path.stat().st_size / 1024 / 1024:.1f} "
            f"MiB `{session.path}`"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _frontmatter(meta: dict[str, Any]) -> str:
    return "---\n" + yaml.safe_dump(
        meta,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ) + "---\n"


def _session_date(session: CodexSession) -> str:
    timestamps = [msg.timestamp_ms for msg in session.messages if msg.timestamp_ms]
    if timestamps:
        return datetime.fromtimestamp(min(timestamps) / 1000, UTC).date().isoformat()
    return datetime.fromtimestamp(session.path.stat().st_mtime, UTC).date().isoformat()


def _first_meaningful_text(texts) -> str:
    for text in texts:
        cleaned = str(text).strip()
        if cleaned and not _is_meta_text(cleaned):
            return cleaned
    return ""


def _title_source_text(user_texts: list[str], assistant_texts: list[str]) -> str:
    """Pick a readable title source without letting generic prompts dominate."""
    for text in user_texts:
        if _usable_title_text(text):
            return text
    for text in reversed(assistant_texts):
        if _usable_title_text(text):
            return text
    return ""


def _usable_title_text(text: str) -> bool:
    candidate = _title_candidate(text)
    if not candidate:
        return False
    if _normalized_title_key(candidate) in _GENERIC_TITLE_TEXTS:
        return False
    return _semantic_char_count(candidate) >= 8


def _title_from_text(text: str) -> str:
    first = _title_candidate(text)
    if len(first) > 46:
        first = first[:46].rstrip() + "..."
    return first or "未命名记忆"


def _title_candidate(text: str) -> str:
    for line in text.strip().splitlines():
        candidate = " ".join(line.strip().split())
        if not candidate:
            continue
        if candidate.startswith(("```", "---", "<")):
            continue
        candidate = re.sub(r"^#+\s*", "", candidate)
        candidate = re.sub(r"^[-*]\s*", "", candidate)
        candidate = candidate.strip("：:,.，。 ")
        if re.match(r"^(name|path|description):\s*", candidate, re.I):
            continue
        if candidate:
            return candidate
    return ""


def _summary_from_text(first_user: str, final_assistant: str) -> str:
    if final_assistant:
        text = " ".join(final_assistant.split())
        return text[:180].rstrip()
    text = " ".join(first_user.split())
    return text[:180].rstrip()


def _record_body(
    *,
    kind: str,
    title: str,
    summary: str,
    first_user: str,
    final_assistant: str,
    messages: list[CodexMessage],
) -> str:
    body = [f"# {title}", "", "## 摘要", "", summary or "暂无摘要。", ""]
    if first_user:
        body.extend(["## 用户问题", "", first_user.strip(), ""])
    if final_assistant:
        heading = "## 结论" if kind == "episode" else "## 处理结果"
        body.extend([heading, "", final_assistant.strip(), ""])
    turns = [
        f"- {msg.role}: {' '.join(msg.content.split())[:180]}"
        for msg in messages[:12]
        if msg.content.strip()
    ]
    if turns:
        body.extend(["## 关键轮次", "", *turns, ""])
    return "\n".join(body)


def _domain_for_session(session: CodexSession, text: str) -> str:
    cwd = session.cwd or ""
    lower = f"{cwd}\n{text}".lower()
    if "upgrade.json" in lower or "升级" in text:
        return "upgrade"
    if "git svn" in lower or "dcommit" in lower:
        return "git_svn"
    if "aidp" in lower or "标注" in text:
        return "annotation"
    if "api" in lower or "openapi" in lower:
        return "api"
    if "db" in lower or "数据库" in text:
        return "db"
    return "general"


def _artifact_shape_for_text(text: str) -> tuple[str, str]:
    lower = text.lower()
    if any(word in text for word in ("命令", "登录", "连接")) or "mysql -" in lower:
        return "command", "shell_command"
    if "sql" in lower or "select " in lower or "update " in lower:
        return "command", "sql"
    if "怎么" in text or "流程" in text or "步骤" in text:
        return "procedure", "checklist"
    if "为什么" in text or "原因" in text or "排查" in text:
        return "bug_analysis", "explanation"
    if "偏好" in text or "规则" in text:
        return "preference", "explanation"
    if "修改" in text or "提交" in text or "diff" in lower:
        return "code_change", "diff_summary"
    return "note", "explanation"


_ENTITY_RE = re.compile(
    r"\b[A-Za-z][A-Za-z0-9_./:-]{2,}\b|[\u4e00-\u9fff]{2,12}"
)


def _entities_from_text(text: str) -> list[str]:
    entities: list[str] = []
    for match in _ENTITY_RE.finditer(text):
        value = match.group(0).strip(".,;:，。；：")
        if value in entities or len(value) > 48:
            continue
        if value.lower() in {"http", "https", "json", "true", "false"}:
            continue
        entities.append(value)
        if len(entities) >= 16:
            break
    return entities


def _is_high_value_text(text: str) -> bool:
    signals = ("命令", "修复", "原因", "流程", "提交", "验证", "路径", "SQL", "mysql")
    return any(signal in text for signal in signals)


def _dedupe_key(project: str, domain: str, title: str, entities: list[str]) -> str:
    parts = [project, domain, *entities[:4], title[:24]]
    return _safe_id(".".join(_slugify(part) for part in parts if part))


def _v2_filename(record: dict[str, Any]) -> str:
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    session_date = str(source.get("session_date") or "1970-01-01")
    session_id = str(source.get("session_id") or record.get("source_hash") or "")
    title_slug = _slugify(str(record.get("title") or "memory"))[:72]
    return f"{session_date}-{title_slug}-{_short_source_id(session_id)}.md"


def _short_source_id(value: str) -> str:
    matched = _UUID_RE.search(value)
    raw = matched.group(1) if matched else value
    cleaned = _safe_id(raw.replace("-", ""))
    if len(cleaned) > 12:
        return f"{cleaned[:8]}{cleaned[-4:]}"
    return cleaned or "source"


def _slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[\s/]+", "-", value)
    value = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "", value)
    value = re.sub(r"-+", "-", value).strip("-_")
    return value or "memory"


def _file_sha256(path: Path) -> str:
    h = __import__("hashlib").sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _normalized_low_value(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _normalized_title_key(text: str) -> str:
    normalized = _normalized_low_value(text)
    return normalized.strip("$／/，。！？!?：: ")


def _semantic_char_count(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


def _session_id_from_path(path: Path) -> str:
    match = _UUID_RE.search(path.stem)
    if match:
        return match.group(1)
    return path.stem


def _project_id_from_cwd(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        normalized = value.strip()
        for pattern, project_id in _CODEX_PROJECT_RULES:
            if pattern.search(normalized):
                return project_id
        leaf = Path(value).expanduser().name
        if leaf:
            return _safe_id(leaf)
    return _DEFAULT_PROJECT_ID


def _safe_id(value: str) -> str:
    cleaned = _SAFE_ID_RE.sub("_", value.strip())
    cleaned = cleaned.strip("._-")
    return cleaned[:128] or "default"


def _timestamp_ms(value: Any) -> int:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return int(parsed.timestamp() * 1000)
        except ValueError:
            pass
    return int(datetime.now(UTC).timestamp() * 1000)


def _existing_memory_session_ids(app_id: str) -> set[str]:
    root = MemoryRoot.default().root / app_id
    if not root.exists():
        return set()
    session_ids: set[str] = set()
    ledger_path = _import_ledger_path(app_id)
    if ledger_path.exists():
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            session_id = entry.get("session_id") if isinstance(entry, dict) else None
            if isinstance(session_id, str) and session_id.strip():
                session_ids.add(_safe_id(session_id))
    patterns = (
        re.compile(r"^session_id:\s*['\"]?([^'\"\n]+)['\"]?\s*$"),
        re.compile(r"^\*\*session_id\*\*:\s*`?([^`\n]+)`?\s*$"),
    )
    for path in root.rglob("*.md"):
        if ".index" in path.parts or ".tmp" in path.parts:
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                for pattern in patterns:
                    matched = pattern.match(stripped)
                    if matched:
                        session_ids.add(_safe_id(matched.group(1)))
                        break
        except UnicodeDecodeError:
            continue
    return session_ids


def _record_imported_session_id(app_id: str, session_id: str) -> None:
    ledger_path = _import_ledger_path(app_id)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "session_id": _safe_id(session_id),
        "imported_at": datetime.now(UTC).isoformat(),
    }
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _import_ledger_path(app_id: str) -> Path:
    return MemoryRoot.default().root / app_id / ".imported_sessions.jsonl"
