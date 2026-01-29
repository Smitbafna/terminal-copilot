"""Minimal LangGraph workflow skeleton.

This demonstrates how state flows through the graph:
  Execute Command -> Find Plugin -> Collect Context -> Finish
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from langgraph.graph import StateGraph, END

from terminal_copilot.models import WorkflowState
from terminal_copilot.runner import run_command
from terminal_copilot.plugins import find_matching_plugin, get_plugin_by_name


def execute_command_node(state: WorkflowState) -> dict:
    """Execute the command and store the result."""
    result = run_command(state.command)
    return {"command_result": result}


def find_plugin_node(state: WorkflowState) -> dict:
    """Find the matching plugin for the command."""
    plugin_name = find_matching_plugin(state.command)
    return {"matching_plugin": plugin_name}


def collect_context_node(state: WorkflowState) -> dict:
    """Collect structured context from the matching plugin."""
    if state.matching_plugin and state.matching_plugin != "unknown":
        plugin = get_plugin_by_name(state.matching_plugin)
        if plugin is not None:
            context = plugin.collect_context(state.command)
            return {"context": context}
    return {"context": None}


def build_workflow() -> StateGraph:
    """Build and compile the minimal LangGraph workflow.

    Returns:
        A compiled StateGraph that can be invoked.
    """
    workflow = StateGraph(WorkflowState)

    workflow.add_node("execute_command", execute_command_node)
    workflow.add_node("find_plugin", find_plugin_node)
    workflow.add_node("collect_context", collect_context_node)

    workflow.set_entry_point("execute_command")
    workflow.add_edge("execute_command", "find_plugin")
    workflow.add_edge("find_plugin", "collect_context")
    workflow.add_edge("collect_context", END)

    return workflow.compile()


def run_workflow(command: str) -> WorkflowState:
    """Run the workflow for a given command.

    Args:
        command: The shell command to process.

    Returns:
        The final WorkflowState after the graph finishes.
    """
    app = build_workflow()
    result = app.invoke({"command": command})
    # Convert the dict result back into a typed WorkflowState
    return WorkflowState(**result)