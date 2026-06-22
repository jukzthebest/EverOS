"""Local dashboard helper APIs.

These endpoints are intentionally loopback-oriented operations for the local
operator UI. They expose memory-root inspection, safe Markdown reads/writes,
and cascade maintenance without widening the public memory API contract.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from everos.config import load_settings
from everos.core.persistence import MemoryRoot
from everos.entrypoints.cli.commands.cascade import _build_orchestrator
from everos.infra.persistence.sqlite import md_change_state_repo

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])

_EDITABLE_SUFFIXES = frozenset({".md", ".markdown", ".toml", ".txt"})
_SKIP_DIRS = frozenset({".git", ".tmp", "__pycache__"})


class DashboardHealth(BaseModel):
    memory_root: str
    memory_root_exists: bool
    sqlite_exists: bool
    lancedb_exists: bool
    markdown_files: int
    app_project_count: int
    queue: dict[str, int | None]
    provider_chain: list[str]
    model: str
    grok_model: str | None
    codex_model: str | None
    openai_model: str | None
    embedding_provider: str
    embedding_model: str | None


class TreeNode(BaseModel):
    name: str
    path: str
    type: Literal["file", "dir"]
    size: int | None = None
    modified_at: float | None = None
    children: list[TreeNode] | None = None


class FileResponse(BaseModel):
    path: str
    content: str
    modified_at: float
    size: int
    editable: bool


class FileWriteRequest(BaseModel):
    path: str = Field(min_length=1)
    content: str


class CascadeActionResponse(BaseModel):
    status: str
    processed: int | None = None
    message: str


@router.get("/health", response_model=DashboardHealth)
async def health() -> DashboardHealth:
    root = MemoryRoot.default()
    settings = load_settings()
    queue = await _queue_summary()
    md_count, scope_count = await asyncio.to_thread(_count_memory_files, root.root)
    chain = settings.llm.provider_chain or [settings.llm.provider]
    return DashboardHealth(
        memory_root=str(root.root),
        memory_root_exists=root.root.exists(),
        sqlite_exists=root.system_db.exists(),
        lancedb_exists=root.lancedb_dir.exists(),
        markdown_files=md_count,
        app_project_count=scope_count,
        queue=queue,
        provider_chain=list(chain),
        model=settings.llm.model,
        grok_model=settings.llm.grok_model,
        codex_model=settings.llm.codex_model,
        openai_model=settings.llm.openai_model,
        embedding_provider=settings.embedding.provider,
        embedding_model=settings.embedding.model,
    )


@router.get("/tree", response_model=TreeNode)
async def tree(max_depth: int = 4, include_hidden: bool = True) -> TreeNode:
    root = MemoryRoot.default()
    root.ensure()
    return await asyncio.to_thread(
        _build_tree,
        root.root,
        root.root,
        max(max_depth, 0),
        include_hidden,
    )


@router.get("/file", response_model=FileResponse)
async def read_file(path: str) -> FileResponse:
    absolute = _resolve_memory_path(path)
    if not absolute.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    if absolute.suffix.lower() not in _EDITABLE_SUFFIXES:
        raise HTTPException(status_code=415, detail="unsupported file type")
    try:
        content = await asyncio.to_thread(absolute.read_text, encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=415, detail="file is not UTF-8 text") from exc
    stat = absolute.stat()
    return FileResponse(
        path=_relative_memory_path(absolute),
        content=content,
        modified_at=stat.st_mtime,
        size=stat.st_size,
        editable=_is_editable(absolute),
    )


@router.put("/file", response_model=FileResponse)
async def write_file(req: FileWriteRequest) -> FileResponse:
    absolute = _resolve_memory_path(req.path)
    if not _is_editable(absolute):
        raise HTTPException(status_code=403, detail="path is not editable")
    absolute.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(absolute.write_text, req.content, encoding="utf-8")
    return await read_file(_relative_memory_path(absolute))


@router.post("/cascade/sync", response_model=CascadeActionResponse)
async def cascade_sync() -> CascadeActionResponse:
    try:
        processed = await _build_orchestrator().sync_once()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return CascadeActionResponse(
        status="ok",
        processed=processed,
        message=f"sync complete; processed {processed} row(s)",
    )


@router.post("/cascade/fix", response_model=CascadeActionResponse)
async def cascade_fix() -> CascadeActionResponse:
    try:
        moved = await md_change_state_repo.reset_retryable_to_pending()
        processed = await _build_orchestrator().drain_once()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return CascadeActionResponse(
        status="ok",
        processed=processed,
        message=f"re-enqueued {moved} retryable row(s); processed {processed}",
    )


async def _queue_summary() -> dict[str, int | None]:
    try:
        summary = await md_change_state_repo.queue_summary()
    except Exception:
        return {
            "pending": None,
            "done": None,
            "failed_retryable": None,
            "failed_permanent": None,
            "lag": None,
        }
    lag = None
    if summary.max_lsn is not None and summary.last_processed_lsn is not None:
        lag = summary.max_lsn - summary.last_processed_lsn
    return {
        "pending": summary.pending,
        "done": summary.done,
        "failed_retryable": summary.failed_retryable,
        "failed_permanent": summary.failed_permanent,
        "lag": lag,
    }


def _count_memory_files(root: Path) -> tuple[int, int]:
    if not root.exists():
        return 0, 0
    md_count = 0
    scopes: set[tuple[str, str]] = set()
    for path in root.rglob("*.md"):
        if ".index" in path.parts or ".tmp" in path.parts:
            continue
        md_count += 1
        rel = path.relative_to(root)
        if len(rel.parts) >= 2:
            scopes.add((rel.parts[0], rel.parts[1]))
    return md_count, len(scopes)


def _build_tree(
    root: Path,
    current: Path,
    max_depth: int,
    include_hidden: bool,
) -> TreeNode:
    stat = current.stat()
    rel = "." if current == root else current.relative_to(root).as_posix()
    if current.is_file() or max_depth == 0:
        return TreeNode(
            name=current.name or root.name,
            path=rel,
            type="file" if current.is_file() else "dir",
            size=stat.st_size if current.is_file() else None,
            modified_at=stat.st_mtime,
        )

    children: list[TreeNode] = []
    for child in sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if child.name in _SKIP_DIRS or child.name == ".index":
            continue
        if not include_hidden and child.name.startswith("."):
            continue
        if child.is_dir() or child.suffix.lower() in _EDITABLE_SUFFIXES:
            children.append(_build_tree(root, child, max_depth - 1, include_hidden))
    return TreeNode(
        name=current.name or root.name,
        path=rel,
        type="dir",
        modified_at=stat.st_mtime,
        children=children,
    )


def _resolve_memory_path(path: str) -> Path:
    root = MemoryRoot.default().root
    candidate = root if path in ("", ".") else root / path
    try:
        resolved = candidate.expanduser().resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="path escapes memory root") from exc
    return resolved


def _relative_memory_path(path: Path) -> str:
    return path.relative_to(MemoryRoot.default().root).as_posix()


def _is_editable(path: Path) -> bool:
    root = MemoryRoot.default().root
    try:
        rel = path.resolve().relative_to(root)
    except (OSError, ValueError):
        return False
    if ".index" in rel.parts or ".tmp" in rel.parts:
        return False
    if path.suffix.lower() not in _EDITABLE_SUFFIXES:
        return False
    derived_dirs = {".atomic_facts", ".foresights", ".cases"}
    return not any(part in derived_dirs for part in rel.parts)
