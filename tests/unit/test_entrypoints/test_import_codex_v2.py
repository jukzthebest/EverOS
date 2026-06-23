"""Unit coverage for the Codex v2 markdown importer."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from everos.entrypoints.cli.commands.import_cmd import (
    CodexMessage,
    CodexSession,
    _session_to_v2_record,
    _write_v2_record,
)
from everos.memory.cascade.registry import match_kind


def test_v2_markdown_paths_route_to_dedicated_handlers() -> None:
    assert (
        match_kind("codex/projects/Yundun/episodes/2026/06/foo.md").name
        == "v2_episode"
    )
    assert (
        match_kind("codex/projects/Yundun/cases/2026/06/foo.md").name == "v2_case"
    )
    assert (
        match_kind("codex/projects/Yundun/playbooks/2026/06/foo.md").name
        == "v2_playbook"
    )
    assert match_kind("codex/projects/Yundun/PROJECT.md") is None


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
            project_id="Yundun",
            agent_id="codex",
            cwd="/Users/lengxiaochu/Yundun/yundun-apsarastack",
            messages=[
                CodexMessage(role="user", content="commit", timestamp_ms=ts),
                CodexMessage(
                    role="assistant",
                    content="已提交 scanner_main 连接命令文档更新。",
                    timestamp_ms=ts + 1,
                ),
            ],
        )

    record1 = _session_to_v2_record(
        build("019ecf54-dde8-7293-8d1e-db2aae686fbc", src1),
        user_id="lengxiaochu",
        agent_id="codex",
    )
    record2 = _session_to_v2_record(
        build("019ecf54-dde8-7293-8d1e-db2aae686fbd", src2),
        user_id="lengxiaochu",
        agent_id="codex",
    )

    assert record1["title"] == "已提交 scanner_main 连接命令文档更新"
    path1 = _write_v2_record(tmp_path / "memory", "codex", record1)
    path2 = _write_v2_record(tmp_path / "memory", "codex", record2)

    assert path1 != path2
    assert path1.name.startswith("2026-06-16-已提交-scanner_main")
    assert path2.name.startswith("2026-06-16-已提交-scanner_main")
    project_index = tmp_path / "memory" / "codex" / "projects" / "Yundun"
    assert (project_index / "PROJECT.md").exists()
