"""Import external conversation archives into EverOS."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from everos.core.persistence import MemoryRoot

app = typer.Typer(
    name="import",
    help="Import local agent/session archives into EverOS",
    no_args_is_help=True,
)

_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_.@+-]+")
_UUID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
_META_PREFIXES = (
    "# AGENTS.md instructions",
    "<environment_context>",
    "<codex_internal_context",
    "<turn_aborted>",
)
_LOW_VALUE_TEXTS = frozenset(
    {
        "ok",
        "okay",
        "好的",
        "好",
        "继续",
        "继续吧",
        "go on",
    }
)
_DEFAULT_CODEX_SESSIONS_DIR = Path("~/.codex/sessions")
_CODEX_PROJECT_RULES: tuple[tuple[str, str], ...] = (
    ("/Yundun/", "Yundun"),
    ("/CSPM/", "CSPM"),
    ("/aidp/", "AIDP"),
    ("/agent-wiki/", "AgentWiki"),
    ("/project-wiki/", "AgentWiki"),
    ("/Documents/daily/", "Daily"),
    ("/Obsidian/", "Content"),
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
    ] = "lengxiaochu",
    agent_id: Annotated[
        str,
        typer.Option("--agent-id", help="EverOS sender_id for assistant messages."),
    ] = "codex",
    app_id: Annotated[
        str,
        typer.Option("--app-id", help="EverOS app_id scope."),
    ] = "codex",
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
    messages: list[CodexMessage] = []

    for line in path.read_text(encoding="utf-8").splitlines():
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
    content = "\n\n".join(texts).strip()
    if not content:
        return None
    return CodexMessage(
        role=role,
        content=content,
        timestamp_ms=_timestamp_ms(event.get("timestamp")),
    )


def _is_meta_text(text: str) -> bool:
    return any(text.startswith(prefix) for prefix in _META_PREFIXES)


def _has_memory_value(session: CodexSession) -> bool:
    user_messages = [m for m in session.messages if m.role == "user"]
    assistant_messages = [m for m in session.messages if m.role == "assistant"]
    if not user_messages or not assistant_messages:
        return False

    user_text = "\n".join(m.content for m in user_messages).strip()
    all_text = "\n".join(m.content for m in session.messages).strip()
    if _normalized_low_value(user_text) in _LOW_VALUE_TEXTS:
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
                )
            )
    return chunked


def _normalized_low_value(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _semantic_char_count(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


def _session_id_from_path(path: Path) -> str:
    match = _UUID_RE.search(path.stem)
    if match:
        return match.group(1)
    return path.stem


def _project_id_from_cwd(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        normalized = f"/{value.strip().strip('/')}/"
        for marker, project_id in _CODEX_PROJECT_RULES:
            if marker in normalized:
                return project_id
    return "Misc"


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
