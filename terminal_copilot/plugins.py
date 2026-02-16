"""Lightweight plugin interface for command detection and context collection."""

from __future__ import annotations

import json
import os
import re
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

# Framework detection markers
FRAMEWORK_MARKERS: Dict[str, str] = {
    "next": "next.config.js",
    "astro": "astro.config.mjs",
    "nuxt": "nuxt.config.js",
    "remix": "remix.config.js",
    "sveltekit": "svelte.config.js",
    "gatsby": "gatsby-config.js",
    "vite": "vite.config.js",
    "vue": "vue.config.js",
}


def _detect_framework(project_root: Path) -> Optional[str]:
    """Detect framework from package.json dependencies or framework config files.
    
    Returns the framework name (e.g., "Next.js") if detected, None otherwise.
    """
    # First check for framework-specific config files
    for framework, marker in FRAMEWORK_MARKERS.items():
        if (project_root / marker).exists():
            return framework.capitalize() + (".js" if framework == "next" or framework == "astro" or framework == "nuxt" else "")
    
    # Check package.json for framework dependencies
    package_json = project_root / "package.json"
    if package_json.exists():
        try:
            with open(package_json) as f:
                pkg = json.load(f)
            
            deps = pkg.get("dependencies", {}) | pkg.get("devDependencies", {})
            dep_names = {name.lower() for name in deps.keys()}
            
            # Check for Next.js
            if "next" in dep_names:
                return "Next.js"
            # Check for Astro
            if "astro" in dep_names:
                return "Astro"
            # Check for Nuxt
            if "nuxt" in dep_names or "@nuxt/ui" in dep_names:
                return "Nuxt"
            # Check for Remix
            if "@remix-run" in dep_names:
                return "Remix"
            # Check for SvelteKit
            if "sveltekit" in dep_names or "@sveltejs/kit" in dep_names:
                return "SvelteKit"
            # Check for Gatsby
            if "gatsby" in dep_names:
                return "Gatsby"
        except (json.JSONDecodeError, OSError):
            pass
    
    return None


def _parse_version(version_str: str) -> Optional[int]:
    """Parse a version string like 'v18.0.0' or '18.0.0' to major version int."""
    import re
    match = re.search(r"(\d+)", version_str)
    if match:
        return int(match.group(1))
    return None


def _check_node_version(project_root: Path) -> Optional[str]:
    """Check Node.js version compatibility against package.json engines field.

    Returns warning message if version mismatch detected, None otherwise.
    """
    package_json = project_root / "package.json"
    if not package_json.exists():
        return None

    try:
        with open(package_json) as f:
            pkg = json.load(f)

        engines = pkg.get("engines", {})
        node_req = engines.get("node", "")
        if not node_req:
            return None

        # Parse required version (supports >=, >, ^, etc.)
        import re
        match = re.search(r"(\d+)", node_req)
        if not match:
            return None

        required_major = int(match.group(1))

        # Get installed version
        installed = _run_quick_command("node --version 2>/dev/null")
        if not installed:
            return "Node.js is not installed"

        installed_major = _parse_version(installed)
        if installed_major is None:
            return None

        if installed_major < required_major:
            return (
                f"Version mismatch detected: package.json requires Node {node_req}, "
                f"but installed Node is {installed}"
            )
        return None
    except (json.JSONDecodeError, OSError):
        return None


def _check_python_version(project_root: Path) -> Optional[str]:
    """Check Python version compatibility against pyproject.toml or setup.py.

    Returns warning message if version mismatch detected, None otherwise.
    """
    # Check pyproject.toml first (PEP 621)
    pyproject = project_root / "pyproject.toml"
    if pyproject.exists():
        try:
            with open(pyproject) as f:
                content = f.read()

            # Look for python_requires
            match = re.search(r"python_requires\s*=\s*[\"']([^\"']+)[\"']", content)
            if match:
                req_str = match.group(1)
                # Parse required version (supports >=3.12, >3.10, etc.)
                req_match = re.search(r"(\d+)\.(\d+)", req_str)
                if req_match:
                    required_major = int(req_match.group(1))
                    required_minor = int(req_match.group(2))

                    import sys
                    current = sys.version_info
                    current_major = current.major
                    current_minor = current.minor

                    if (current_major, current_minor) < (required_major, required_minor):
                        return (
                            f"Version mismatch detected: pyproject.toml requires Python "
                            f"{req_str}, but current Python is {current_major}.{current_minor}"
                        )
        except OSError:
            pass

    return None


