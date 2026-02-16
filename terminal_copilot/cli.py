"""CLI entry point for Terminal Copilot using Typer."""

from __future__ import annotations

import os
import re
import subprocess
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
from terminal_copilot.models import CommandResult, InvestigationResult, AgentState
from terminal_copilot.agent import run_agent, run_agent_streaming
from terminal_copilot.plugins import (
    get_all_plugins,
    get_plugin_by_name,
    find_matching_plugin,
    detect_project_type,
    PROJECT_MARKERS,
    validate_environment,
    _detect_framework,
    _find_project_root,
)
from terminal_copilot.history import record_failed_command, load_last_failed
from terminal_copilot.investigation import run_investigation, build_investigation
from terminal_copilot.preflight import (
    run_preflight,
    format_preflight_result,
    predict_potential_issues,
    run_intelligent_warnings,
)
from terminal_copilot.runner import run_command

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
            f"  Confidence: {confidence_color}{bar} {result.confidence}%[/]",
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


def _prompt_for_fix(suggested_commands: List[str]) -> Optional[int]:
    """Prompt the user to select a fix option.

    Args:
        suggested_commands: List of suggested commands to choose from.

    Returns:
        The index of the selected command (0-based), or -1 for cancel, None for error.
    """
    console.print()
    console.print("[bold]Choose a fix to apply:[/bold]")
    console.print()

    # Display options with numbering
    for i, cmd in enumerate(suggested_commands, 1):
        console.print(f"  {i}. [bold white]{cmd}[/bold white]")
    console.print(f"  {len(suggested_commands) + 1}. [dim]Cancel[/dim]")
    console.print()

    try:
        response = console.input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]Cancelled[/dim]")
        return -1

    # Parse the selection
    try:
        selection = int(response)
    except ValueError:
        console.print("[red]Invalid selection. Please enter a number.[/red]")
        return None

    if selection == len(suggested_commands) + 1:
        console.print("[dim]Cancelled[/dim]")
        return -1

    if 1 <= selection <= len(suggested_commands):
        return selection - 1  # Return 0-based index

    console.print(f"[red]Invalid selection. Enter a number between 1 and {len(suggested_commands) + 1}.[/red]")
    return None


def _display_fix_result(result: CommandResult, plugin_name: str, context: Optional[Dict[str, Any]] = None) -> None:
    """Display the result of executing a fix command."""
    display_result(result, plugin_name, context)


def _extract_command(cmd: str) -> str:
    """Extract only the shell command, stripping parenthetical explanations.
    
    The LLM may return commands with explanations like:
    'docker build -t image . (if you intended to build a local image)'
    
    This function extracts just 'docker build -t image .'
    """
    # If there's a parenthetical at the end, strip it
    if "(" in cmd and ")" in cmd:
        # Find the last complete parenthetical
        last_open = cmd.rfind("(")
        last_close = cmd.rfind(")")
        if last_close > last_open:
            cmd = cmd[:last_open].strip()
    return cmd


def _run_repair_loop(
    original_command: str,
    original_result: CommandResult,
    plugin_name: str,
    context: Optional[Dict[str, Any]],
    diagnosis: InvestigationResult,
) -> bool:
    """Run the interactive repair loop.

    Args:
        original_command: The original command that failed.
        original_result: The result of the original command.
        plugin_name: The matching plugin name.
        context: Structured context from the plugin.
        diagnosis: The investigation diagnosis.

    Returns:
        True if a fix succeeded, False otherwise.
    """
    if not diagnosis.suggested_commands:
        console.print("[dim]No suggested fixes available.[/dim]")
        return False

    while True:
        # Prompt user to choose a fix
        selection = _prompt_for_fix(diagnosis.suggested_commands)

        if selection is None:
            # Invalid input, retry
            continue

        if selection == -1:
            # User cancelled
            return False

        # Execute the chosen fix
        raw_command = diagnosis.suggested_commands[selection]
        fix_command = _extract_command(raw_command)
        console.print()
        console.print(f"[bold]Executing:[/bold] [cyan]{fix_command}[/cyan]")

        fix_result = run_command(fix_command)
        _display_fix_result(fix_result, find_matching_plugin(fix_command) or "unknown")

        if fix_result.success:
            console.print()
            console.print("[bold green]✓ Fix succeeded![/bold green]")
            return True

        # Fix failed - re-investigate
        console.print()
        console.print("[yellow]Fix failed. Re-investigating...[/yellow]")
        console.print()

        # Build new investigation with the fix output
        new_investigation = build_investigation(
            command=original_command,
            result=fix_result,
            plugin=plugin_name,
            context=context,
        )

        try:
            diagnosis = run_investigation(new_investigation)
            if diagnosis.root_cause:
                _display_diagnosis(diagnosis)
            else:
                console.print("[dim]No diagnosis available for the fix attempt.[/dim]")
                break
        except ValueError as exc:
            console.print(f"[red]Error during re-investigation: {exc}[/red]")
            break


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


