"""Local sync health checks."""

from __future__ import annotations

from pathlib import Path

import typer

from everos.core.persistence import MemoryRoot

app = typer.Typer(
    name="sync",
    help="Inspect local-first sync safety for the memory root",
    no_args_is_help=True,
)


@app.command("doctor")
def doctor() -> None:
    """Check whether the memory root is safe for Markdown-only sync."""
    root = MemoryRoot.default()
    root.ensure()
    typer.echo(f"memory root: {root.root}")
    _line("markdown files", str(_count_markdown(root.root)))
    _line(".index present", "yes" if root.index_dir.exists() else "no")
    _line("sqlite present", "yes" if root.system_db.exists() else "no")
    _line("lancedb present", "yes" if root.lancedb_dir.exists() else "no")

    ignored = _find_sync_ignore(root.root)
    if ignored:
        _line("sync ignore", str(ignored))
        text = ignored.read_text(encoding="utf-8", errors="replace")
        for required in (".index", ".tmp", "*.db", "*.db-wal", "*.db-shm"):
            _line(f"ignore {required}", "yes" if required in text else "missing")
    else:
        _line("sync ignore", "missing")
        typer.echo("warning: add .stignore or .gitignore entries for .index and .tmp")


def _line(label: str, value: str) -> None:
    typer.echo(f"{label:18} {value}")


def _count_markdown(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(
        1
        for path in root.rglob("*.md")
        if ".index" not in path.parts and ".tmp" not in path.parts
    )


def _find_sync_ignore(root: Path) -> Path | None:
    for name in (".stignore", ".gitignore"):
        candidate = root / name
        if candidate.exists():
            return candidate
    return None
