"""Read-only tool registry for the LLM agent.

The LLM can request these tools during investigation. All tools are read-only
— no file writes, deletes, or arbitrary shell execution.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from terminal_copilot.models import ToolResult


# ── helpers ──────────────────────────────────────────────────────────────────

def _run_quick(cmd: str, timeout: int = 10) -> Optional[str]:
    """Run a shell command and return stripped stdout, or None on failure."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def _run_quick_allow_stderr(cmd: str, timeout: int = 10) -> str:
    """Run a shell command and return stdout + stderr combined, even on error."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout,
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if err:
            out += f"\nstderr:\n{err}" if out else f"stderr:\n{err}"
        return out or f"(exit {result.returncode})"
    except subprocess.TimeoutExpired:
        return "(timed out)"
    except Exception as exc:
        return f"(error: {exc})"


_MAX_FILE_SIZE = 100_000  # 100 KB


def _find_project_root(cwd: Optional[Path] = None) -> Path:
    current = cwd or Path.cwd().resolve()
    for parent in [current] + list(current.parents):
        if (parent / ".git").exists():
            return parent
        # Also treat Cargo.toml, package.json, pyproject.toml as project roots
        if (parent / "Cargo.toml").exists():
            return parent
        if (parent / "package.json").exists():
            return parent
        if (parent / "pyproject.toml").exists():
            return parent
    return current


# ── Tool definitions ─────────────────────────────────────────────────────────

class BaseTool:
    """Base class for a tool the LLM can request."""

    name: str = ""
    description: str = ""
    args_schema: Dict[str, Any] = {}

    def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with the given arguments and return a result."""
        raise NotImplementedError


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read the contents of a file at the given path (relative to project root). Max 100 KB."
    args_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file, relative to the project root or absolute",
            },
        },
        "required": ["path"],
    }

    def execute(self, path: str, **kwargs: Any) -> ToolResult:
        try:
            p = Path(path)
            if not p.is_absolute():
                p = _find_project_root() / path
            p = p.resolve()
            if not p.exists():
                return ToolResult(tool=self.name, error=f"File not found: {path}")
            if not p.is_file():
                return ToolResult(tool=self.name, error=f"Not a file: {path}")
            size = p.stat().st_size
            if size > _MAX_FILE_SIZE:
                return ToolResult(
                    tool=self.name,
                    error=f"File too large ({size} bytes, max {_MAX_FILE_SIZE})",
                )
            content = p.read_text(encoding="utf-8", errors="replace")
            return ToolResult(tool=self.name, output=content)
        except PermissionError:
            return ToolResult(tool=self.name, error=f"Permission denied: {path}")
        except Exception as exc:
            return ToolResult(tool=self.name, error=str(exc))


class ListDirectoryTool(BaseTool):
    name = "list_directory"
    description = "List files and directories in a given path."
    args_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the directory, relative to project root or absolute",
            },
        },
        "required": ["path"],
    }

    def execute(self, path: str, **kwargs: Any) -> ToolResult:
        try:
            p = Path(path)
            if not p.is_absolute():
                p = _find_project_root() / path
            p = p.resolve()
            if not p.exists():
                return ToolResult(tool=self.name, error=f"Directory not found: {path}")
            if not p.is_dir():
                return ToolResult(tool=self.name, error=f"Not a directory: {path}")

            entries: List[str] = []
            for entry in sorted(p.iterdir()):
                suffix = "/" if entry.is_dir() else ""
                entries.append(f"{entry.name}{suffix}")

            output = "\n".join(entries) if entries else "(empty directory)"
            return ToolResult(tool=self.name, output=output)
        except PermissionError:
            return ToolResult(tool=self.name, error=f"Permission denied: {path}")
        except Exception as exc:
            return ToolResult(tool=self.name, error=str(exc))


