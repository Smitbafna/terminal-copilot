"""Tests for the Gemini command-correction client."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from terminal_copilot.llm import suggest_command_correction


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(text=response_text)
    return client


def test_suggest_command_correction_returns_validated_suggestion():
    client = _mock_client(
        '{"corrected_command": "git", "reason": "The extra letters resemble git"}'
    )

    with patch("terminal_copilot.llm._get_api_key", return_value="test-key"):
        with patch("terminal_copilot.llm.genai.Client", return_value=client) as client_class:
            suggestion = suggest_command_correction("gioiit", ["git", "npm", "docker"])

    assert suggestion is not None
    assert suggestion.command == "git"
    assert suggestion.reason == "The extra letters resemble git"
    client_class.assert_called_once_with(api_key="test-key")

    request = client.models.generate_content.call_args.kwargs
    assert "gioiit" in request["contents"]
    assert "git" in request["contents"]


@pytest.mark.parametrize(
    "response_text",
    [
        "not valid JSON",
        '{"corrected_command": null, "reason": "no confident match"}',
        '{"corrected_command": "curl", "reason": "not allowed"}',
        '["git"]',
    ],
)
def test_suggest_command_correction_rejects_unusable_responses(response_text):
    client = _mock_client(response_text)

    with patch("terminal_copilot.llm._get_api_key", return_value="test-key"):
        with patch("terminal_copilot.llm.genai.Client", return_value=client):
            suggestion = suggest_command_correction("gioiit", ["git", "npm"])

    assert suggestion is None
