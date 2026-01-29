"""LangGraph workflow for Terminal Copilot.

Thin wrapper around the agent that delegates to the new
tool-calling agent flow. Kept for backwards compatibility.
"""

from __future__ import annotations

from terminal_copilot.models import WorkflowState as OldWorkflowState
from terminal_copilot.agent import run_agent


def run_workflow(command: str) -> OldWorkflowState:
    """Run the workflow for a given command.

    Delegates to the new agent-based implementation.

    Args:
        command: The shell command to process.

    Returns:
        The final WorkflowState after the graph finishes.
    """
    agent_state = run_agent(command)

    # Map AgentState back to old WorkflowState for backwards compatibility
    return OldWorkflowState(
        command=agent_state.command,
        command_result=agent_state.command_result,
        matching_plugin=agent_state.matching_plugin,
        context=agent_state.context,
        investigation=agent_state.investigation,
        diagnosis=agent_state.diagnosis,
        error=agent_state.error,
    )