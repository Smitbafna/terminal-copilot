"""Lightweight plugin interface for command detection."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List


class BasePlugin(ABC):
    """Base class for all plugins.

    Each plugin can determine whether it supports a given command.
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


class NpmPlugin(BasePlugin):
    """Plugin for npm/pnpm commands."""

    name = "npm"

    def supports(self, command: str) -> bool:
        stripped = command.strip()
        return stripped.startswith("npm ") or stripped.startswith("pnpm ")


class DockerPlugin(BasePlugin):
    """Plugin for docker commands."""

    name = "docker"

    def supports(self, command: str) -> bool:
        return command.strip().startswith("docker ")


class GitPlugin(BasePlugin):
    """Plugin for git commands."""

    name = "git"

    def supports(self, command: str) -> bool:
        return command.strip().startswith("git ")


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