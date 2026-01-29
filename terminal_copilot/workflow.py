"""LangGraph workflow for Terminal Copilot.

Flow:
  Execute Command -> Find Plugin -> Collect Context ->
    [Success? -> END]
    [Failure -> Build Investigation -> LLM Diagnosis -> END]
"""

from __future__ import annotations

from typing import Any, Dict, Literal

from langgraph.graph import StateGraph, END

from terminal_copilot.models import WorkflowState
from terminal_copilot.runner import run_command
from terminal_copilot.plugins import find_matching_plugin, get_plugin_by_name
from terminal_copilot.investigation import build_investigation, run_investigation


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


def should_investigate(state: WorkflowState) -> Literal["build_investigation", "end"]:
    """Decide whether to investigate or end the workflow.

    If the command succeeded, end. If it failed, proceed to investigation.
    """
    if state.command_result and not state.command_result.success:
        return "build_investigation"
    return "end"


def build_investigation_node(state: WorkflowState) -> dict:
    """Build structured investigation data from the workflow state."""
    if not state.command_result:
        return {"error": "No command result available for investigation"}

    investigation = build_investigation(
        command=state.command,
        result=state.command_result,
        plugin=state.matching_plugin or "unknown",
        context=state.context,
    )
    return {"investigation": investigation}


def llm_diagnosis_node(state: WorkflowState) -> dict:
    """Run LLM diagnosis on the investigation data."""
    if not state.investigation:
        return {"error": "No investigation data available for diagnosis"}

    try:
        diagnosis = run_investigation(state.investigation)
        return {"diagnosis": diagnosis}
    except Exception as exc:
        return {
            "error": f"LLM diagnosis failed: {exc}",
            "diagnosis": None,
        }


def build_workflow() -> StateGraph:
    """Build and compile the LangGraph workflow.

    Returns:
        A compiled StateGraph that can be invoked.
    """
    workflow = StateGraph(WorkflowState)

    workflow.add_node("execute_command", execute_command_node)
    workflow.add_node("find_plugin", find_plugin_node)
    workflow.add_node("collect_context", collect_context_node)
    workflow.add_node("build_investigation", build_investigation_node)
    workflow.add_node("llm_diagnosis", llm_diagnosis_node)

    workflow.set_entry_point("execute_command")

    # Main execution flow
    workflow.add_edge("execute_command", "find_plugin")
    workflow.add_edge("find_plugin", "collect_context")

    # Conditional: investigate only on failure
    workflow.add_conditional_edges(
        "collect_context",
        should_investigate,
        {
            "build_investigation": "build_investigation",
            "end": END,
        },
    )

    # Investigation flow
    workflow.add_edge("build_investigation", "llm_diagnosis")
    workflow.add_edge("llm_diagnosis", END)

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
