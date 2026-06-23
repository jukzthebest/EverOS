"""Cascade handlers for v2 one-topic memory markdown files."""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import PurePosixPath
from typing import Any, ClassVar

from everos.component.utils.datetime import from_iso_format
from everos.core.persistence import MarkdownReader
from everos.infra.persistence.lancedb import (
    AgentCase,
    AgentSkill,
    Episode,
    ParentType,
    agent_case_repo,
    agent_skill_repo,
    episode_repo,
)

from ..types import HandlerOutcome
from .base import Handler


class V2EpisodeHandler(Handler):
    """Index v2 ``episodes`` markdown into the existing episode table."""

    kind = "v2_episode"
    lance_repo: ClassVar[Any] = episode_repo

    async def handle_added_or_modified(self, md_path: str) -> HandlerOutcome:
        absolute = self._deps.memory_root.root / md_path
        parsed = await MarkdownReader.read(absolute)
        fm = parsed.frontmatter
        digest = _digest(fm, parsed.body)
        row_id = str(fm.get("id") or _id_from_path(md_path))
        prior = await episode_repo.get_by_id(row_id)
        if prior is not None and prior.content_sha256 == digest:
            return HandlerOutcome(
                md_path=md_path,
                kind=self.kind,
                upserted=0,
                deleted=0,
                skipped=1,
            )

        body = parsed.body.strip()
        summary = str(fm.get("summary") or "").strip() or None
        tokens = " ".join(self._deps.tokenizer.tokenize(_index_text(fm, body)))
        vector = await self._deps.embedder.embed(_index_text(fm, body))
        row = Episode(
            id=row_id,
            entry_id=row_id,
            owner_id=_source_string(fm, "user_id", "lengxiaochu"),
            owner_type="user",
            app_id=_app_id(md_path),
            project_id=str(fm.get("project") or _project_from_path(md_path)),
            session_id=_source_string(fm, "session_id", ""),
            timestamp=_source_timestamp(fm),
            parent_type=ParentType.MEMCELL.value,
            parent_id=_source_string(fm, "parent_id", ""),
            sender_ids=["lengxiaochu", "codex"],
            subject=str(fm.get("title") or row_id),
            summary=summary,
            episode=body,
            episode_tokens=tokens,
            md_path=md_path,
            content_sha256=digest,
            vector=vector,
        )
        await episode_repo.delete_by_md_path(md_path)
        await episode_repo.upsert([row])
        return HandlerOutcome(
            md_path=md_path,
            kind=self.kind,
            upserted=1,
            deleted=0,
            skipped=0,
        )

    async def handle_deleted(self, md_path: str) -> HandlerOutcome:
        deleted = await episode_repo.delete_by_md_path(md_path)
        return HandlerOutcome(
            md_path=md_path,
            kind=self.kind,
            upserted=0,
            deleted=deleted,
            skipped=0,
        )


class V2CaseHandler(Handler):
    """Index v2 ``cases`` markdown into the existing agent_case table."""

    kind = "v2_case"
    lance_repo: ClassVar[Any] = agent_case_repo

    async def handle_added_or_modified(self, md_path: str) -> HandlerOutcome:
        absolute = self._deps.memory_root.root / md_path
        parsed = await MarkdownReader.read(absolute)
        fm = parsed.frontmatter
        digest = _digest(fm, parsed.body)
        row_id = str(fm.get("id") or _id_from_path(md_path))
        prior = await agent_case_repo.get_by_id(row_id)
        if prior is not None and prior.content_sha256 == digest:
            return HandlerOutcome(
                md_path=md_path,
                kind=self.kind,
                upserted=0,
                deleted=0,
                skipped=1,
            )

        body = parsed.body.strip()
        title = str(fm.get("title") or row_id)
        summary = str(fm.get("summary") or "").strip()
        approach = body
        intent_tokens = " ".join(self._deps.tokenizer.tokenize(title))
        approach_tokens = " ".join(
            self._deps.tokenizer.tokenize(_index_text(fm, approach))
        )
        vector = await self._deps.embedder.embed(_index_text(fm, title))
        row = AgentCase(
            id=row_id,
            entry_id=row_id,
            owner_id=_source_string(fm, "agent_id", "codex"),
            owner_type="agent",
            app_id=_app_id(md_path),
            project_id=str(fm.get("project") or _project_from_path(md_path)),
            session_id=_source_string(fm, "session_id", ""),
            timestamp=_source_timestamp(fm),
            parent_type=ParentType.MEMCELL.value,
            parent_id=_source_string(fm, "parent_id", ""),
            quality_score=_confidence_score(str(fm.get("confidence") or "medium")),
            task_intent=title,
            task_intent_tokens=intent_tokens,
            approach=approach,
            approach_tokens=approach_tokens,
            key_insight=summary or None,
            md_path=md_path,
            content_sha256=digest,
            vector=vector,
        )
        await agent_case_repo.delete_by_md_path(md_path)
        await agent_case_repo.upsert([row])
        return HandlerOutcome(
            md_path=md_path,
            kind=self.kind,
            upserted=1,
            deleted=0,
            skipped=0,
        )

    async def handle_deleted(self, md_path: str) -> HandlerOutcome:
        deleted = await agent_case_repo.delete_by_md_path(md_path)
        return HandlerOutcome(
            md_path=md_path,
            kind=self.kind,
            upserted=0,
            deleted=deleted,
            skipped=0,
        )


