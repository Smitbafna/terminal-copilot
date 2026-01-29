"""Command history storage for Terminal Copilot.

Stores the last failed command with its context in ~/.terminal-copilot/history.json
so that `terminal-copilot explain` can analyze it without re-execution.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from terminal_copilot.models import CommandResult, InvestigationData

HISTORY_DIR = Path.home() / ".terminal-copilot"
HISTORY_PATH = HISTORY_DIR / "history.json"


class CommandHistoryEntry:
    """A single history entry stored on disk."""

    def __init__(
        self,
        command: str,
        exit_code: int,
        stderr: str,
        stdout: str,
        plugin: str,
        context: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None,
    ) -> None:
        self.command = command
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout = stdout
        self.plugin = plugin
        self.context = context or {}
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stderr": self.stderr,
            "stdout": self.stdout,
            "plugin": self.plugin,
            "context": self.context,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CommandHistoryEntry:
        return cls(
            command=data["command"],
            exit_code=data.get("exit_code", -1),
            stderr=data.get("stderr", ""),
            stdout=data.get("stdout", ""),
            plugin=data.get("plugin", "unknown"),
            context=data.get("context"),
            timestamp=data.get("timestamp"),
        )

    def to_investigation_data(self) -> InvestigationData:
        return InvestigationData(
            command=self.command,
            exit_code=self.exit_code,
            stderr=self.stderr,
            stdout=self.stdout,
            plugin=self.plugin,
            context=self.context,
        )


def _ensure_history_dir() -> None:
    """Create the history directory if it doesn't exist."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def record_failed_command(
    command: str,
    result: CommandResult,
    plugin: str,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a failed command to the history file.

    Args:
        command: The command that was executed.
        result: The CommandResult from execution.
        plugin: The matching plugin name.
        context: Structured context from the plugin.
    """
    if result.success:
        return  # Only record failures

    _ensure_history_dir()

    entry = CommandHistoryEntry(
        command=command,
        exit_code=result.exit_code,
        stderr=result.stderr,
        stdout=result.stdout,
        plugin=plugin,
        context=context,
    )

    with open(HISTORY_PATH, "w") as f:
        json.dump(entry.to_dict(), f, indent=2)


def load_last_failed() -> Optional[CommandHistoryEntry]:
    """Load the last failed command from history.

    Returns:
        A CommandHistoryEntry if one exists, None otherwise.
    """
    if not HISTORY_PATH.exists():
        return None

    try:
        with open(HISTORY_PATH) as f:
            data = json.load(f)
        return CommandHistoryEntry.from_dict(data)
    except (json.JSONDecodeError, OSError, KeyError):
        return None