def _show_prediction_preview(command_str: str) -> bool:
    """Show prediction preview before command execution.
    
    Returns True if user confirms (Y), False if user declines (n).
    """
    # Get project type and framework
    project_type = detect_project_type()
    project_root = _find_project_root()
    framework = _detect_framework(project_root)
    
    console.print()
    console.print("[bold blue]🔮 Command Prediction[/bold blue]")
    console.print()
    
    # Build preview table
    preview_table = Table.grid(padding=(0, 2))
    preview_table.add_column(style="bold")
    preview_table.add_column()
    
    # Show project type with framework if detected
    if framework:
        console.print(f"Detected project: [bold]{framework}[/bold]")
    elif project_type != "Unknown":
        console.print(f"Detected project: [bold]{project_type}[/bold]")
    else:
        console.print("Detected project: [dim]None[/dim]")
    
    console.print()
    preview_table.add_row("[bold]Expected command:[/bold]", f"[cyan]{command_str}[/cyan]")
    
    console.print(Panel(preview_table, title="[bold]Command Prediction[/bold]", border_style="blue"))
    console.print()
    
    # Show plugin preflight checks
    plugin_name = find_matching_plugin(command_str)
    plugin = get_plugin_by_name(plugin_name)
    
    if plugin:
        checks = plugin.preflight_checks()
        console.print(f"[bold blue]{plugin.name.title()} Plugin Checks:[/bold blue]")
        console.print()
        
        # Build checks table in the format shown in task
        checks_table = Table.grid(padding=(0, 2))
        checks_table.add_column()
        checks_table.add_column()
        
        for check in checks:
            if check.passed:
                checks_table.add_row(f"[green]✓[/green]", check.message)
            else:
                checks_table.add_row(f"[yellow]⚠[/yellow]", check.message)
        
        console.print(checks_table)
        console.print()
    
    # Check for potential issues (plugin-specific predictions)
    potential_issues = predict_potential_issues(command_str)
    
    # Check for intelligent warnings (proactive issue detection)
    intelligent_warnings = run_intelligent_warnings(command_str)
    
    all_issues = potential_issues + intelligent_warnings
    
    if all_issues:
        console.print("[bold yellow]Potential issues:[/bold yellow]")
        console.print()
        for issue in all_issues:
            icon = "[yellow]⚠[/yellow]" if issue.level == "warning" else "[blue]ℹ[/blue]"
            console.print(f"  {icon} {issue.message}")
            if issue.suggestion:
                console.print(f"    [dim]{issue.suggestion}[/dim]")
        console.print()
    
    # Ask for confirmation
    try:
        response = console.input("Continue? [Y/n]: ")
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]Cancelled[/dim]")
        return False
    
    if response.lower() in ("n", "no"):
        console.print("[dim]Aborted by user[/dim]")
        return False
    
    return True


def _run_impl(command_str: str, config_path: Optional[Path] = None, skip_preflight: bool = False) -> None:
    """Shared implementation: execute a command and display the result."""
    _ = load_config(config_path)

    # Run preflight checks before executing
    if not skip_preflight:
        preflight_result = run_preflight(command_str)
        if preflight_result.errors:
            # Show errors and ask for confirmation
            console.print()
            console.print(Panel(
                preflight_result.errors[0].message,
                title="[bold red]Preflight Error[/bold red]",
                border_style="red",
            ))
            if preflight_result.errors[0].suggestion:
                console.print(f"[dim]{preflight_result.errors[0].suggestion}[/dim]")
            console.print()
            console.print("[yellow]Use --skip-preflight to override[/yellow]")
            raise typer.Exit(code=1)
        
        if preflight_result.warnings or preflight_result.infos:
            console.print("[bold blue]🔍 Preflight Checks[/bold blue]")
            console.print()
            if preflight_result.warnings:
                for issue in preflight_result.warnings:
                    console.print(f"[yellow]⚠ {issue.message}[/yellow]")
                    if issue.suggestion:
                        console.print(f"  [dim]{issue.suggestion}[/dim]")
            if preflight_result.infos:
                for issue in preflight_result.infos:
                    console.print(f"[blue]ℹ {issue.message}[/blue]")
                    if issue.suggestion:
                        console.print(f"  [dim]{issue.suggestion}[/dim]")
            console.print()
    
    # Show prediction preview (unless skipping preflight)
    if not skip_preflight:
        if not _show_prediction_preview(command_str):
            raise typer.Exit(code=130)

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

        # Run interactive repair loop
        if state.diagnosis.suggested_commands:
            _run_repair_loop(
                original_command=state.command,
                original_result=state.command_result,
                plugin_name=state.matching_plugin or "unknown",
                context=state.context,
                diagnosis=state.diagnosis,
            )

    raise typer.Exit(code=state.command_result.exit_code)


