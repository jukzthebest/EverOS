"""V2 Codex memory markdown schemas.

V2 files are one-topic-per-file and live under:

``<app>/projects/<project>/<kind>/<YYYY>/<MM>/<slug>.md``

They are intended to be readable in Obsidian while still feeding the existing
LanceDB tables through dedicated cascade handlers.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from everos.core.persistence.markdown import BaseFrontmatter


class V2MemoryPathMixin:
    """Path strategy for v2 one-topic memory documents."""

    KIND_DIR: ClassVar[str]

    @classmethod
    def path_glob(cls) -> str:
        return f"*/projects/*/{cls.KIND_DIR}/*/*/*.md"


class V2EpisodeFrontmatter(V2MemoryPathMixin, BaseFrontmatter):
    """Frontmatter for v2 episode documents."""

    KIND_DIR: ClassVar[str] = "episodes"

    schema_version: Literal[2] = 2
    type: Literal["episode"] = "episode"
    project: str
    domain: str = "general"
    artifact_type: str = "note"
    answer_shape: str = "explanation"
    title: str
    summary: str = ""
    entities: list[str] = []
    confidence: str = "medium"
    visibility: str = "active"
    dedupe_key: str
    source: dict[str, object] = {}
    source_hash: str = ""


class V2CaseFrontmatter(V2MemoryPathMixin, BaseFrontmatter):
    """Frontmatter for v2 case documents."""

    KIND_DIR: ClassVar[str] = "cases"

    schema_version: Literal[2] = 2
    type: Literal["case"] = "case"
    project: str
    domain: str = "general"
    artifact_type: str = "procedure"
    answer_shape: str = "checklist"
    title: str
    summary: str = ""
    entities: list[str] = []
    confidence: str = "medium"
    reusability: str = "medium"
    visibility: str = "active"
    dedupe_key: str
    source: dict[str, object] = {}
    source_hash: str = ""


class V2PlaybookFrontmatter(V2MemoryPathMixin, BaseFrontmatter):
    """Frontmatter for v2 playbook documents."""

    KIND_DIR: ClassVar[str] = "playbooks"

    schema_version: Literal[2] = 2
    type: Literal["playbook"] = "playbook"
    project: str
    domain: str = "general"
    artifact_type: str = "procedure"
    answer_shape: str = "checklist"
    title: str
    summary: str = ""
    entities: list[str] = []
    confidence: str = "medium"
    maturity: str = "draft"
    visibility: str = "active"
    derived_from: list[str] = []
    dedupe_key: str
    source_hash: str = ""