def _check_docker_daemon(project_root: Path) -> Optional[str]:
    """Check if Docker daemon is running when Docker-related files exist.

    Returns warning message if daemon not running, None otherwise.
    """
    has_docker_files = (
        (project_root / "Dockerfile").exists() or
        (project_root / "docker-compose.yml").exists() or
        (project_root / "docker-compose.yaml").exists()
    )

    if not has_docker_files:
        return None

    docker_ps = _run_quick_command("docker ps 2>&1")
    if docker_ps and "Cannot connect to the Docker daemon" in docker_ps:
        return "Dockerfile exists but Docker daemon is not running"

    return None


def _check_node_modules(project_root: Path) -> Optional[str]:
    """Check for missing node_modules directory.

    Returns warning message if node_modules missing but package.json/lock present, None otherwise.
    """
    package_json = project_root / "package.json"
    if not package_json.exists():
        return None

    # Check if lockfile exists (indicates dependencies have been installed)
    has_lockfile = (
        (project_root / "package-lock.json").exists() or
        (project_root / "pnpm-lock.yaml").exists() or
        (project_root / "yarn.lock").exists()
    )

    has_node_modules = (project_root / "node_modules").is_dir()

    if has_lockfile and not has_node_modules:
        return "package.json exists but node_modules directory is missing (run npm install)"

    return None


def _check_cargo_lock(project_root: Path) -> Optional[str]:
    """Check for missing Cargo.lock for Rust projects.

    Returns warning message if Cargo.lock missing but Cargo.toml present, None otherwise.
    """
    cargo_toml = project_root / "Cargo.toml"
    if not cargo_toml.exists():
        return None

    has_cargo_lock = (project_root / "Cargo.lock").exists()
    has_target = (project_root / "target").is_dir()

    if not has_cargo_lock and not has_target:
        return "Cargo.toml exists but Cargo.lock and target directory are missing (run cargo fetch)"

    return None


def validate_environment(cwd: Optional[Path] = None) -> Dict[str, str]:
    """Validate the environment against detected project requirements.

    Walks up from cwd to find project root, detects project type, and checks
    for version compatibility, runtime environment issues, and missing dependencies.

    Args:
        cwd: The working directory to start from. Defaults to current directory.

    Returns:
        A dictionary with warning messages for each detected issue.
    """
    warnings: Dict[str, str] = {}
    project_root = _find_project_root(cwd)
    project_type = detect_project_type(cwd)

    if project_type == "Node":
        warning = _check_node_version(project_root)
        if warning:
            warnings["node_version"] = warning
        warning = _check_node_modules(project_root)
        if warning:
            warnings["node_modules"] = warning

    elif project_type == "Rust":
        warning = _check_cargo_lock(project_root)
        if warning:
            warnings["cargo_lock"] = warning

    elif project_type == "Python":
        warning = _check_python_version(project_root)
        if warning:
            warnings["python_version"] = warning

    elif project_type in ("Docker", "Docker Compose"):
        warning = _check_docker_daemon(project_root)
        if warning:
            warnings["docker_daemon"] = warning

    return warnings


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


class PreflightCheck:
    """A single preflight check result."""
    
    def __init__(self, passed: bool, message: str, suggestion: Optional[str] = None):
        self.passed = passed
        self.message = message
        self.suggestion = suggestion
    
    @property
    def status(self) -> str:
        """Return the status icon for display."""
        return "✓" if self.passed else "⚠"
    
    @property
    def level(self) -> str:
        """Return the level for compatibility with PreflightIssue."""
        return "info" if self.passed else "warning"