@app.command()
def run(
    command: List[str] = typer.Argument(
        ...,
        help="The shell command to execute (no quotes needed: `run git status` works)",
        show_default=False,
    ),
    skip_preflight: bool = typer.Option(
        False,
        "--skip-preflight",
        "-s",
        help="Skip preflight checks before running the command",
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
    _run_impl(command_str, config, skip_preflight)


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

        # Run interactive repair loop
        if result.suggested_commands:
            _run_repair_loop(
                original_command=entry.command,
                original_result=CommandResult(
                    command=entry.command,
                    stdout=entry.stdout,
                    stderr=entry.stderr,
                    exit_code=entry.exit_code,
                    execution_time=0.0,
                    success=False,
                ),
                plugin_name=entry.plugin,
                context=entry.context,
                diagnosis=result,
            )
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


def _get_all_detected_projects(cwd: Optional[Path] = None) -> Dict[str, bool]:
    """Get all detected project types for a directory.

    Args:
        cwd: The working directory to check. Defaults to current directory.

    Returns:
        A dictionary mapping project type names to whether they were detected.
    """
    project_root = cwd or Path.cwd()
    detected: Dict[str, bool] = {}

    for project_type, markers in PROJECT_MARKERS.items():
        found = False
        for marker in markers:
            if (project_root / marker).exists():
                found = True
                break
        detected[project_type] = found

    return detected


@app.command()
def detect(
    path: Optional[Path] = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to check for project type (defaults to current directory)",
        exists=False,
    ),
) -> None:
    """Detect the project type based on marker files.

    Walks up from the given directory to find a project root, then checks
    for known project marker files to determine the project type.

    Example: terminal-copilot detect
    """
    cwd = path or Path.cwd()
    project_root = cwd.resolve()

    # Walk up to find project root
    for parent in [project_root] + list(project_root.parents):
        if (parent / ".git").exists():
            project_root = parent
            break

    console.print()
    console.print("[bold blue]Project Detection[/bold blue]")
    console.print()

    # Get primary project type
    project_type = detect_project_type(cwd)

    # Display results
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold", width=20)
    table.add_column()

    if project_type != "Unknown":
        icon = "[green]✓[/green]"
        table.add_row("Project Type:", f"{icon} [bold]{project_type}[/bold]")
    else:
        icon = "[dim]○[/dim]"
        table.add_row("Project Type:", f"{icon} [dim]Unknown[/dim]")

    table.add_row("Project Root:", str(project_root))

    console.print(Panel(table, title="[bold]Project Detection Result[/bold]", border_style="blue"))
    console.print()

    # Show all detected markers
    console.print("[bold]Detected Markers:[/bold]")
    console.print()

    marker_table = Table.grid(padding=(0, 2))
    marker_table.add_column(style="bold", width=20)
    marker_table.add_column()

    all_detected = _get_all_detected_projects(project_root)
    for project_type_name, is_detected in all_detected.items():
        icon = "[green]✓[/green]" if is_detected else "[dim]○[/dim]"
        status = "Found" if is_detected else "Not found"

        # Get the marker files for this project type
        markers = PROJECT_MARKERS.get(project_type_name, [])
        marker_str = ", ".join(markers) if markers else "N/A"

        marker_table.add_row(
            f"{icon} {project_type_name}",
            f"{status} {marker_str}"
        )

    console.print(marker_table)
    console.print()


@app.command()
def validate(
    path: Optional[Path] = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to check for environment validation (defaults to current directory)",
        exists=False,
    ),
) -> None:
    """Validate the environment against detected project requirements.

    Checks for version compatibility and runtime environment issues:

    - Node.js: Compares installed version against package.json engines requirement
    - Python: Compares installed version against pyproject.toml python_requires
    - Docker: Checks if Docker daemon is running when Dockerfile exists

    Example: terminal-copilot validate
    """
    cwd = path or Path.cwd()

    console.print()
    console.print("[bold blue]Environment Validation[/bold blue]")
    console.print()

    # Get environment warnings
    warnings = validate_environment(cwd)

    if not warnings:
        console.print("[bold green]✓ Environment is compatible with project requirements[/bold green]")
        console.print()
        return

    # Display warnings
    console.print("[yellow]⚠ Environment Issues Detected:[/yellow]")
    console.print()

    warning_table = Table.grid(padding=(0, 2))
    warning_table.add_column(style="bold", width=15)
    warning_table.add_column()

    for check_name, message in warnings.items():
        if "version mismatch" in check_name.lower() or "mismatch" in message.lower():
            icon = "[yellow]⚠[/yellow]"
        else:
            icon = "[red]✗[/red]"
        warning_table.add_row(f"{icon} {check_name}", message)

    console.print(warning_table)
    console.print()

    # Show project type for context
    project_type = detect_project_type(cwd)
    console.print(f"[dim]Project type: {project_type}[/dim]")
    console.print()