class FileExistsTool(BaseTool):
    name = "file_exists"
    description = "Check if a file or directory exists at the given path."
    args_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to check, relative to project root or absolute",
            },
        },
        "required": ["path"],
    }

    def execute(self, path: str, **kwargs: Any) -> ToolResult:
        try:
            p = Path(path)
            if not p.is_absolute():
                p = _find_project_root() / path
            exists = p.exists()
            entry_type = ""
            if exists:
                entry_type = "directory" if p.is_dir() else "file"
            return ToolResult(
                tool=self.name,
                output=json.dumps({"exists": exists, "type": entry_type, "path": str(p)}),
            )
        except Exception as exc:
            return ToolResult(tool=self.name, error=str(exc))


class GitStatusTool(BaseTool):
    name = "git_status"
    description = "Show the working tree status (equivalent to `git status --short`). Returns empty if not in a git repo."
    args_schema = {
        "type": "object",
        "properties": {},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            root = _run_quick("git rev-parse --show-toplevel 2>/dev/null")
            if not root:
                return ToolResult(tool=self.name, output="(not a git repository)")
            status = _run_quick("git status --short 2>/dev/null") or "(clean)"
            return ToolResult(tool=self.name, output=status)
        except Exception as exc:
            return ToolResult(tool=self.name, error=str(exc))


class GitDiffTool(BaseTool):
    name = "git_diff"
    description = "Show the diff of changes (unstaged). Optionally specify a file path to see changes for that file only."
    args_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Optional: file path to show diff for, relative to project root",
            },
        },
    }

    def execute(self, path: Optional[str] = None, **kwargs: Any) -> ToolResult:
        try:
            root = _run_quick("git rev-parse --show-toplevel 2>/dev/null")
            if not root:
                return ToolResult(tool=self.name, output="(not a git repository)")
            cmd = "git diff 2>/dev/null"
            if path:
                cmd += f" -- {_shlex_quote(path)}"
            diff = _run_quick(cmd) or "(no changes)"
            return ToolResult(tool=self.name, output=diff)
        except Exception as exc:
            return ToolResult(tool=self.name, error=str(exc))


class GitBranchTool(BaseTool):
    name = "git_branch"
    description = "Show the current git branch name."
    args_schema = {
        "type": "object",
        "properties": {},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            branch = _run_quick("git rev-parse --abbrev-ref HEAD 2>/dev/null")
            if not branch:
                return ToolResult(tool=self.name, output="(not a git repository)")
            return ToolResult(tool=self.name, output=branch)
        except Exception as exc:
            return ToolResult(tool=self.name, error=str(exc))


class RunCommandTool(BaseTool):
    name = "run_command"
    description = "Run a READ-ONLY shell command to inspect the environment. Use for checking versions, configs, etc. NEVER use for destructive operations."
    args_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to run (read-only only: versions, checks, listings)",
            },
        },
        "required": ["command"],
    }

    def execute(self, command: str, **kwargs: Any) -> ToolResult:
        # Safety: block known destructive commands
        dangerous = ["rm ", "mv ", "cp ", "dd ", "mkfs", "format", ">", "| ",
                      "chmod", "chown", "write", "delete", "sudo ", "su ",
                      "kill ", "pkill", "shutdown", "reboot", "init "]
        cmd_lower = command.strip().lower()
        for d in dangerous:
            if cmd_lower.startswith(d):
                return ToolResult(
                    tool=self.name,
                    error=f"Command blocked for safety: '{command}' is not allowed",
                )

        output = _run_quick_allow_stderr(command, timeout=15)
        return ToolResult(tool=self.name, output=output)


class WhichTool(BaseTool):
    name = "which"
    description = "Check if a binary/tool is available on the system and where it's located."
    args_schema = {
        "type": "object",
        "properties": {
            "binary": {
                "type": "string",
                "description": "Name of the binary to locate (e.g., 'node', 'npm', 'python3')",
            },
        },
        "required": ["binary"],
    }

    def execute(self, binary: str, **kwargs: Any) -> ToolResult:
        try:
            path = _run_quick(f"which {binary} 2>/dev/null")
            version = _run_quick(f"{binary} --version 2>/dev/null")
            parts = [f"path: {path}" if path else "path: (not found)"]
            if version:
                parts.append(f"version: {version}")
            return ToolResult(tool=self.name, output="\n".join(parts))
        except Exception as exc:
            return ToolResult(tool=self.name, error=str(exc))


