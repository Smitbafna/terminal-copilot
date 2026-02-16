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

from terminal_copilot.plugins import (
    find_matching_plugin,
    get_plugin_by_name,
    detect_project_type,
    _detect_framework,
    validate_environment,
    _find_project_root,
    PreflightCheck,
)


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


# ── Helper functions ──────────────────────────────────────────────────────────


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


def _detect_git_merge_conflicts() -> bool:
    """Detect if there are git merge conflicts in the working directory.
    
    Returns True if merge conflicts detected, False otherwise.
    """
    # Check for conflict markers in files
    conflict_patterns = [
        re.compile(r"^<<<<<<<", re.MULTILINE),
        re.compile(r"^========", re.MULTILINE),
        re.compile(r"^>>>>>>>", re.MULTILINE),
    ]
    
    # Search for conflicted files using git diff
    conflicted_files = _run_quick_check(
        "git diff --name-only --diff-filter=U 2>/dev/null"
    )
    if conflicted_files:
        return True
    
    return False


def _detect_port_in_use(port: int) -> bool:
    """Detect if a port is already in use.

    Args:
        port: The port number to check.
        
    Returns True if port is in use, False otherwise.
    """
    # Try using ss (more common on Linux) or netstat as fallback
    result = _run_quick_check(f"ss -tln 2>/dev/null | grep -q ':{port} '")
    if result is not None or _run_quick_check(f"netstat -tln 2>/dev/null | grep -q ':{port} '"):
        return True
    
    # Try lsof as another fallback
    if _run_quick_check(f"lsof -i :{port} 2>/dev/null"):
        return True
    
    return False


def _get_common_dev_ports() -> List[int]:
    """Get common development ports for web applications.

    Returns a list of common port numbers used by dev servers.
    """
    return [3000, 3001, 4000, 5000, 5173, 8000, 8080, 9000]


# ── Plugin-specific preflight functions (for command-specific checks) ─────────────


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
    
    # Check for common typos (use regex to match exact typo without triggering on correct command)
    if re.search(r"npm\s+instal(?!l)", command):  # Typo: missing 'l'
        issues.append(PreflightIssue(
            level="error",
            message="Possible typo: 'npm instal' should be 'npm install'",
            suggestion="Correct the command to: npm install",
        ))
    
    if re.search(r"npm\s+uninstal(?!l)", command):  # Typo
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


# ── Main preflight function ────────────────────────────────────────────────────


def run_preflight(command: str) -> PreflightResult:
    """Run all preflight checks for a given command.
    
    Args:
        command: The shell command to check.
        
    Returns:
        A PreflightResult with any issues found.
    """
    all_issues: List[PreflightIssue] = []
    
    # Get matching plugin and run its checks
    plugin_name = find_matching_plugin(command)
    plugin = get_plugin_by_name(plugin_name)
    
    if plugin:
        plugin_checks = plugin.preflight_checks()
        for check in plugin_checks:
            if not check.passed:
                all_issues.append(PreflightIssue(
                    level=check.level,
                    message=check.message,
                    suggestion=check.suggestion,
                ))
    
    # Run plugin-specific checks (for command-specific validations)
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


def _check_env_local(cwd: Path) -> Optional[str]:
    """Check for missing .env.local file in Node.js projects.
    
    Returns warning message if .env.local missing but .env exists, None otherwise.
    """
    env_file = cwd / ".env"
    env_local_file = cwd / ".env.local"
    
    # Only warn if .env exists but .env.local doesn't (Next.js convention)
    if env_file.exists() and not env_local_file.exists():
        return ".env.local missing"
    return None


def _npm_prediction(command: str) -> List[PreflightIssue]:
    """Prediction checks for npm commands showing potential issues before execution."""
    issues: List[PreflightIssue] = []
    cwd = Path.cwd()
    package_json = cwd / "package.json"
    
    # Check for .env.local missing (Next.js convention)
    if package_json.exists():
        env_warning = _check_env_local(cwd)
        if env_warning:
            issues.append(PreflightIssue(
                level="warning",
                message=env_warning,
                suggestion="Create .env.local for environment variables or ensure .env has required vars",
            ))
    
    # Check node version mismatch
    node_version = validate_environment(cwd)
    if "node_version" in node_version:
        issues.append(PreflightIssue(
            level="warning",
            message=node_version["node_version"],
            suggestion=None,
        ))
    
    return issues


def predict_potential_issues(command: str) -> List[PreflightIssue]:
    """Predict potential issues for a command before execution.
    
    This function checks for issues that might cause the command to fail or behave
    unexpectedly, without blocking on errors. It's meant for the preview feature.

    Args:
        command: The shell command to check.
        
    Returns:
        A list of PreflightIssue with potential problems.
    """
    issues: List[PreflightIssue] = []
    cwd = Path.cwd()
    
    plugin_name = find_matching_plugin(command)
    
    if plugin_name == "npm":
        project_root = _find_project_root()
        framework = _detect_framework(project_root)
        
        # Collect prediction-specific issues
        issues.extend(_npm_prediction(command))
        
        # Check node_modules missing for npm run commands
        if re.match(r"^(npm|yarn|pnpm)\s+run\s+", command):
            if (project_root / "package-lock.json").exists() and not (project_root / "node_modules").is_dir():
                issues.append(PreflightIssue(
                    level="warning",
                    message="node_modules directory is missing",
                    suggestion="Run 'npm install' before starting the dev server",
                ))
    
    return issues


# ── Intelligent Warnings ───────────────────────────────────────────────────────


def run_intelligent_warnings(command: str) -> List[PreflightIssue]:
    """Run intelligent warnings that predict issues before they happen.
    
    These warnings proactively detect potential problems like:
    - Git merge conflicts
    - Docker daemon not running
    - Port already in use
    
    Args:
        command: The shell command to check.
        
    Returns:
        A list of PreflightIssue with potential problems.
    """
    issues: List[PreflightIssue] = []
    
    # Check git merge conflicts for any command in a git repo
    git_root = _run_quick_check("git rev-parse --show-toplevel 2>/dev/null")
    if git_root:
        if _detect_git_merge_conflicts():
            issues.append(PreflightIssue(
                level="warning",
                message="Git has merge conflicts in your working directory",
                suggestion="This build is likely to fail. Resolve conflicts before proceeding.",
            ))
    
    # Check Docker daemon for docker commands
    if "docker " in command:
        docker_ps = _run_quick_check("docker ps 2>&1", timeout=5)
        if docker_ps and "Cannot connect to the Docker daemon" in docker_ps:
            issues.append(PreflightIssue(
                level="warning",
                message="Docker daemon is not running",
                suggestion="Start Docker: 'systemctl start docker' or launch Docker Desktop",
            ))
    
    # Check port in use for dev server commands
    if re.search(r"(start|dev|serve|run\s+develop)", command):
        for port in _get_common_dev_ports():
            if _detect_port_in_use(port):
                issues.append(PreflightIssue(
                    level="warning",
                    message=f"Port {port} is already in use",
                    suggestion=f"Another process is using port {port}. Check with 'lsof -i :{port}' or kill the process.",
                ))
                break  # Only report the first conflicting port
    
    return issues


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