class V2PlaybookHandler(Handler):
    """Index v2 ``playbooks`` markdown into the existing agent_skill table."""

    kind = "v2_playbook"
    lance_repo: ClassVar[Any] = agent_skill_repo

    async def handle_added_or_modified(self, md_path: str) -> HandlerOutcome:
        absolute = self._deps.memory_root.root / md_path
        parsed = await MarkdownReader.read(absolute)
        fm = parsed.frontmatter
        digest = _digest(fm, parsed.body)
        name = str(fm.get("id") or _id_from_path(md_path))
        row_id = f"codex_{name}"
        prior = await agent_skill_repo.get_by_id(row_id)
        if prior is not None and prior.content_sha256 == digest:
            return HandlerOutcome(
                md_path=md_path,
                kind=self.kind,
                upserted=0,
                deleted=0,
                skipped=1,
            )

        title = str(fm.get("title") or name)
        description = str(fm.get("summary") or title)
        content = parsed.body.strip()
        description_tokens = " ".join(self._deps.tokenizer.tokenize(description))
        content_tokens = " ".join(
            self._deps.tokenizer.tokenize(_index_text(fm, content))
        )
        vector = await self._deps.embedder.embed(_index_text(fm, description))
        row = AgentSkill(
            id=row_id,
            owner_id="codex",
            owner_type="agent",
            app_id=_app_id(md_path),
            project_id=str(fm.get("project") or _project_from_path(md_path)),
            name=name,
            description=description,
            description_tokens=description_tokens,
            content=content,
            content_tokens=content_tokens,
            confidence=_confidence_score(str(fm.get("confidence") or "medium")),
            maturity_score=1.0 if fm.get("maturity") == "stable" else 0.5,
            source_case_ids=[str(v) for v in fm.get("derived_from") or []],
            cluster_id=None,
            md_path=md_path,
            content_sha256=digest,
            vector=vector,
        )
        await agent_skill_repo.delete_by_md_path(md_path)
        await agent_skill_repo.upsert([row])
        return HandlerOutcome(
            md_path=md_path,
            kind=self.kind,
            upserted=1,
            deleted=0,
            skipped=0,
        )

    async def handle_deleted(self, md_path: str) -> HandlerOutcome:
        deleted = await agent_skill_repo.delete_by_md_path(md_path)
        return HandlerOutcome(
            md_path=md_path,
            kind=self.kind,
            upserted=0,
            deleted=deleted,
            skipped=0,
        )


def _digest(frontmatter: dict[str, Any], body: str) -> str:
    payload = repr(sorted(frontmatter.items())) + "\n" + body.rstrip()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _index_text(frontmatter: dict[str, Any], body: str) -> str:
    parts = [
        str(frontmatter.get("title") or ""),
        str(frontmatter.get("summary") or ""),
        " ".join(str(v) for v in frontmatter.get("entities") or []),
        str(frontmatter.get("domain") or ""),
        str(frontmatter.get("artifact_type") or ""),
        str(frontmatter.get("answer_shape") or ""),
        body,
    ]
    return "\n".join(part for part in parts if part)


def _source_string(frontmatter: dict[str, Any], key: str, default: str) -> str:
    source = frontmatter.get("source")
    if isinstance(source, dict):
        value = source.get(key)
        if isinstance(value, str) and value:
            return value
    return default


def _source_timestamp(frontmatter: dict[str, Any]) -> dt.datetime:
    source = frontmatter.get("source")
    raw = None
    if isinstance(source, dict):
        raw = source.get("session_date") or source.get("timestamp")
    if isinstance(raw, str) and raw:
        try:
            if len(raw) == 10:
                return dt.datetime.fromisoformat(raw).replace(tzinfo=dt.UTC)
            return from_iso_format(raw)
        except ValueError:
            pass
    return dt.datetime.now(dt.UTC)


def _app_id(md_path: str) -> str:
    parts = PurePosixPath(md_path).parts
    return parts[0] if parts else "codex"


def _project_from_path(md_path: str) -> str:
    parts = PurePosixPath(md_path).parts
    try:
        return parts[parts.index("projects") + 1]
    except (ValueError, IndexError):
        return "default"


def _id_from_path(md_path: str) -> str:
    return PurePosixPath(md_path).stem.replace("-", "_")


def _confidence_score(label: str) -> float:
    if label == "high":
        return 0.9
    if label == "low":
        return 0.3
    return 0.6