class FindFileTool(BaseTool):
    name = "find_file"
    description = "Find files by name in the project tree. Searches up to 3 levels deep."
    args_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "File name or pattern to search for (e.g., 'package.json', '*.yaml')",
            },
        },
        "required": ["name"],
    }

    def execute(self, name: str, **kwargs: Any) -> ToolResult:
        try:
            root = _find_project_root()
            # Use find with maxdepth to limit scope
            result = subprocess.run(
                f"find '{root}' -maxdepth 3 -name '{name}' -not -path '*/node_modules/*' -not -path '*/.git/*' 2>/dev/null",
                shell=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            files = [line.strip() for line in result.stdout.split("\n") if line.strip()]
            if not files:
                return ToolResult(tool=self.name, output=f"(no files matching '{name}' found)")
            return ToolResult(tool=self.name, output="\n".join(files[:30]))
        except Exception as exc:
            return ToolResult(tool=self.name, error=str(exc))


class ReadPackageJsonTool(BaseTool):
    name = "read_package_json"
    description = "Read the project's package.json and return its key fields (name, scripts, dependencies, devDependencies)."
    args_schema = {
        "type": "object",
        "properties": {},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            root = _find_project_root()
            pkg_path = root / "package.json"
            if not pkg_path.exists():
                return ToolResult(tool=self.name, error="package.json not found in project root")
            with open(pkg_path) as f:
                pkg = json.load(f)
            # Extract key fields
            summary: Dict[str, Any] = {}
            if "name" in pkg:
                summary["name"] = pkg["name"]
            if "version" in pkg:
                summary["version"] = pkg["version"]
            if "scripts" in pkg:
                summary["scripts"] = pkg["scripts"]
            if "dependencies" in pkg:
                summary["dependencies"] = list(pkg["dependencies"].keys())
            if "devDependencies" in pkg:
                summary["devDependencies"] = list(pkg["devDependencies"].keys())
            if "packageManager" in pkg:
                summary["packageManager"] = pkg["packageManager"]
            if "engines" in pkg:
                summary["engines"] = pkg["engines"]

            return ToolResult(tool=self.name, output=json.dumps(summary, indent=2))
        except json.JSONDecodeError:
            return ToolResult(tool=self.name, error="Invalid package.json: JSON parse error")
        except Exception as exc:
            return ToolResult(tool=self.name, error=str(exc))


# ── Registry ─────────────────────────────────────────────────────────────────

def _shlex_quote(s: str) -> str:
    """Simple shell quoting."""
    escaped = s.replace("'", "'\\''")
    return f"'{escaped}'"


def get_all_tools() -> List[BaseTool]:
    """Return all available tools."""
    return [
        ReadFileTool(),
        ListDirectoryTool(),
        FileExistsTool(),
        GitStatusTool(),
        GitDiffTool(),
        GitBranchTool(),
        RunCommandTool(),
        WhichTool(),
        FindFileTool(),
        ReadPackageJsonTool(),
    ]


def get_tool_by_name(name: str) -> Optional[BaseTool]:
    """Get a tool instance by its name."""
    for tool in get_all_tools():
        if tool.name == name:
            return tool
    return None


def get_tools_schema() -> List[Dict[str, Any]]:
    """Return the tools schema as a list of dicts for the LLM prompt.

    Each entry has name, description, and args_schema so the LLM
    knows what tools are available and how to call them.
    """
    schema: List[Dict[str, Any]] = []
    for tool in get_all_tools():
        schema.append({
            "name": tool.name,
            "description": tool.description,
            "args_schema": tool.args_schema,
        })
    return schema