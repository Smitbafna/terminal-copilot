"""Build structured investigation data for LLM diagnosis.

This module constructs a structured InvestigationData object from the
workflow state, serializes it to JSON, and passes it to the LLM for diagnosis.
No raw text dumping — everything is structured.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Generator, Optional

from terminal_copilot.models import (
    CommandResult,
    InvestigationData,
    InvestigationResult,
)
from terminal_copilot.llm import diagnose_investigation, parse_diagnosis

# Max stderr length to send to LLM (to avoid token blowout)
_MAX_STDERR_LENGTH = 2000
_MAX_STDOUT_LENGTH = 500
_MAX_CONTEXT_ITEMS = 30


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len, appending truncation notice if needed."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n\n[...truncated; showing first {} chars]".format(max_len)


def _sanitize_context(ctx: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Sanitize and limit context to safe, relevant items."""
    if not ctx:
        return {}

    sanitized: Dict[str, Any] = {}
    # Keys that might contain very long values — truncate
    long_text_keys = {"status", "modified_files", "running_containers", "images"}

    for i, (key, value) in enumerate(ctx.items()):
        if i >= _MAX_CONTEXT_ITEMS:
            sanitized["_truncated"] = True
            break

        if key in long_text_keys and isinstance(value, str):
            sanitized[key] = _truncate(value, 500)
        elif isinstance(value, (list, tuple)):
            # Limit list lengths
            if len(value) > 10:
                sanitized[key] = list(value[:10]) + [f"... ({len(value) - 10} more)"]
            else:
                sanitized[key] = list(value)
        else:
            sanitized[key] = value

    return sanitized


def build_investigation(
    command: str,
    result: CommandResult,
    plugin: str,
    context: Optional[Dict[str, Any]] = None,
) -> InvestigationData:
    """Build a structured investigation object from the workflow state.

    Args:
        command: The original command that was executed.
        result: The CommandResult from execution.
        plugin: The name of the matching plugin.
        context: Structured context collected by the plugin.

    Returns:
        An InvestigationData object ready for LLM diagnosis.
    """
    return InvestigationData(
        command=command,
        exit_code=result.exit_code,
        stderr=_truncate(result.stderr, _MAX_STDERR_LENGTH),
        stdout=_truncate(result.stdout, _MAX_STDOUT_LENGTH),
        plugin=plugin,
        context=_sanitize_context(context),
    )


def serialize_investigation(data: InvestigationData) -> str:
    """Serialize investigation data to a JSON string for the LLM prompt.

    Args:
        data: The InvestigationData to serialize.

    Returns:
        A pretty-printed JSON string.
    """
    return json.dumps(data.model_dump(), indent=2, default=str)


def run_investigation(data: InvestigationData) -> InvestigationResult:
    """Run the LLM investigation: stream diagnosis and parse result.

    Args:
        data: The structured investigation data.

    Returns:
        An InvestigationResult with diagnosis and suggested commands.
    """
    investigation_json = serialize_investigation(data)

    # Stream the LLM response and collect full text
    full_response: list[str] = []
    for chunk in diagnose_investigation(investigation_json):
        full_response.append(chunk)

    raw_text = "".join(full_response)
    diagnosis, commands = parse_diagnosis(raw_text)

    return InvestigationResult(
        diagnosis=diagnosis,
        suggested_commands=commands,
        raw_response=raw_text,
    )


def run_investigation_streaming(
    data: InvestigationData,
) -> Generator[tuple[str, str, InvestigationResult] | tuple[str, None, None], None, tuple[str, str, InvestigationResult]]:
    """Stream the investigation and collect chunks for progressive display.

    This is useful for CLI display where we want to show the LLM response
    as it streams in real-time. Final parsing happens after streaming completes.

    Args:
        data: The structured investigation data.

    Yields:
        Intermediate tuples of (chunk, None, None) during streaming.
        Final tuple of ("", raw_text, investigation_result) when done.
    """
    investigation_json = serialize_investigation(data)

    full_response: list[str] = []
    for chunk in diagnose_investigation(investigation_json):
        full_response.append(chunk)
        yield chunk, None, None

    raw_text = "".join(full_response)
    diagnosis, commands = parse_diagnosis(raw_text)
    result = InvestigationResult(
        diagnosis=diagnosis,
        suggested_commands=commands,
        raw_response=raw_text,
    )

    yield "", raw_text, result
