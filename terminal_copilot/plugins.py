"""Lightweight plugin interface for command detection and context collection."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from terminal_copilot.tools import get_tools_schema


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


# Mapping of project types to their marker files
PROJECT_MARKERS: Dict[str, List[str]] = {
    "Node": ["package.json"],
    "Rust": ["Cargo.toml"],
    "Go": ["go.mod"],
    "Python": ["requirements.txt", "pyproject.toml"],
    "Docker": ["Dockerfile"],
    "Docker Compose": ["docker-compose.yml", "docker-compose.yaml"],
}


def detect_project_type(cwd: Optional[Path] = None) -> str:
    """Detect the project type based on marker files.

    Walks up from the current directory to find project root, then checks
    for known project marker files to determine the project type.

    Args:
        cwd: The working directory to start from. Defaults to current directory.

    Returns:
        The detected project type (e.g., "Node", "Rust", "Go", "Python", "Docker",
        "Docker Compose") or "Unknown" if no markers found.
    """
    project_root = _find_project_root(cwd)

    # Check each project type's markers
    for project_type, markers in PROJECT_MARKERS.items():
        for marker in markers:
            if (project_root / marker).exists():
                return project_type

    # Special case: Check for both Dockerfile and docker-compose.yml
    # Docker Compose takes precedence if both exist
    if (project_root / "docker-compose.yml").exists() or (project_root / "docker-compose.yaml").exists():
        return "Docker Compose"
    if (project_root / "Dockerfile").exists():
        return "Docker"

    return "Unknown"


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

    def available_tools(self) -> List[Dict[str, Any]]:
        """Return the subset of global tools relevant to this plugin.

        Returns:
            A list of tool schemas (name, description, args_schema) that
            are relevant for diagnosing issues with this plugin's domain.
        """
        return []


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


# ── Compilation / Build Plugins ─────────────────────────────────────────────


class CCompilePlugin(BasePlugin):
    """Plugin for C/C++ compilation commands (gcc, g++, clang, make, cmake)."""

    name = "compile"

    def supports(self, command: str) -> bool:
        stripped = command.strip()
        return (
            stripped.startswith("gcc ")
            or stripped.startswith("g++ ")
            or stripped.startswith("clang ")
            or stripped.startswith("clang++ ")
            or stripped.startswith("make ")
            or stripped.startswith("cmake ")
            or stripped.startswith("cc ")
            or stripped.startswith("c++ ")
        )

    def collect_context(
        self, command: str, cwd: Optional[Path] = None
    ) -> Dict[str, Any]:
        cwd_path = cwd or Path.cwd()
        project_root = _find_project_root(cwd_path)
        context: Dict[str, Any] = {}

        context["gcc_version"] = _run_quick_command("gcc --version 2>/dev/null | head -1")
        context["g++_version"] = _run_quick_command("g++ --version 2>/dev/null | head -1")
        context["clang_version"] = _run_quick_command("clang --version 2>/dev/null | head -1")
        context["make_version"] = _run_quick_command("make --version 2>/dev/null | head -1")
        context["cmake_version"] = _run_quick_command("cmake --version 2>/dev/null | head -1")

        # Check for common C/C++ project files
        context["makefile_exists"] = _file_exists(project_root / "Makefile") or _file_exists(project_root / "makefile")
        context["cmakelists_exists"] = _file_exists(project_root / "CMakeLists.txt")
        context["has_c_files"] = bool(list(project_root.glob("**/*.c"))[:5])
        context["has_cpp_files"] = bool(list(project_root.glob("**/*.cpp"))[:5])
        context["has_header_files"] = bool(list(project_root.glob("**/*.h"))[:5])
        context["has_headerpp_files"] = bool(list(project_root.glob("**/*.hpp"))[:5])

        context["project_root"] = str(project_root)
        context["cwd"] = str(cwd_path)

        return context


class RustPlugin(BasePlugin):
    """Plugin for Rust compilation commands (rustc, cargo)."""

    name = "rust"

    def supports(self, command: str) -> bool:
        stripped = command.strip()
        return (
            stripped.startswith("rustc ")
            or stripped.startswith("cargo ")
        )

    def collect_context(
        self, command: str, cwd: Optional[Path] = None
    ) -> Dict[str, Any]:
        cwd_path = cwd or Path.cwd()
        project_root = _find_project_root(cwd_path)
        context: Dict[str, Any] = {}

        context["rustc_version"] = _run_quick_command("rustc --version 2>/dev/null")
        context["cargo_version"] = _run_quick_command("cargo --version 2>/dev/null")

        # Cargo project structure
        cargo_toml_path = project_root / "Cargo.toml"
        context["cargo_toml_exists"] = _file_exists(cargo_toml_path)
        context["has_rust_files"] = bool(list(project_root.glob("**/*.rs"))[:10])

        if cargo_toml_path.exists():
            try:
                with open(cargo_toml_path) as f:
                    # Simple TOML key extraction (no toml parser dependency)
                    content = f.read()
                for line in content.split("\n"):
                    line = line.strip()
                    if line.startswith("name"):
                        context["crate_name"] = line.split("=")[-1].strip().strip('"')
                        break
            except OSError:
                pass

        context["project_root"] = str(project_root)
        context["cwd"] = str(cwd_path)

        return context


class GoPlugin(BasePlugin):
    """Plugin for Go compilation commands (go build, go run, go test, go fmt, etc)."""

    name = "go"

    def supports(self, command: str) -> bool:
        stripped = command.strip()
        return stripped.startswith("go ")

    def collect_context(
        self, command: str, cwd: Optional[Path] = None
    ) -> Dict[str, Any]:
        cwd_path = cwd or Path.cwd()
        project_root = _find_project_root(cwd_path)
        context: Dict[str, Any] = {}

        context["go_version"] = _run_quick_command("go version 2>/dev/null")

        # Go module info
        context["go_mod_exists"] = _file_exists(project_root / "go.mod")
        context["go_sum_exists"] = _file_exists(project_root / "go.sum")
        context["has_go_files"] = bool(list(project_root.glob("**/*.go"))[:10])

        if _file_exists(project_root / "go.mod"):
            try:
                with open(project_root / "go.mod") as f:
                    first_lines = [next(f).strip() for _ in range(3) if True]
                context["go_mod_summary"] = "\n".join(first_lines)
            except (StopIteration, OSError):
                pass

        context["project_root"] = str(project_root)
        context["cwd"] = str(cwd_path)

        return context


class PythonPlugin(BasePlugin):
    """Plugin for Python execution (python3, python) — catches syntax/runtime errors."""

    name = "python"

    def supports(self, command: str) -> bool:
        stripped = command.strip()
        return (
            stripped.startswith("python ")
            or stripped.startswith("python3 ")
            or stripped.startswith("python3.")
        )

    def collect_context(
        self, command: str, cwd: Optional[Path] = None
    ) -> Dict[str, Any]:
        cwd_path = cwd or Path.cwd()
        project_root = _find_project_root(cwd_path)
        context: Dict[str, Any] = {}

        context["python_version"] = _run_quick_command("python3 --version 2>/dev/null")

        # Virtual environment check
        in_venv = os.environ.get("VIRTUAL_ENV") or os.environ.get("PIP_REQUIRE_VIRTUALENV")
        context["in_virtualenv"] = bool(in_venv)
        context["venv_path"] = in_venv if in_venv else None

        # Project files
        context["requirements_txt_exists"] = _file_exists(project_root / "requirements.txt")
        context["setup_py_exists"] = _file_exists(project_root / "setup.py")
        context["pyproject_toml_exists"] = _file_exists(project_root / "pyproject.toml")
        context["has_python_files"] = bool(list(project_root.glob("**/*.py"))[:10])

        context["project_root"] = str(project_root)
        context["cwd"] = str(cwd_path)

        return context


def get_all_plugins() -> List[BasePlugin]:
    """Return all available plugins."""
    return [
        NpmPlugin(),
        DockerPlugin(),
        GitPlugin(),
        CCompilePlugin(),
        RustPlugin(),
        GoPlugin(),
        PythonPlugin(),
    ]


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