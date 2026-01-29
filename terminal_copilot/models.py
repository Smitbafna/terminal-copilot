"""Pydantic models for Terminal Copilot."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class CommandResult(BaseModel):
    """Result of executing a shell command."""

    command: str = Field(description="The command that was executed")
    stdout: str = Field(description="Standard output from the command")
    stderr: str = Field(description="Standard error from the command")
    exit_code: int = Field(description="Exit code of the command")
    execution_time: float = Field(description="Execution time in seconds")
    success: bool = Field(description="Whether the command succeeded")


class PluginConfig(BaseModel):
    """Configuration for a single plugin."""

    name: str = Field(description="Name of the plugin")


class AppConfig(BaseModel):
    """Application configuration loaded from YAML."""

    provider: Optional[str] = Field(
        default=None, description="AI provider to use (not implemented yet)"
    )
    auto_execute: bool = Field(
        default=False, description="Whether to auto-execute commands"
    )
    plugins: List[str] = Field(
        default_factory=list, description="List of enabled plugin names"
    )


class WorkflowState(BaseModel):
    """State that flows through the LangGraph workflow."""

    command: str = Field(description="The original command to execute")
    command_result: Optional[CommandResult] = Field(
        default=None, description="Result of executing the command"
    )
    matching_plugin: Optional[str] = Field(
        default=None, description="Name of the plugin that matched the command"
    )
    error: Optional[str] = Field(
        default=None, description="Error message if something failed"
    )