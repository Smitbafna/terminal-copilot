"""Gemini LLM client with structured JSON output for tool-calling.

Returns JSON tool requests / finish decisions — never markdown.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types

from terminal_copilot.models import ToolRequest

# Auto-load .env file from the project root (or parent directories)
load_dotenv(Path(__file__).parent.parent / ".env")
load_dotenv()  # also check cwd and parent dirs as fallback


DEFAULT_MODEL = "gemini-3.1-flash-lite"


def _get_api_key() -> str:
    """Retrieve the Gemini API key from environment variables."""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError(
            "Gemini API key not found. Set GEMINI_API_KEY or GOOGLE_API_KEY."
        )
    return api_key


def _make_system_prompt(tools_schema: List[Dict[str, Any]]) -> str:
    """Build the system prompt with available tool definitions."""
    tools_json = json.dumps(tools_schema, indent=2)

    return f"""You are a terminal diagnostics agent. Your job is to investigate why a shell command
failed by requesting information piece by piece. You are given a set of tools you can use to
gather information. Use them strategically — only request what you actually need.

RULES:
1. NEVER request dangerous tools. Only use the tools listed below.
2. Request ONE tool at a time. Wait for the result before requesting another.
3. Think step by step. Start with the most likely cause.
4. When you have enough information to diagnose the problem definitively, set "finish": true.
5. If you hit an error from a tool, note it but try alternative approaches.
6. Maximum {10} tool calls.

Available tools:
{tools_json}

You MUST respond with valid JSON only, using this exact schema:

For requesting a tool:
{{"thought": "Your reasoning about what you need", "tool": "tool_name", "args": {{"arg1": "value1"}}}}

For finishing the diagnosis:
{{"thought": "Summary of what you found", "finish": true, "root_cause": "Clear explanation of the root cause", "confidence": 95, "commands": ["fix command 1", "fix command 2"]}}

IMPORTANT: Respond with ONLY valid JSON. No markdown, no code fences, no extra text."""


def build_prompt(
    command: str,
    exit_code: int,
    stderr: str,
    stdout: str,
    plugin: str,
    context: Optional[Dict[str, Any]] = None,
    messages: Optional[List[Dict[str, Any]]] = None,
    tools_schema: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Build the full prompt for the agent loop.

    Args:
        command: The failed command.
        exit_code: The exit code.
        stderr: Standard error output.
        stdout: Standard output.
        plugin: The matching plugin name.
        context: Optional structured context from the plugin.
        messages: Previous conversation history (system + user + assistant).
        tools_schema: Schema of available tools.

    Returns:
        The full system prompt with investigation context.
    """
    tools_schema = tools_schema or []
    system = _make_system_prompt(tools_schema)

    context_str = ""
    if context:
        context_str = f"\nContext from plugin '{plugin}':\n```json\n{json.dumps(context, indent=2, default=str)}\n```\n"

    investigation_context = f"""
## Investigation Context

Command: {command}
Exit Code: {exit_code}
{context_str}
stderr:
```
{stderr[:2000]}
```

stdout:
```
{stdout[:500]}
```
"""

    # If there's conversation history, append to it
    if messages:
        # Reconstruct the conversation so far
        history_str = ""
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            history_str += f"\n{role.upper()}:\n{content}\n"

        return f"{system}\n\n{investigation_context}\n\n## Conversation so far\n{history_str}\n\nWhat is your next step? Respond with JSON."
    else:
        # First call — provide initial prompt
        return f"{system}\n\n{investigation_context}\n\nWhat information do you need first? Respond with JSON representing your first tool request."


def call_llm_for_tool_request(
    messages: List[Dict[str, Any]],
    tools_schema: List[Dict[str, Any]],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> ToolRequest:
    """Call the LLM and parse a ToolRequest from its JSON response.

    Args:
        messages: The conversation history (system + user + assistant messages).
        tools_schema: Schema of available tools for the system prompt.
        model: The Gemini model to use.
        temperature: Sampling temperature.
        max_tokens: Maximum tokens in the response.

    Returns:
        A parsed ToolRequest from the LLM's JSON response.

    Raises:
        ValueError: If the LLM response cannot be parsed as valid JSON.
    """
    system_content = _make_system_prompt(tools_schema)

    # Build the full conversation history into the prompt so the LLM
    # can see ALL previous tool calls, results, and its own thoughts.
    conversation_parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        label = role.upper() if role == "user" or role == "assistant" else role
        conversation_parts.append(f"{label}:\n{content}")

    conversation_history = "\n\n".join(conversation_parts)

    prompt = f"{system_content}\n\n{conversation_history}\n\nWhat is your next step? Respond with JSON."

    api_key = _get_api_key()
    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        ),
    )

    raw = response.text.strip()

    # Strip code fences if present (defensive)
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
    if raw.startswith("```json"):
        raw = raw[7:].strip()
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

    try:
        data = json.loads(raw)
        return ToolRequest(**data)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(
            f"Failed to parse LLM response as JSON. Response was:\n{raw}\n\nError: {exc}"
        )


