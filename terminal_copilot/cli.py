"""CLI entry point for Terminal Copilot using Typer."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
from rich.status import Status
from rich.text import Text

from terminal_copilot.config import load_config
from terminal_copilot.models import CommandResult, InvestigationResult
from terminal_copilot.agent import run_agent, run_agent_streaming
from terminal_copilot.plugins import get_all_plugins
from terminal_copilot.history import record_failed_command, load_last_failed
from terminal_copilot.investigation import run_investigation

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


def _display_diagnosis(result: Optional[InvestigationResult]) -> None:
    """Display the investigation diagnosis and suggested commands."""
    if not result:
        return

    # Root cause panel
    if result.root_cause:
        console.print()
        console.print(Panel(
            result.root_cause,
            title="[bold yellow]🔍 Root Cause[/bold yellow]",
            border_style="yellow",
        ))
        console.print()

    # Confidence bar
    if result.confidence > 0:
        bar_len = 20
        filled = int(bar_len * result.confidence / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        confidence_color = (
            "[green]" if result.confidence >= 80
            else "[yellow]" if result.confidence >= 50
            else "[red]"
        )
        console.print(
            f"  Confidence: {confidence_color}{bar} {result.confidence}%[/]"
        )
        console.print()

    # Suggested commands
    if result.suggested_commands:
        cmd_table = Table.grid(padding=(0, 2))
        cmd_table.add_column(style="bold cyan", width=4)
        cmd_table.add_column()
        for i, cmd in enumerate(result.suggested_commands, 1):
            cmd_table.add_row(f"{i}.", f"[bold white]{cmd}[/bold white]")
        console.print(Panel(
            cmd_table,
            title="[bold green]💡 Suggested Commands[/bold green]",
            border_style="green",
        ))
        console.print()

        console.print("[dim]Tip: Copy-paste any command above to fix the issue.[/dim]")
        console.print()


def _display_agent_progress(command: str) -> "AgentState":
    """Run the agent with live progress display showing tool calls.

    Args:
        command: The command to investigate.

    Returns:
        The final AgentState.
    """
    from terminal_copilot.models import AgentState as AgentStateModel

    console.print(f"[bold]Running[/bold] [white]{command}[/white]...")
    console.print()

    final_state: Optional[AgentStateModel] = None
    tool_count = 0
    tool_spinner = Status("[yellow]Investigating...[/yellow]", console=console)
    tool_spinner.start()

    for event in run_agent_streaming(command):
        event_type = event.get("type")

        if event_type == "node":
            node = event.get("node", "")
            # Print node progress as it happens
            if node == "execute_command":
                tool_spinner.update("[blue]⚡ Running command...[/blue]")
            elif node == "find_plugin":
                tool_spinner.update("[blue]🔌 Detecting plugin...[/blue]")
            elif node == "collect_context":
                tool_spinner.update("[blue]📦 Collecting context...[/blue]")
            elif node == "initialize_agent":
                tool_spinner.stop()
                plugin_name = "unknown"
                data = event.get("data", {})
                if isinstance(data, dict):
                    plugin_name = data.get("matching_plugin", "unknown")
                console.print("  [green]✓[/green] Plugin: [bold]" + plugin_name + "[/bold]")
                console.print()
                tool_spinner = Status("[yellow]🤔 LLM reasoning...[/yellow]", console=console)
                tool_spinner.start()

        elif event_type == "tool_request":
            tool = event.get("tool", "")
            args = event.get("args", {})
            thought = event.get("thought", "")
            tool_count += 1
            tool_spinner.stop()

            args_str = ""
            if args:
                args_str = " " + " ".join(f"{k}={v}" for k, v in args.items())

            console.print(f"  [bold cyan]🔧 Tool {tool_count}:[/bold cyan] [white]{tool}{args_str}[/white]")
            if thought:
                console.print(f"    [dim]└ {thought}[/dim]")
            console.print()

            tool_spinner = Status(f"[yellow]Executing {tool}...[/yellow]", console=console)
            tool_spinner.start()

        elif event_type == "tool_result":
            tool_spinner.stop()
            success = event.get("success", False)
            summary = event.get("summary", "")
            icon = "[green]✓[/green]" if success else "[red]✗[/red]"
            summary_str = summary[:100] + "..." if len(summary) > 100 else summary
            console.print(f"    {icon} [dim]{summary_str}[/dim]")
            console.print()
            tool_spinner = Status("[yellow]🤔 LLM reasoning...[/yellow]", console=console)
            tool_spinner.start()

        elif event_type == "done":
            tool_spinner.stop()
            state_data = event.get("state")
            if state_data:
                final_state = state_data
            break

    console.print("[bold blue]🔍 Generating diagnosis...[/bold blue]")
    console.print()

    if final_state is None:
        # Fallback: run non-streaming
        final_state = run_agent(command)

    return final_state


def _run_impl(command_str: str, config_path: Optional[Path] = None) -> None:
    """Shared implementation: execute a command and display the result."""
    _ = load_config(config_path)

    # Run the agent with live progress display
    state = _display_agent_progress(command_str)

    if state.command_result is None:
        console.print("[red]Error: command execution returned no result[/red]")
        raise typer.Exit(code=1)

    # Display result summary
    display_result(
        result=state.command_result,
        matching_plugin=state.matching_plugin or "unknown",
        context=state.context,
    )

    if state.command_result.success:
        # Command succeeded — no investigation needed
        return

    # Record to history for `explain` command
    record_failed_command(
        command=state.command,
        result=state.command_result,
        plugin=state.matching_plugin or "unknown",
        context=state.context,
    )

    # Command failed — show AI diagnosis
    if state.diagnosis:
        _display_diagnosis(state.diagnosis)

    raise typer.Exit(code=state.command_result.exit_code)


@app.command()
def run(
    command: List[str] = typer.Argument(
        ...,
        help="The shell command to execute (no quotes needed: `run git status` works)",
        show_default=False,
    ),
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
        exists=False,
    ),
) -> None:
    """Execute a shell command and display the result with diagnostic context.

    Example: terminal-copilot run npm install
    """
    command_str = " ".join(command)
    _run_impl(command_str, config)


@app.command()
def explain(
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
        exists=False,
    ),
) -> None:
    """Analyze the last failed command without re-executing it.

    Uses history stored in ~/.terminal-copilot/history.json.

    Example: terminal-copilot explain
    """
    _ = load_config(config)

    # Load last failed command from history
    entry = load_last_failed()
    if entry is None:
        console.print("[yellow]No failed command history found.[/yellow]")
        console.print("Run [bold]terminal-copilot run <command>[/bold] first.")
        raise typer.Exit(code=1)

    # Show what we're analyzing
    console.print()
    console.print(Panel(
        f"[bold]Command:[/bold] {entry.command}\n"
        f"[bold]Exit Code:[/bold] [red]{entry.exit_code}[/red]\n"
        f"[bold]Plugin:[/bold] {entry.plugin}\n"
        f"[bold]Timestamp:[/bold] {entry.timestamp}",
        title="[bold]Analyzing Last Failed Command[/bold]",
        border_style="blue",
    ))
    console.print()

    if entry.context:
        console.print(_build_context_tree(entry.context, "Stored Context"))
        console.print()

    if entry.stderr:
        console.print(Panel(entry.stderr.strip(), title="stderr", border_style="red"))
        console.print()

    # Run the investigation (no re-execution)
    console.print("[bold yellow]🔍 Investigating...[/bold yellow]")
    console.print()

    investigation_data = entry.to_investigation_data()

    try:
        result = run_investigation(investigation_data)
        _display_diagnosis(result)
    except ValueError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        console.print()
        console.print(
            "[yellow]Tip: Set the GEMINI_API_KEY or GOOGLE_API_KEY "
            "environment variable.[/yellow]"
        )
        raise typer.Exit(code=1)


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

    # Check history file
    from terminal_copilot.history import HISTORY_PATH
    checks.append(("History file exists", HISTORY_PATH.exists(), None))

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
    """Entry point for the CLI (installed via pyproject.toml scripts)."""
    app()


if __name__ == "__main__":
    entry()