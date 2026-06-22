"""Derived index maintenance commands."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Annotated

import typer

from everos.core.persistence import MemoryRoot
from everos.entrypoints.cli.commands.cascade import _build_orchestrator, _runtime

app = typer.Typer(
    name="index",
    help="Maintain local SQLite/LanceDB indexes",
    no_args_is_help=True,
)


@app.command("rebuild")
def rebuild(
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Actually remove .index before rebuilding."),
    ] = False,
) -> None:
    """Delete derived indexes and rebuild them from Markdown."""
    root = MemoryRoot.default()
    root.ensure()
    if not yes:
        typer.echo(f"would remove derived index directory: {root.index_dir}")
        typer.echo("rerun with --yes to rebuild")
        return
    _remove_index_dir(root.index_dir)

    async def _run() -> None:
        async with _runtime():
            processed = await _build_orchestrator().sync_once()
            typer.echo(f"rebuild complete — processed {processed} row(s)")

    asyncio.run(_run())


def _remove_index_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
