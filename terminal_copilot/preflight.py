"""Project health and preflight checks for Terminal Copilot.

This module inspects the project for common problems before executing commands
and warns the user if something is obviously wrong.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from terminal_copilot.plugins import find_matching_plugin, get_plugin_by_name


@dataclass
class PreflightIssue:
    """A single preflight issue detected."""
    level: str  # "error", "warning", "info"
    message: str
    suggestion: Optional[str] = None


@dataclass
class PreflightResult:
    """Result of preflight checks."""
    has_issues: bool = False
    errors: List[PreflightIssue] = field(default_factory=list)
    warnings: List[PreflightIssue] = field(default_factory=list)
    infos: List[PreflightIssue] = field(default_factory=list)

    @classmethod
    def create(cls, issues: List[PreflightIssue]) -> "PreflightResult":
        """Create a PreflightResult from a list of issues."""
        result = cls()
        for issue in issues:
            result.has_issues = True
            if issue.level == "error":
                result.errors.append(issue)
            elif issue.level == "warning":
                result.warnings.append(issue)
            else:
                result.infos.append(issue)
        return result


# ── Plugin-specific preflight checks ─────────────────────────────────────────


def _npm_preflight(command: str) -> List[PreflightIssue]:
    """Check for common npm/pnpm/yarn issues before running a command."""
    issues: List[PreflightIssue] = []
    
    # Check if we're in a project directory
    cwd = Path.cwd()
    package_json = cwd / "package.json"
    
    # Commands that need package.json
    needs_pkg_json = re.match(r"^(pnpm|npm|yarn)\s+(install|update|run|build|test|start)", command)
    if needs_pkg_json and not package_json.exists():
        issues.append(PreflightIssue(
            level="warning",
            message="No package.json found in current directory",
            suggestion="Run 'npm init' or ensure you're in a Node.js project directory",
        ))
    
    # Check npm availability
    if _run_quick_check("npm --version 2>/dev/null") is None:
        issues.append(PreflightIssue(
            level="error",
            message="npm is not installed or not in PATH",
            suggestion="Install Node.js from https://nodejs.org",
        ))
    
    # Check for common typos
    if "npm instal" in command:  # Typo: missing 'l'
        issues.append(PreflightIssue(
            level="error",
            message="Possible typo: 'npm instal' should be 'npm install'",
            suggestion="Correct the command to: npm install",
        ))
    
    if "npm uninstal" in command:  # Typo
        issues.append(PreflightIssue(
            level="error",
            message="Possible typo: 'npm uninstal' should be 'npm uninstall'",
            suggestion="Correct the command to: npm uninstall",
        ))
    
    return issues


def _docker_preflight(command: str) -> List[PreflightIssue]:
    """Check for common Docker issues before running a command."""
    issues: List[PreflightIssue] = []
    
    # Check if docker is running
    docker_version = _run_quick_check("docker --version 2>/dev/null")
    if docker_version is None:
        issues.append(PreflightIssue(
            level="error",
            message="Docker is not installed or not in PATH",
            suggestion="Install Docker from https://docker.com",
        ))
        return issues  # No point checking further if Docker isn't available
    
    # Check if Docker daemon is running
    docker_ps = _run_quick_check("docker ps 2>&1", timeout=5)
    if docker_ps and "Cannot connect to the Docker daemon" in docker_ps:
        issues.append(PreflightIssue(
            level="error",
            message="Docker daemon is not running",
            suggestion="Start Docker: 'systemctl start docker' or launch Docker Desktop",
        ))
    
    # Check for common Docker issues
    if "docker run" in command and "--rm" not in command:
        # Running without --rm might leave stale containers
        pass  # This is info, not a warning
    
    # Check for image existence for run commands
    run_match = re.search(r"docker run\s+(?:--rm\s+)?(?:--\S+\s+)*(\S+)", command)
    if run_match:
        image = run_match.group(1)
        images = _run_quick_check("docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null")
        if images and image not in images and f"{image}:latest" not in images:
            issues.append(PreflightIssue(
                level="info",
                message=f"Docker image '{image}' may not be present locally",
                suggestion=f"Pull the image first: 'docker pull {image}'",
            ))
    
    return issues


def _git_preflight(command: str) -> List[PreflightIssue]:
    """Check for common Git issues before running a command."""
    issues: List[PreflightIssue] = []
    
    # Check if we're in a git repo
    git_root = _run_quick_check("git rev-parse --show-toplevel 2>/dev/null")
    if git_root is None and "git " in command:
        issues.append(PreflightIssue(
            level="warning",
            message="Not in a Git repository",
            suggestion="Run 'git init' to create a repository or check your directory",
        ))
        return issues
    
    # Check if git is installed
    if "git " in command and _run_quick_check("git --version 2>/dev/null") is None:
        issues.append(PreflightIssue(
            level="error",
            message="Git is not installed or not in PATH",
            suggestion="Install Git from https://git-scm.com",
        ))
    
    return issues


def _python_preflight(command: str) -> List[PreflightIssue]:
    """Check for common Python issues before running a command."""
    issues: List[PreflightIssue] = []
    
    # Check if python is available
    if _run_quick_check("python3 --version 2>/dev/null") is None:
        # Check if just 'python' works
        if _run_quick_check("python --version 2>/dev/null") is None:
            issues.append(PreflightIssue(
                level="warning",
                message="Python is not installed or not in PATH",
                suggestion="Install Python from https://python.org",
            ))
    
    # Check for file existence
    run_match = re.match(r"python(?:3)?\s+(\S+)", command)
    if run_match:
        file_path = Path(run_match.group(1))
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        if not file_path.exists():
            issues.append(PreflightIssue(
                level="error",
                message=f"Python file not found: {file_path}",
                suggestion="Check the file path exists",
            ))
    
    # Check if in virtualenv
    in_venv = os.environ.get("VIRTUAL_ENV") or os.environ.get("PIP_REQUIRE_VIRTUALENV")
    if in_venv is None and ("pip" in command or "python" in command):
        issues.append(PreflightIssue(
            level="info",
            message="Not in a virtual environment",
            suggestion="Consider using a virtual environment: 'python -m venv venv && source venv/bin/activate'",
        ))
    
    return issues


def _rust_preflight(command: str) -> List[PreflightIssue]:
    """Check for common Rust issues before running a command."""
    issues: List[PreflightIssue] = []
    
    # Check if cargo is installed
    cargo_version = _run_quick_check("cargo --version 2>/dev/null")
    if cargo_version is None and "cargo " in command:
        issues.append(PreflightIssue(
            level="error",
            message="Cargo/Rust is not installed or not in PATH",
            suggestion="Install Rust from https://rustup.rs",
        ))
    
    # Check for Cargo.toml
    if "cargo " in command:
        cwd = Path.cwd()
        if not (cwd / "Cargo.toml").exists():
            issues.append(PreflightIssue(
                level="warning",
                message="No Cargo.toml found in current directory",
                suggestion="Ensure you're in a Rust project directory",
            ))
    
    return issues


def _go_preflight(command: str) -> List[PreflightIssue]:
    """Check for common Go issues before running a command."""
    issues: List[PreflightIssue] = []
    
    # Check if go is installed
    go_version = _run_quick_check("go version 2>/dev/null")
    if go_version is None and "go " in command:
        issues.append(PreflightIssue(
            level="error",
            message="Go is not installed or not in PATH",
            suggestion="Install Go from https://go.dev",
        ))
    
    # Check for go.mod
    if "go " in command and "go.mod" not in command:
        cwd = Path.cwd()
        if not (cwd / "go.mod").exists():
            issues.append(PreflightIssue(
                level="info",
                message="No go.mod found in current directory",
                suggestion="This may not be a Go module. Run 'go mod init' if needed",
            ))
    
    return issues


def _generic_preflight(command: str) -> List[PreflightIssue]:
    """Generic preflight checks that apply to all commands."""
    issues: List[PreflightIssue] = []
    
    # Check for common shell typos
    common_typos = [
        (r"^sl\s", "sl", "ls"),
        (r"^ls\s+-l\s+[^/]", "ls -l path", "Check path argument"),
        (r"^cat\s+[^.]", "cat filename", "Check file extension"),
    ]
    
    # Check for rm without flags (dangerous)
    if re.search(r"\brm\s+\S", command) and "-i" not in command and "-rf" not in command:
        # Check if user is rm-ing an important directory
        rm_match = re.search(r"rm\s+([^\s]+)", command)
        if rm_match:
            target = rm_match.group(1)
            if target in ["/", "/usr", "/etc", "/home", "/var"]:
                issues.append(PreflightIssue(
                    level="error",
                    message=f"Dangerous rm target: {target}",
                    suggestion="This will delete critical system files. Use --skip-preflight to force",
                ))
    
    # Check for sudo
    if "sudo " in command:
        issues.append(PreflightIssue(
            level="warning",
            message="Command uses sudo - may require elevated permissions",
            suggestion="Ensure you have the necessary permissions",
        ))
    
    return issues


def _run_quick_check(cmd: str, timeout: int = 5) -> Optional[str]:
    """Run a quick shell command and return stripped stdout, or None on failure."""
    import subprocess
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        # Return stderr for error checking (like Docker daemon check)
        return result.stderr.strip() if result.stderr else None
    except Exception:
        return None


# ── Main preflight function ────────────────────────────────────────────────────


def run_preflight(command: str) -> PreflightResult:
    """Run all preflight checks for a given command.
    
    Args:
        command: The shell command to check.
        
    Returns:
        A PreflightResult with any issues found.
    """
    all_issues: List[PreflightIssue] = []
    
    # Get matching plugin
    plugin_name = find_matching_plugin(command)
    
    # Run plugin-specific checks
    if plugin_name == "npm":
        all_issues.extend(_npm_preflight(command))
    elif plugin_name == "docker":
        all_issues.extend(_docker_preflight(command))
    elif plugin_name == "git":
        all_issues.extend(_git_preflight(command))
    elif plugin_name == "python":
        all_issues.extend(_python_preflight(command))
    elif plugin_name == "rust":
        all_issues.extend(_rust_preflight(command))
    elif plugin_name == "go":
        all_issues.extend(_go_preflight(command))
    
    # Run generic checks for all commands
    all_issues.extend(_generic_preflight(command))
    
    return PreflightResult.create(all_issues)


def format_preflight_result(result: PreflightResult) -> str:
    """Format preflight result for display.
    
    Args:
        result: The preflight result to format.
        
    Returns:
        A formatted string for display.
    """
    if not result.has_issues:
        return ""
    
    lines = []
    if result.errors:
        lines.append("[red]❌ Errors:[/red]")
        for issue in result.errors:
            lines.append(f"  [red]• {issue.message}[/red]")
            if issue.suggestion:
                lines.append(f"    [dim]{issue.suggestion}[/dim]")
    
    if result.warnings:
        lines.append("[yellow]⚠️  Warnings:[/yellow]")
        for issue in result.warnings:
            lines.append(f"  [yellow]• {issue.message}[/yellow]")
            if issue.suggestion:
                lines.append(f"    [dim]{issue.suggestion}[/dim]")
    
    if result.infos:
        lines.append("[blue]ℹ️  Info:[/blue]")
        for issue in result.infos:
            lines.append(f"  [blue]• {issue.message}[/blue]")
            if issue.suggestion:
                lines.append(f"    [dim]{issue.suggestion}[/dim]")
    
    return "\n".join(lines)