class BasePlugin(ABC):
    """Base class for all plugins.

    Each plugin can determine whether it supports a given command,
    collect structured context from the environment, and run preflight checks.
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

    def preflight_checks(self, cwd: Optional[Path] = None) -> List[PreflightCheck]:
        """Run preflight checks for this plugin's domain.

        Args:
            cwd: The working directory to check. Defaults to current directory.

        Returns:
            A list of PreflightCheck results for each check performed.
        """
        return []

    def available_tools(self) -> List[Dict[str, Any]]:
        """Return the subset of global tools relevant to this plugin.

        Returns:
            A list of tool schemas (name, description, args_schema) that
            are relevant for diagnosing issues with this plugin's domain.
        """
        return []


class NpmPlugin(BasePlugin):
    """Plugin for npm/pnpm/yarn commands."""

    name = "npm"

    def supports(self, command: str) -> bool:
        stripped = command.strip()
        return (
            stripped.startswith("npm ")
            or stripped.startswith("pnpm ")
            or stripped.startswith("yarn ")
            or stripped.startswith("npx ")
        )

    def preflight_checks(self, cwd: Optional[Path] = None) -> List[PreflightCheck]:
        """Run preflight checks for Node.js/npm projects."""
        checks: List[PreflightCheck] = []
        cwd_path = cwd or Path.cwd()
        project_root = _find_project_root(cwd_path)

        # Check package.json
        package_json = project_root / "package.json"
        checks.append(PreflightCheck(
            passed=package_json.exists(),
            message="package.json",
            suggestion=None if package_json.exists() else "Run 'npm init' to create a package.json",
        ))

        # Check Node version
        node_version = _run_quick_command("node --version 2>/dev/null")
        checks.append(PreflightCheck(
            passed=node_version is not None,
            message=f"node {node_version or 'not installed'}",
            suggestion="Install Node.js from https://nodejs.org" if not node_version else None,
        ))

        # Check npm version
        npm_version = _run_quick_command("npm --version 2>/dev/null")
        checks.append(PreflightCheck(
            passed=npm_version is not None,
            message=f"npm {npm_version or 'not installed'}",
            suggestion="Install Node.js from https://nodejs.org" if not npm_version else None,
        ))

        # Check lockfile
        has_lockfile = (
            (project_root / "package-lock.json").exists()
            or (project_root / "pnpm-lock.yaml").exists()
            or (project_root / "yarn.lock").exists()
        )
        checks.append(PreflightCheck(
            passed=has_lockfile,
            message="lockfile",
            suggestion="Run 'npm install' or your package manager's install command to create a lockfile",
        ))

        # Check node_modules
        has_node_modules = (project_root / "node_modules").is_dir()
        checks.append(PreflightCheck(
            passed=has_node_modules,
            message="node_modules",
            suggestion="Run 'npm install' to install dependencies",
        ))

        return checks

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

    def preflight_checks(self, cwd: Optional[Path] = None) -> List[PreflightCheck]:
        """Run preflight checks for Docker projects."""
        checks: List[PreflightCheck] = []

        # Check Docker installed
        docker_version = _run_quick_command("docker --version 2>/dev/null")
        checks.append(PreflightCheck(
            passed=docker_version is not None,
            message=f"Docker installed {docker_version or ''}",
            suggestion="Install Docker from https://docker.com" if not docker_version else None,
        ))

        # Check Docker daemon running (only if Docker is installed)
        if docker_version:
            docker_ps = _run_quick_command("docker ps 2>&1", timeout=5)
            daemon_running = docker_ps is None or "Cannot connect to the Docker daemon" not in (docker_ps or "")
            checks.append(PreflightCheck(
                passed=daemon_running,
                message="daemon running",
                suggestion="Start Docker: 'systemctl start docker' or launch Docker Desktop",
            ))

        # Check Dockerfile
        cwd_path = cwd or Path.cwd()
        project_root = _find_project_root(cwd_path)
        dockerfile_exists = _file_exists(project_root / "Dockerfile")
        checks.append(PreflightCheck(
            passed=dockerfile_exists,
            message="Dockerfile",
            suggestion=None if dockerfile_exists else "Create a Dockerfile for containerized builds",
        ))

        # Check compose plugin (for docker-compose.yml/docker-compose.yaml)
        compose_exists = (
            _file_exists(project_root / "docker-compose.yml")
            or _file_exists(project_root / "docker-compose.yaml")
        )
        checks.append(PreflightCheck(
            passed=compose_exists,
            message="compose plugin",
            suggestion="Install docker-compose: 'pip install docker-compose' or use 'docker compose'" 
                if not compose_exists
                else None,
        ))

        return checks

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

    def preflight_checks(self, cwd: Optional[Path] = None) -> List[PreflightCheck]:
        """Run preflight checks for git commands."""
        checks: List[PreflightCheck] = []

        # Check Git installed
        git_version = _run_quick_command("git --version 2>/dev/null")
        checks.append(PreflightCheck(
            passed=git_version is not None,
            message=f"Git installed {git_version or ''}",
            suggestion="Install Git from https://git-scm.com" if not git_version else None,
        ))

        # Check we're in a git repo
        git_root = _run_quick_command("git rev-parse --show-toplevel 2>/dev/null")
        checks.append(PreflightCheck(
            passed=git_root is not None,
            message="in git repository",
            suggestion="Run 'git init' to initialize a repository or check your directory",
        ))

        return checks

    def collect_context(
        self, command: str, cwd: Optional[Path] = None
    ) -> Dict[str, Any]:
        cwd_path = cwd or Path.cwd()
        context: Dict[str, Any] = {}

        context["git_version"] = _run_quick_command("git --version")

        # Check if we're in a git repo
        git_root = _run_quick_command("git rev-parse --show-toplevel 2>/dev/null")
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


# ── Compilation / Build Plugins ───────────────────────────────────────────────


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

    def preflight_checks(self, cwd: Optional[Path] = None) -> List[PreflightCheck]:
        """Run preflight checks for C/C++ projects."""
        checks: List[PreflightCheck] = []

        # Check gcc
        gcc_version = _run_quick_command("gcc --version 2>/dev/null | head -1")
        checks.append(PreflightCheck(
            passed=gcc_version is not None,
            message="gcc installed",
            suggestion="Install gcc for C compilation" if not gcc_version else None,
        ))

        # Check g++
        gpp_version = _run_quick_command("g++ --version 2>/dev/null | head -1")
        checks.append(PreflightCheck(
            passed=gpp_version is not None,
            message="g++ installed",
            suggestion="Install g++ for C++ compilation" if not gpp_version else None,
        ))

        # Check make
        make_version = _run_quick_command("make --version 2>/dev/null | head -1")
        checks.append(PreflightCheck(
            passed=make_version is not None,
            message="make installed",
            suggestion="Install make for build automation" if not make_version else None,
        ))

        return checks

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

    def preflight_checks(self, cwd: Optional[Path] = None) -> List[PreflightCheck]:
        """Run preflight checks for Rust projects."""
        checks: List[PreflightCheck] = []

        # Check rustc
        rustc_version = _run_quick_command("rustc --version 2>/dev/null")
        checks.append(PreflightCheck(
            passed=rustc_version is not None,
            message=f"rustc {rustc_version or 'not installed'}",
            suggestion="Install Rust from https://rustup.rs" if not rustc_version else None,
        ))

        # Check cargo
        cargo_version = _run_quick_command("cargo --version 2>/dev/null")
        checks.append(PreflightCheck(
            passed=cargo_version is not None,
            message=f"cargo {cargo_version or 'not installed'}",
            suggestion="Install Rust from https://rustup.rs" if not cargo_version else None,
        ))

        # Check Cargo.toml exists
        cwd_path = cwd or Path.cwd()
        project_root = _find_project_root(cwd_path)
        cargo_toml_exists = _file_exists(project_root / "Cargo.toml")
        checks.append(PreflightCheck(
            passed=cargo_toml_exists,
            message="Cargo.toml",
            suggestion="Ensure you're in a Rust project directory or run 'cargo init'",
        ))

        return checks

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

    def preflight_checks(self, cwd: Optional[Path] = None) -> List[PreflightCheck]:
        """Run preflight checks for Go projects."""
        checks: List[PreflightCheck] = []

        # Check Go installed
        go_version = _run_quick_command("go version 2>/dev/null")
        checks.append(PreflightCheck(
            passed=go_version is not None,
            message=f"Go installed {go_version or ''}",
            suggestion="Install Go from https://go.dev" if not go_version else None,
        ))

        # Check go.mod exists
        cwd_path = cwd or Path.cwd()
        project_root = _find_project_root(cwd_path)
        go_mod_exists = _file_exists(project_root / "go.mod")
        checks.append(PreflightCheck(
            passed=go_mod_exists,
            message="go.mod",
            suggestion="Run 'go mod init' to initialize a Go module" if not go_mod_exists else None,
        ))

        return checks

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

    def preflight_checks(self, cwd: Optional[Path] = None) -> List[PreflightCheck]:
        """Run preflight checks for Python projects."""
        checks: List[PreflightCheck] = []

        # Check Python installed
        python_version = _run_quick_command("python3 --version 2>/dev/null")
        checks.append(PreflightCheck(
            passed=python_version is not None,
            message=f"Python {python_version or 'not installed'}",
            suggestion="Install Python from https://python.org" if not python_version else None,
        ))

        # Check virtual environment
        in_venv = os.environ.get("VIRTUAL_ENV") or os.environ.get("PIP_REQUIRE_VIRTUALENV")
        checks.append(PreflightCheck(
            passed=bool(in_venv),
            message="virtual environment",
            suggestion="Consider using a virtual environment: 'python -m venv venv && source venv/bin/activate'",
        ))

        return checks

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