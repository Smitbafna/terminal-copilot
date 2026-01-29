"""CLI entry point for Terminal Copilot using Typer."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from terminal_copilot.config import load_config
from terminal_copilot.models import CommandResult
from terminal_copilot.workflow import run_workflow

app = typer.Typer(
    name="terminal-copilot",
    help="A CLI tool that wraps shell commands with diagnostics.",
)
console = Console()


def display_result(result: CommandResult, matching_plugin: str) -> None:
    """Display the command result using Rich panels.

    Args:
        result: The command execution result to display.
        matching_plugin: The name of the plugin that matched the command.
    """
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("Command:", result.command)
    summary.add_row("Plugin:", matching_plugin)
    summary.add_row(
        "Exit Code:",
        f"[green]{result.exit_code}[/green]"
        if result.success
        else f"[red]{result.exit_code}[/red]",
    )
    summary.add_row("Time:", f"{result.execution_time}s")
    summary.add_row(
        "Status:",
        "[green]✓ Success[/green]" if result.success else "[red]✗ Failed[/red]",
    )

    console.print()
    console.print(Panel(summary, title="[bold]Terminal Copilot[/bold]", border_style="blue"))
    console.print()

    if result.stdout:
        console.print(Panel(result.stdout.strip(), title="stdout", border_style="green"))
        console.print()

    if result.stderr:
        console.print(Panel(result.stderr.strip(), title="stderr", border_style="red"))
        console.print()


def _run_impl(command_str: str, config_path: Optional[Path] = None) -> None:
    """Shared implementation: execute a command and display the result."""
    _ = load_config(config_path)

    state = run_workflow(command_str)

    if state.command_result is None:
        console.print("[red]Error: command execution returned no result[/red]")
        raise typer.Exit(code=1)

    display_result(
        result=state.command_result,
        matching_plugin=state.matching_plugin or "unknown",
    )

    if not state.command_result.success:
        raise typer.Exit(code=state.command_result.exit_code)


@app.command()
def run(
    ctx: typer.Context,
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
        exists=False,
    ),
) -> None:
    """Execute a shell command and display the result."""
    # ctx.args contains everything after the subcommand
    # Join them back into a single command string
    command_str = " ".join(ctx.args)
    if not command_str:
        console.print("[red]Error: no command provided[/red]")
        raise typer.Exit(code=1)
    _run_impl(command_str, config)


def entry() -> None:
    """Entry point for the CLI.  Patches sys.argv so that quoted arguments
    reach Typer properly as extra args instead of being tokenized."""
    # If the user ran: terminal-copilot run <command...>
    # We intercept and pass everything after 'run' as extra_args to the run command
    # This avoids Typer's shell-style tokenization of the command argument
    if len(sys.argv) >= 3 and sys.argv[1] == "run":
        # Collect everything after "run" as a single quoted argument
        cmd_parts = sys.argv[2:]
        if not cmd_parts:
            print("Error: no command provided", file=sys.stderr)
            sys.exit(1)
        command_str = " ".join(cmd_parts)

        # Parse --config/-c if present
        config_path: Optional[Path] = None
        remaining: list[str] = []
        i = 0
        while i < len(cmd_parts):
            if cmd_parts[i] in ("--config", "-c") and i + 1 < len(cmd_parts):
                config_path = Path(cmd_parts[i + 1])
                i += 2
            else:
                remaining.append(cmd_parts[i])
                i += 1

        command_str = " ".join(remaining)
        _run_impl(command_str, config_path)
    else:
        # Normal Typer flow (--help, --install-completion, etc.)
        app()