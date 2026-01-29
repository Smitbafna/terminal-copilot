"""LangGraph agent loop for Terminal Copilot.

The agent decides what information to request, calls tools one at a time,
and repeats until it has enough information to diagnose the problem.

Flow:
  Execute Command -> Find Plugin -> Initialize Investigation ->
  Reason ->
    [Need Tool? -> Execute Tool -> Return Result -> Reason -> ...] ->
  Finish -> Diagnosis
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional

from langgraph.graph import StateGraph, END

from terminal_copilot.models import (
    AgentState,
    CommandResult,
    InvestigationResult,
    ToolResult,
    ToolRequest,
)
from terminal_copilot.runner import run_command
from terminal_copilot.plugins import find_matching_plugin, get_plugin_by_name
from terminal_copilot.llm import call_llm_for_tool_request, build_prompt
from terminal_copilot.tools import get_all_tools, get_tool_by_name, get_tools_schema

MAX_AGENT_ITERATIONS = 10


# ── Graph Nodes ──────────────────────────────────────────────────────────────


def execute_command_node(state: AgentState) -> dict:
    """Execute the command and store the result."""
    result = run_command(state.command)
    return {"command_result": result}


def find_plugin_node(state: AgentState) -> dict:
    """Find the matching plugin for the command."""
    plugin_name = find_matching_plugin(state.command)
    return {"matching_plugin": plugin_name}


def collect_context_node(state: AgentState) -> dict:
    """Collect structured context from the matching plugin."""
    if state.matching_plugin and state.matching_plugin != "unknown":
        plugin = get_plugin_by_name(state.matching_plugin)
        if plugin is not None:
            context = plugin.collect_context(state.command)
            return {"context": context}
    return {"context": None}


def should_investigate(state: AgentState) -> Literal["initialize_agent", "end"]:
    """Decide whether to investigate or end the workflow.

    If the command succeeded, end. If it failed, proceed to agent investigation.
    """
    if state.command_result and not state.command_result.success:
        return "initialize_agent"
    return "end"


def initialize_agent_node(state: AgentState) -> dict:
    """Initialize the agent loop with the first prompt.

    Builds the initial messages list with system + user context.
    """
    if not state.command_result:
        return {"error": "No command result available for investigation"}

    tools_schema = get_tools_schema()

    # Build the initial prompt
    prompt = build_prompt(
        command=state.command,
        exit_code=state.command_result.exit_code,
        stderr=state.command_result.stderr,
        stdout=state.command_result.stdout,
        plugin=state.matching_plugin or "unknown",
        context=state.context,
        tools_schema=tools_schema,
    )

    messages = [
        {"role": "user", "content": prompt},
    ]

    return {
        "messages": messages,
        "tool_results": [],
        "agent_iteration": 0,
    }


def reason_node(state: AgentState) -> dict:
    """Call the LLM to decide what to do next — request a tool or finish."""
    tools_schema = get_tools_schema()
    messages = list(state.messages)

    # Add tool results from the last execution as a user message
    if state.tool_results:
        last_result = state.tool_results[-1]
        result_msg = (
            f"Tool '{last_result.tool}' returned:\n"
            f"{'Success' if last_result.success else 'ERROR'}\n"
            f"{last_result.output if last_result.success else last_result.error}"
        )
        messages.append({"role": "user", "content": result_msg})

    # Truncate messages if too long (keep last 6 turns + system)
    if len(messages) > 12:
        messages = messages[:1] + messages[-11:]

    try:
        tool_request = call_llm_for_tool_request(
            messages=messages,
            tools_schema=tools_schema,
        )
    except ValueError as exc:
        # If parsing fails, finish with what we have
        return {
            "tool_request": ToolRequest(
                thought=f"Failed to parse LLM response: {exc}",
                finish=True,
                root_cause="Error communicating with LLM",
                confidence=0,
                commands=[],
            ),
            "error": str(exc),
        }

    # Add the assistant's response to messages
    messages.append({
        "role": "assistant",
        "content": json.dumps(tool_request.model_dump(), indent=2),
    })

    return {
        "tool_request": tool_request,
        "messages": messages,
        "agent_iteration": state.agent_iteration + 1,
    }


def execute_tool_node(state: AgentState) -> dict:
    """Execute the tool requested by the LLM."""
    if not state.tool_request or not state.tool_request.tool:
        return {"error": "No tool requested"}

    tool_name = state.tool_request.tool
    tool_args = state.tool_request.args or {}

    tool = get_tool_by_name(tool_name)
    if tool is None:
        result = ToolResult(
            tool=tool_name,
            error=f"Unknown tool: '{tool_name}'. Available tools: {[t.name for t in get_all_tools()]}",
        )
    else:
        try:
            result = tool.execute(**tool_args)
        except Exception as exc:
            result = ToolResult(tool=tool_name, error=str(exc))

    tool_results = list(state.tool_results)
    tool_results.append(result)

    return {"tool_results": tool_results}


def should_continue_agent(state: AgentState) -> Literal["execute_tool", "finish", "end"]:
    """Decide whether to continue the agent loop or finish.

    Returns:
        "execute_tool" if there's a tool to call,
        "finish" if the LLM says it's done,
        "end" if max iterations reached or error.
    """
    if state.error:
        return "end"

    if state.agent_iteration >= MAX_AGENT_ITERATIONS:
        return "end"

    if state.tool_request is None:
        return "end"

    if state.tool_request.finish:
        return "finish"

    if state.tool_request.tool:
        return "execute_tool"

    return "end"


def finish_node(state: AgentState) -> dict:
    """Process the final diagnosis from the LLM's finish response."""
    if not state.tool_request:
        return {
            "diagnosis": InvestigationResult(
                root_cause="No diagnosis available",
                confidence=0,
                suggested_commands=[],
            ),
        }

    # Build a summary of what tools were used
    tool_summary = ""
    if state.tool_results:
        tool_names = [t.tool for t in state.tool_results]
        tool_summary = f"\nTools used: {', '.join(tool_names)}"

    diagnosis = InvestigationResult(
        root_cause=state.tool_request.root_cause or "Unknown",
        confidence=state.tool_request.confidence or 0,
        suggested_commands=state.tool_request.commands or [],
        raw_response=state.tool_request.thought or "",
        diagnosis=f"{state.tool_request.root_cause or ''}{tool_summary}\n\n{state.tool_request.thought or ''}",
    )

    return {
        "diagnosis": diagnosis,
        "tool_request": ToolRequest(finish=True),
    }


