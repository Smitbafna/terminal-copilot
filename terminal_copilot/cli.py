"""CLI entry point for Terminal Copilot using Typer."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
from rich.text import Text

from terminal_copilot.config import load_config
from terminal_copilot.models import CommandResult
from terminal_copilot.workflow import run_workflow
from terminal_copilot.plugins import get_all_plugins

app = typer.Typer(
    name="terminal-copilot",
    help="A CLI tool that wraps shell commands with diagnostics.",
)
console = Console()


def _format_context_value(value: Any) -> str:
    """Format a context value for display."""
    if isinstance(value, bool):
        return "[green]✓ Yes[/green]" if value else "[dim]No[/dim]"
    if isinstance(value, list):
        if not value:
            return "[dim](empty)[/dim]"
        return "\n".join(f"  • {item}" for item in value)
    if value is None:
        return "[dim](not found)[/dim]"
    return str(value)


def _build_context_tree(context: Dict[str, Any], title: str = "Investigation Context") -> Tree:
    """Build a Rich Tree from context dict."""
    tree = Tree(f"[bold blue]{title}[/bold blue]")

    def _add_to_tree(data: Any, parent: Tree) -> None:
        if isinstance(data, dict):
            for key, value in data.items():
                label = f"[bold]{key.replace('_', ' ').title()}:[/bold] "
                if isinstance(value, (dict, list)):
                    label += ""
                    branch = parent.add(label)
                    _add_to_tree(value, branch)
                else:
                    label += _format_context_value(value)
                    parent.add(label)
        elif isinstance(data, list):
            if data:
                for item in data:
                    if isinstance(item, dict):
                        branch = parent.add("[dim]•[/dim]")
                        _add_to_tree(item, branch)
                    else:
                        parent.add(f"  • {item}")
            else:
                parent.add("[dim](empty)[/dim]")
        else:
            parent.add(_format_context_value(data))

    _add_to_tree(context, tree)
    return tree


def display_result(result: CommandResult, matching_plugin: str, context: Optional[Dict[str, Any]] = None) -> None:
    """Display the command result using Rich panels.

    Args:
        result: The command execution result to display.
        matching_plugin: The name of the plugin that matched the command.
        context: Optional structured context collected by the plugin.
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

    if context:
        console.print(_build_context_tree(context))
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
        context=state.context,
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
    """Execute a shell command and display the result with diagnostic context."""
    # ctx.args contains everything after the subcommand
    # Join them back into a single command string
    command_str = " ".join(ctx.args)
    if not command_str:
        console.print("[red]Error: no command provided[/red]")
        raise typer.Exit(code=1)
    _run_impl(command_str, config)


@app.command()
def doctor() -> None:
    """Check the system for all required dependencies and configuration."""
    console.print()
    console.print("[bold blue]Terminal Copilot Doctor[/bold blue]")
    console.print("=" * 50)
    console.print()

    checks: list[tuple[str, bool, Optional[str]]] = []

    # Check Git
    git_ok = _check_tool("git --version")
    checks.append(("Git installed", git_ok, None))

    # Check Docker
    docker_ok = _check_tool("docker --version")
    checks.append(("Docker installed", docker_ok, None))

    # Check Node
    node_ok = _check_tool("node --version")
    checks.append(("Node installed", node_ok, None))

    # Check Python
    python_ok = _check_tool("python3 --version") or _check_tool("python --version")
    checks.append(("Python installed", python_ok, None))

    # Check Gemini API Key
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    checks.append(("Gemini API Key configured", bool(gemini_key), None))

    # Check config loaded
    try:
        config = load_config()
        checks.append(("Config loaded", True, None))
    except Exception:
        checks.append(("Config loaded", False, "Could not load config"))

    # Check plugins loaded
    plugins_loaded = len(get_all_plugins()) > 0
    checks.append(("Plugins loaded", plugins_loaded, None))

    # Render results
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold", width=30)
    table.add_column()

    for label, ok, detail in checks:
        icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
        status = "[green]OK[/green]" if ok else "[red]MISSING[/red]"
        row = f"{icon}  {label}"
        table.add_row(row, status)

    console.print(Panel(table, title="[bold]System Check[/bold]", border_style="blue"))
    console.print()

    # Summary
    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    if passed == total:
        console.print("[bold green]All checks passed![/bold green]")
    else:
        console.print(
            f"[bold yellow]{passed}/{total} checks passed. "
            f"{total - passed} issue(s) found.[/bold yellow]"
        )
        console.print()
        console.print(
            "[yellow]Tip: Some features may not work without the missing dependencies.[/yellow]"
        )

    console.print()


def _check_tool(cmd: str) -> bool:
    """Check if a tool is available on the system."""
    import subprocess
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


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