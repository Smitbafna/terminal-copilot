"""Lightweight plugin interface for command detection and context collection."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional


def _run_quick_command(cmd: str, timeout: int = 5) -> Optional[str]:
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
            return result.stdout.strip()
        return None
    except Exception:
        return None


def _file_exists(path: Path) -> bool:
    return path.exists()


def _find_project_root(cwd: Optional[Path] = None) -> Path:
    """Walk up from cwd to find a project root (has .git, .gitignore, etc.)."""
    current = cwd or Path.cwd().resolve()
    for parent in [current] + list(current.parents):
        if (parent / ".git").exists():
            return parent
    return current


class BasePlugin(ABC):
    """Base class for all plugins.

    Each plugin can determine whether it supports a given command,
    and collect structured context from the environment.
    """

    name: str = ""

    @abstractmethod
    def supports(self, command: str) -> bool:
        """Check if this plugin can handle the given command.

        Args:
            command: The shell command to check.

        Returns:
            True if this plugin supports the command, False otherwise.
        """
        ...

    def collect_context(self, command: str, cwd: Optional[Path] = None) -> Dict[str, Any]:
        """Collect structured context from the environment for this plugin.

        Args:
            command: The shell command that was executed.
            cwd: The working directory to collect context from.

        Returns:
            A dict with structured context information.
        """
        return {}


class NpmPlugin(BasePlugin):
    """Plugin for npm/pnpm commands."""

    name = "npm"

    def supports(self, command: str) -> bool:
        stripped = command.strip()
        return (
            stripped.startswith("npm ")
            or stripped.startswith("pnpm ")
            or stripped.startswith("yarn ")
            or stripped.startswith("npx ")
        )

    def collect_context(
        self, command: str, cwd: Optional[Path] = None
    ) -> Dict[str, Any]:
        cwd_path = cwd or Path.cwd()
        project_root = _find_project_root(cwd_path)
        context: Dict[str, Any] = {}

        # Node & npm versions
        context["node_version"] = _run_quick_command("node --version")
        context["npm_version"] = _run_quick_command("npm --version")
        context["pnpm_version"] = _run_quick_command("pnpm --version 2>/dev/null")
        context["yarn_version"] = _run_quick_command("yarn --version 2>/dev/null")

        # Project files
        context["project_root"] = str(project_root)
        context["cwd"] = str(cwd_path)
        context["package_json_exists"] = _file_exists(project_root / "package.json")
        context["pnpm_lock_exists"] = _file_exists(project_root / "pnpm-lock.yaml")
        context["package_lock_exists"] = _file_exists(project_root / "package-lock.json")
        context["yarn_lock_exists"] = _file_exists(project_root / "yarn.lock")

        # Read package.json if it exists
        pkg_json_path = project_root / "package.json"
        if pkg_json_path.exists():
            try:
                with open(pkg_json_path) as f:
                    pkg = json.load(f)
                context["package_name"] = pkg.get("name", "")
                context["package_scripts"] = list(pkg.get("scripts", {}).keys())
                context["package_dependencies"] = (
                    list(pkg.get("dependencies", {}).keys())
                    if "dependencies" in pkg
                    else []
                )
                context["package_dev_dependencies"] = (
                    list(pkg.get("devDependencies", {}).keys())
                    if "devDependencies" in pkg
                    else []
                )
            except (json.JSONDecodeError, OSError):
                context["package_json_parse_error"] = True

        return context


class DockerPlugin(BasePlugin):
    """Plugin for docker commands."""

    name = "docker"

    def supports(self, command: str) -> bool:
        return command.strip().startswith("docker ")

    def collect_context(
        self, command: str, cwd: Optional[Path] = None
    ) -> Dict[str, Any]:
        cwd_path = cwd or Path.cwd()
        project_root = _find_project_root(cwd_path)
        context: Dict[str, Any] = {}

        context["docker_version"] = _run_quick_command("docker --version")

        # Check for common Docker files
        context["dockerfile_exists"] = _file_exists(project_root / "Dockerfile")
        context["compose_exists"] = _file_exists(project_root / "docker-compose.yml") or _file_exists(
            project_root / "docker-compose.yaml"
        )
        context["dockerignore_exists"] = _file_exists(project_root / ".dockerignore")

        # Running containers and images
        ps_output = _run_quick_command("docker ps --format '{{.ID}} {{.Image}} {{.Status}}'")
        context["running_containers"] = (
            [line.strip() for line in ps_output.split("\n") if line.strip()]
            if ps_output
            else []
        )

        images_output = _run_quick_command("docker images --format '{{.Repository}}:{{.Tag}}'")
        context["images"] = (
            [line.strip() for line in images_output.split("\n") if line.strip()]
            if images_output
            else []
        )

        context["project_root"] = str(project_root)
        context["cwd"] = str(cwd_path)

        return context


class GitPlugin(BasePlugin):
    """Plugin for git commands."""

    name = "git"

    def supports(self, command: str) -> bool:
        return command.strip().startswith("git ")

    def collect_context(
        self, command: str, cwd: Optional[Path] = None
    ) -> Dict[str, Any]:
        cwd_path = cwd or Path.cwd()
        context: Dict[str, Any] = {}

        context["git_version"] = _run_quick_command("git --version")

        # Check if we're in a git repo
        git_root = _run_quick_command("git rev-parse --show-toplevel")
        if git_root:
            context["in_git_repo"] = True
            context["git_root"] = git_root
            context["current_branch"] = _run_quick_command(
                "git rev-parse --abbrev-ref HEAD"
            )
            context["last_commit"] = _run_quick_command(
                'git log -1 --oneline 2>/dev/null'
            )
            context["last_commit_full"] = _run_quick_command(
                'git log -1 --format="%H %ai %s" 2>/dev/null'
            )
            context["status"] = _run_quick_command(
                "git status --short 2>/dev/null"
            )
            context["modified_files"] = (
                [
                    line.strip()
                    for line in (context.get("status") or "").split("\n")
                    if line.strip()
                ]
            )
            context["ahead_behind"] = _run_quick_command(
                "git rev-list --count --left-right @{upstream}...HEAD 2>/dev/null || echo ''"
            )
        else:
            context["in_git_repo"] = False

        context["cwd"] = str(cwd_path)

        return context


def get_all_plugins() -> List[BasePlugin]:
    """Return all available plugins."""
    return [NpmPlugin(), DockerPlugin(), GitPlugin()]


def find_matching_plugin(command: str) -> str:
    """Find the name of the first plugin that supports the given command.

    Args:
        command: The shell command to check.

    Returns:
        The name of the matching plugin, or "unknown" if no plugin matches.
    """
    for plugin in get_all_plugins():
        if plugin.supports(command):
            return plugin.name
    return "unknown"


def get_plugin_by_name(name: str) -> Optional[BasePlugin]:
    """Get a plugin instance by its name.

    Args:
        name: The name of the plugin to find.

    Returns:
        The plugin instance, or None if not found.
    """
    for plugin in get_all_plugins():
        if plugin.name == name:
            return plugin
    return None