def entry() -> None:
    """Entry point for the CLI (installed via pyproject.toml scripts)."""
    app()


if __name__ == "__main__":
    entry()


# ── Health Report ─────────────────────────────────────────────────────────────


@app.command(name="health")
def health(
    path: Optional[Path] = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to check for health report (defaults to current directory)",
        exists=False,
    ),
) -> None:
    """Generate a health report for the current project.

    Shows environment checks and project status:

    - Node.js: Node and npm versions
    - package.json: Project manifest
    - .env.local: Environment file for Next.js projects
    - Git: Working directory status
    - Docker: Daemon running status

    Example: terminal-copilot health
    """
    
    cwd = path or Path.cwd()
    project_root = _find_project_root(cwd)
    framework = _detect_framework(project_root)
    project_type = detect_project_type(cwd)
    
    console.print()
    
    # Show project name
    if framework:
        console.print(f"[bold blue]Project:[/bold blue] {framework}")
    elif project_type != "Unknown":
        console.print(f"[bold blue]Project:[/bold blue] {project_type}")
    else:
        console.print(f"[bold blue]Project:[/bold blue] [dim]Unknown[/dim]")
    console.print()
    
    console.print("[bold blue]Environment[/bold blue]")
    console.print()
    
    # Build checks table
    checks_table = Table.grid(padding=(0, 2))
    checks_table.add_column()
    checks_table.add_column()
    
    score = 100
    
    # Check Node.js
    node_version = _run_tool_check("node --version", extract_version=True)
    if node_version:
        checks_table.add_row("[green]✓[/green]", f"Node {node_version}")
    else:
        checks_table.add_row("[red]✗[/red]", "Node not installed")
        score -= 20
    
    # Check npm
    npm_version = _run_tool_check("npm --version")
    if npm_version:
        checks_table.add_row("[green]✓[/green]", f"npm {npm_version}")
    else:
        checks_table.add_row("[red]✗[/red]", "npm not installed")
        score -= 20
    
    # Check package.json
    package_json = project_root / "package.json"
    if package_json.exists():
        checks_table.add_row("[green]✓[/green]", "package.json")
    else:
        # Only penalize if it's a Node project
        if project_type == "Node":
            checks_table.add_row("[red]✗[/red]", "package.json missing")
            score -= 15
        else:
            checks_table.add_row("[dim]○[/dim]", "[dim]package.json (not a Node project)[/dim]")
    
    # Check .env.local (Next.js convention)
    env_file = project_root / ".env"
    env_local_file = project_root / ".env.local"
    if env_file.exists() and not env_local_file.exists():
        checks_table.add_row("[yellow]⚠[/yellow]", ".env.local missing")
        score -= 5
    elif env_local_file.exists():
        checks_table.add_row("[green]✓[/green]", ".env.local")
    else:
        checks_table.add_row("[dim]○[/dim]", "[dim].env.local (not needed)[/dim]")
    
    # Check Git - working directory clean
    git_root = _run_tool_check("git rev-parse --show-toplevel")
    if git_root:
        # Check if working directory is clean
        status = _run_tool_check("git status --porcelain")
        if status:
            checks_table.add_row("[yellow]⚠[/yellow]", "Git dirty")
            score -= 8
        else:
            checks_table.add_row("[green]✓[/green]", "Git clean")
    else:
        checks_table.add_row("[dim]○[/dim]", "[dim]Git (not a repository)[/dim]")
    
    # Check Docker daemon
    docker_version = _run_tool_check("docker --version")
    if docker_version:
        docker_ps = _run_tool_check("docker ps ", timeout=5)
        if docker_ps and "Cannot connect to the Docker daemon" in docker_ps:
            checks_table.add_row("[yellow]⚠[/yellow]", "Docker not running")
            score -= 10
        else:
            checks_table.add_row("[green]✓[/green]", "Docker running")
    else:
        checks_table.add_row("[dim]○[/dim]", "[dim]Docker (not installed)[/dim]")
    
    console.print(checks_table)
    console.print()
    
    # Show overall score
    score_color = "[green]" if score >= 80 else "[yellow]" if score >= 60 else "[red]"
    console.print(
        f"[bold]Overall Score[/bold]\n\n  {score_color}{score}/100[/]"
    )
    console.print()


def _run_tool_check(cmd: str, timeout: int = 5, extract_version: bool = False) -> Optional[str]:
    """Run a quick shell command and return stripped stdout, or None on failure."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            if extract_version and output:
                # Extract version number (e.g., "v22.0.0" -> "22")
                match = re.search(r"(\d+)", output)
                if match:
                    return match.group(1)
            return output if output else None
        return None
    except Exception:
        return None