def handle_max_iterations(state: AgentState) -> dict:
    """Handle case where max iterations were reached without a finish."""
    return {
        "diagnosis": InvestigationResult(
            root_cause="Investigation reached maximum iterations without a definitive answer",
            confidence=0,
            suggested_commands=[],
            raw_response="Investigation terminated due to iteration limit.",
            diagnosis="The agent investigation reached its maximum number of steps "
            "without reaching a definitive conclusion. Try running the command again "
            "or providing more context.",
        ),
    }


# ── Build Graph ──────────────────────────────────────────────────────────────


def build_agent_graph() -> StateGraph:
    """Build and compile the LangGraph agent graph.

    Flow:
      execute_command -> find_plugin -> collect_context ->
      [success? -> end] [failure -> initialize_agent ->
        reason -> [tool? -> execute_tool -> reason -> ...] ->
        [finish? -> finish_node -> end]
        [max_it? -> handle_max_iterations -> end]
      ]
    """
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("execute_command", execute_command_node)
    workflow.add_node("find_plugin", find_plugin_node)
    workflow.add_node("collect_context", collect_context_node)
    workflow.add_node("initialize_agent", initialize_agent_node)
    workflow.add_node("reason", reason_node)
    workflow.add_node("execute_tool", execute_tool_node)
    workflow.add_node("finish", finish_node)
    workflow.add_node("handle_max_iterations", handle_max_iterations)

    workflow.set_entry_point("execute_command")

    # Main execution flow
    workflow.add_edge("execute_command", "find_plugin")
    workflow.add_edge("find_plugin", "collect_context")

    # Conditional: investigate only on failure
    workflow.add_conditional_edges(
        "collect_context",
        should_investigate,
        {
            "initialize_agent": "initialize_agent",
            "end": END,
        },
    )

    # Agent loop
    workflow.add_edge("initialize_agent", "reason")

    workflow.add_conditional_edges(
        "reason",
        should_continue_agent,
        {
            "execute_tool": "execute_tool",
            "finish": "finish",
            "end": "handle_max_iterations",
        },
    )

    workflow.add_edge("execute_tool", "reason")
    workflow.add_edge("finish", END)
    workflow.add_edge("handle_max_iterations", END)

    return workflow.compile()


def run_agent(command: str) -> AgentState:
    """Run the full agent workflow for a given command.

    Args:
        command: The shell command to process.

    Returns:
        The final AgentState after the graph finishes.
    """
    app = build_agent_graph()
    result = app.invoke({"command": command})
    # Convert the dict result back into a typed AgentState
    return AgentState(**result)


def run_agent_streaming(command: str):
    """Run the agent workflow and yield events for live progress display.

    Args:
        command: The shell command to process.

    Yields:
        Dicts with 'type' and 'data' for progress display.
    """
    app = build_agent_graph()

    # Collect all state fields as they come in
    accumulated = {"command": command}

    for event in app.stream({"command": command}):
        for node_name, node_output in event.items():
            yield {"type": "node", "node": node_name, "data": node_output}

            # Merge all state fields from this node
            for key, value in node_output.items():
                accumulated[key] = value

            if "tool_request" in node_output:
                tr = node_output["tool_request"]
                if isinstance(tr, ToolRequest) and tr.tool and not tr.finish:
                    yield {
                        "type": "tool_request",
                        "tool": tr.tool,
                        "args": tr.args,
                        "thought": tr.thought,
                    }
                elif isinstance(tr, dict) and tr.get("tool") and not tr.get("finish"):
                    yield {
                        "type": "tool_request",
                        "tool": tr["tool"],
                        "args": tr.get("args", {}),
                        "thought": tr.get("thought", ""),
                    }

            if "tool_results" in node_output:
                results = node_output["tool_results"]
                if results:
                    last = results[-1]
                    yield {
                        "type": "tool_result",
                        "tool": last.tool,
                        "success": last.success,
                        "summary": last.output[:200] if last.success else last.error[:200],
                    }

    yield {
        "type": "done",
        "state": AgentState(**accumulated) if accumulated else None,
    }
