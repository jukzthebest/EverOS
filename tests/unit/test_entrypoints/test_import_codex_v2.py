"""Unit coverage for the Codex v2 markdown importer."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import everos.entrypoints.cli.commands.import_cmd as import_cmd
from everos.entrypoints.cli.commands.import_cmd import (
    CodexMessage,
    CodexSession,
    _parse_codex_project_rules,
    _project_id_from_cwd,
    _session_to_v2_record,
    _write_v2_record,
)
from everos.memory.cascade.registry import match_kind


def test_v2_markdown_paths_route_to_dedicated_handlers() -> None:
    assert (
        match_kind("codex/projects/ExampleProject/episodes/2026/06/foo.md").name
        == "v2_episode"
    )
    assert (
        match_kind("codex/projects/ExampleProject/cases/2026/06/foo.md").name
        == "v2_case"
    )
    assert (
        match_kind("codex/projects/ExampleProject/playbooks/2026/06/foo.md").name
        == "v2_playbook"
    )
    assert match_kind("codex/projects/ExampleProject/PROJECT.md") is None


def test_v2_importer_uses_readable_unique_filenames(tmp_path: Path) -> None:
    ts = int(dt.datetime(2026, 6, 16, tzinfo=dt.UTC).timestamp() * 1000)
    src1 = tmp_path / "rollout-1.jsonl"
    src2 = tmp_path / "rollout-2.jsonl"
    src1.write_text('{"type":"session_meta"}\n', encoding="utf-8")
    src2.write_text('{"type":"session_meta"}\n', encoding="utf-8")

    def build(session_id: str, src: Path) -> CodexSession:
        return CodexSession(
            path=src,
            session_id=session_id,
            project_id="ExampleProject",
            agent_id="codex",
            cwd="/tmp/work/example-project",
            messages=[
                CodexMessage(role="user", content="commit", timestamp_ms=ts),
                CodexMessage(
                    role="assistant",
                    content="已提交 database connection command 文档更新。",
                    timestamp_ms=ts + 1,
                ),
            ],
        )

    record1 = _session_to_v2_record(
        build("019ecf54-dde8-7293-8d1e-db2aae686fbc", src1),
        user_id="user",
        agent_id="codex",
    )
    record2 = _session_to_v2_record(
        build("019ecf54-dde8-7293-8d1e-db2aae686fbd", src2),
        user_id="user",
        agent_id="codex",
    )

    assert record1["title"] == "已提交 database connection command 文档更新"
    path1 = _write_v2_record(tmp_path / "memory", "codex", record1)
    path2 = _write_v2_record(tmp_path / "memory", "codex", record2)

    assert path1 != path2
    assert path1.name.startswith("2026-06-16-已提交-database")
    assert path2.name.startswith("2026-06-16-已提交-database")
    project_index = tmp_path / "memory" / "codex" / "projects" / "ExampleProject"
    assert (project_index / "PROJECT.md").exists()


def test_project_id_from_cwd_uses_configurable_regex_rules() -> None:
    original_rules = import_cmd._CODEX_PROJECT_RULES
    try:
        import_cmd._CODEX_PROJECT_RULES = _parse_codex_project_rules(
            "example-[0-9]+=ExampleProject,[=ignored,broken(=ignored"
        )
        assert _project_id_from_cwd("/tmp/work/example-42") == "ExampleProject"
        assert _project_id_from_cwd("/tmp/work/unknown repo") == "unknown_repo"
    finally:
        import_cmd._CODEX_PROJECT_RULES = original_rules
