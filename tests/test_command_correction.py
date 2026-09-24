"""Tests for deterministic command correction."""

from __future__ import annotations

import pytest

from terminal_copilot.command_correction import candidate_commands, correct_command


@pytest.mark.parametrize(
    ("command", "expected", "description"),
    [
        ("npmm install", "npm install", "npmm -> npm"),
        ("  npmm   install", "  npm   install", "npmm -> npm"),
        ("npm instal", "npm install", "npm instal -> npm install"),
        ("npm uninstal", "npm uninstall", "npm uninstal -> npm uninstall"),
        (
            "npmm instal",
            "npm install",
            "npmm -> npm; npm instal -> npm install",
        ),
        ("pnpmmmm --version", "pnpm --version", "pnpmmmm -> pnpm"),
        ("gitt status", "git status", "gitt -> git"),
        ("dockr ps", "docker ps", "dockr -> docker"),
        ("pyton script.py", "python script.py", "pyton -> python"),
    ],
)
def test_corrects_known_command_typos(command, expected, description):
    corrected, correction = correct_command(command)

    assert corrected == expected
    assert correction == description


@pytest.mark.parametrize(
    "command",
    [
        "npm install",
        "npm uninstall",
        "echo npmm install",
        "npmm-install",
        "echo 'npmm install'",
        "npi install",
        "randomunknown --version",
    ],
)
def test_leaves_other_commands_unchanged(command):
    assert correct_command(command) == (command, None)


def test_uses_llm_callback_only_for_unmatched_command():
    calls = []

    def suggester(token, candidates):
        calls.append((token, candidates))
        return ("git", "likely git")

    assert correct_command("gioiit --version", suggester) == (
        "git --version",
        "gioiit -> git (LLM: likely git)",
    )
    assert calls == [("gioiit", candidate_commands())]


def test_does_not_call_llm_for_local_match():
    calls = []

    def suggester(token, candidates):
        calls.append((token, candidates))
        return ("npm", "should not be used")

    assert correct_command("gitt status", suggester) == (
        "git status",
        "gitt -> git",
    )
    assert calls == []


def test_rejects_llm_suggestion_outside_candidate_list():
    def suggester(token, candidates):
        return ("not-a-command", "not in the allowed set")

    assert correct_command("gioiit --version", suggester) == (
        "gioiit --version",
        None,
    )