# ── Legacy functions (backwards compat for `explain` command) ──


def stream_response(
    prompt: str,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.3,
    max_output_tokens: int = 2048,
) -> Generator[str, None, None]:
    """Stream a Gemini response for the given prompt.

    Args:
        prompt: The full prompt text to send to the model.
        model: The Gemini model to use.
        temperature: Sampling temperature (lower = more deterministic).
        max_output_tokens: Maximum tokens in the response.

    Yields:
        Chunks of text as they are streamed from the API.

    Raises:
        ValueError: If the API key is not configured.
        Exception: On API call failures.
    """
    api_key = _get_api_key()
    client = genai.Client(api_key=api_key)

    response = client.models.generate_content_stream(
        model=model,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        ),
    )

    for chunk in response:
        if chunk.text:
            yield chunk.text


def diagnose_investigation(
    investigation_json: str,
    model: str = DEFAULT_MODEL,
) -> Generator[str, None, None]:
    """Stream a diagnosis from Gemini based on structured investigation data.

    Args:
        investigation_json: JSON-serialized InvestigationData.
        model: The Gemini model to use.

    Yields:
        Text chunks of the diagnosis as they are streamed.
    """
    system_context = (
        "You are a terminal diagnostics expert. Your job is to analyze why a "
        "shell command failed and suggest fixes. You will receive a structured "
        "JSON object with the command, exit code, stderr, plugin info, and "
        "environment context.\n\n"
        "Respond with structured output using these exact section headers:\n\n"
        "Root Cause\n"
        "────────────────────────────\n"
        "<1-2 sentence explanation of what went wrong>\n\n"
        "Confidence: <0-100>%\n\n"
        "Suggested Commands\n\n"
        "1. <command>\n"
        "2. <command>\n"
        "3. <command>\n\n"
        "Example:\n\n"
        "Root Cause\n"
        "────────────────────────────\n"
        "The lockfile version is newer than your installed pnpm. "
        "You're running `npm install` but the project uses pnpm.\n\n"
        "Confidence: 96%\n\n"
        "Suggested Commands\n\n"
        "1. pnpm install --lockfile-only\n"
        "2. corepack enable\n"
        "3. pnpm --version\n"
    )

    prompt = f"{system_context}\n\nInvestigation Data:\n```json\n{investigation_json}\n```"

    yield from stream_response(prompt, model=model)


def parse_diagnosis(raw: str) -> tuple[str, int, list[str]]:
    """Parse the raw LLM response into root cause, confidence, and commands.

    Args:
        raw: The full LLM response text.

    Returns:
        A tuple of (root_cause, confidence_percent, list_of_suggested_commands).
    """
    lines = raw.split("\n")
    root_cause: list[str] = []
    commands: list[str] = []
    confidence = 0
    section: str | None = None

    for line in lines:
        stripped = line.strip()

        # Detect section headers
        if stripped == "Root Cause":
            section = "root_cause"
            continue
        if stripped == "Suggested Commands":
            section = "commands"
            continue
        if stripped.startswith("Confidence:"):
            section = None
            # Extract number from "Confidence: 96%"
            num_str = stripped.replace("Confidence:", "").strip().rstrip("%").strip()
            try:
                confidence = int(num_str)
            except ValueError:
                confidence = 0
            continue
        if stripped.startswith("─") or stripped == "":
            continue

        # Collect content per section
        if section == "root_cause":
            root_cause.append(stripped)
        elif section == "commands":
            # Strip leading numbers like "1. ", "2. "
            cmd = stripped
            for prefix in ["1. ", "2. ", "3. ", "4. ", "5. "]:
                if cmd.startswith(prefix):
                    cmd = cmd[len(prefix):]
                    break
            if cmd:
                commands.append(cmd)

    # Fallback: if no structured parsing worked, use old heuristic
    if not root_cause and not commands:
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("npm ", "pnpm ", "yarn ", "npx ", "git ",
                                     "docker ", "pip ", "python ", "node ",
                                     "rm ", "mv ", "cp ", "mkdir ", "touch ",
                                     "sudo ", "export ", "source ", "cd ")):
                commands.append(stripped)

    return " ".join(root_cause).strip(), confidence, commands
