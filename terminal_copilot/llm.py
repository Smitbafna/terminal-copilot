"""Gemini LLM client with streaming support for Terminal Copilot."""

from __future__ import annotations

import os
from typing import Generator, Optional

from google import genai
from google.genai import types as genai_types


DEFAULT_MODEL = "gemini-2.0-flash"


def _get_api_key() -> str:
    """Retrieve the Gemini API key from environment variables."""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError(
            "Gemini API key not found. Set GEMINI_API_KEY or GOOGLE_API_KEY."
        )
    return api_key


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
        "Respond with:\n"
        "1. A concise **Diagnosis** — what went wrong and why.\n"
        "2. **Suggested Commands** — 1-3 concrete shell commands the user can "
        "run to fix the issue. Each command must be on its own line prefixed "
        "with `CMD:`.\n\n"
        "Keep the diagnosis concise (2-4 sentences). For example:\n\n"
        "The `npm install` command failed with exit code 254. The lockfile is "
        "`pnpm-lock.yaml` but you're running `npm install` instead of "
        "`pnpm install`. Mixing package managers can cause dependency conflicts.\n\n"
        "CMD: pnpm install\n"
        "CMD: rm -rf node_modules && pnpm install\n"
    )

    prompt = f"{system_context}\n\nInvestigation Data:\n```json\n{investigation_json}\n```"

    yield from stream_response(prompt, model=model)


def parse_diagnosis(raw: str) -> tuple[str, list[str]]:
    """Parse the raw LLM response into diagnosis text and suggested commands.

    Args:
        raw: The full LLM response text.

    Returns:
        A tuple of (diagnosis_text, list_of_suggested_commands).
    """
    lines = raw.split("\n")
    diagnosis_parts: list[str] = []
    commands: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("CMD:") or stripped.startswith("cmd:"):
            cmd = stripped[4:].strip()
            if cmd:
                commands.append(cmd)
        else:
            diagnosis_parts.append(line)

    diagnosis = "\n".join(diagnosis_parts).strip()

    # If no CMD: markers found, try to extract anything that looks like a command
    if not commands:
        for line in lines:
            stripped = line.strip()
            # Heuristic: lines starting with common CLI prefixes
            if stripped.startswith(("npm ", "pnpm ", "yarn ", "npx ", "git ",
                                     "docker ", "pip ", "python ", "node ",
                                     "rm ", "mv ", "cp ", "mkdir ", "touch ",
                                     "sudo ", "export ", "source ", "cd ")):
                commands.append(stripped)

    return diagnosis, commands