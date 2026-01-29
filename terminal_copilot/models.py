"""Pydantic models for Terminal Copilot."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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
        default=None, description="AI provider to use"
    )
    auto_execute: bool = Field(
        default=False, description="Whether to auto-execute commands"
    )
    plugins: List[str] = Field(
        default_factory=list, description="List of enabled plugin names"
    )


class InvestigationData(BaseModel):
    """Structured investigation object passed to the LLM.

    This is the structured context object that gets serialized into
    the LLM prompt — not raw text dumping.
    """

    command: str = Field(description="The command that was executed and failed")
    exit_code: int = Field(description="Exit code of the failed command")
    stderr: str = Field(description="Standard error output from the command")
    plugin: str = Field(
        description="Name of the plugin that matched the command"
    )
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Structured context collected by the matching plugin",
    )
    stdout: str = Field(
        default="",
        description="Standard output from the command (may be empty or truncated)",
    )


class InvestigationResult(BaseModel):
    """Result of the LLM investigation."""

    diagnosis: str = Field(description="Markdown explanation of what went wrong")
    suggested_commands: List[str] = Field(
        default_factory=list,
        description="Suggested commands to fix the issue",
    )
    raw_response: str = Field(
        default="",
        description="Raw LLM response text",
    )


class WorkflowState(BaseModel):
    """State that flows through the LangGraph workflow.

    Flow: Execute Command -> Find Plugin -> Collect Context ->
          [Success? -> END] [Failure -> Build Investigation -> LLM Diagnosis -> END]
    """

    command: str = Field(description="The original command to execute")
    command_result: Optional[CommandResult] = Field(
        default=None, description="Result of executing the command"
    )
    matching_plugin: Optional[str] = Field(
        default=None, description="Name of the plugin that matched the command"
    )
    context: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Structured context collected by the matching plugin",
    )
    investigation: Optional[InvestigationData] = Field(
        default=None,
        description="Structured investigation data built for the LLM",
    )
    diagnosis: Optional[InvestigationResult] = Field(
        default=None,
        description="LLM diagnosis result with suggestions",
    )
    error: Optional[str] = Field(
        default=None, description="Error message if something failed"
    )
