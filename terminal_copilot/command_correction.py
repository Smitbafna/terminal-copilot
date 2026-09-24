"""Deterministic corrections for misspelled shell commands.

Corrections happen before preflight checks and execution so the rest of the
application (plugin selection, diagnostics, and history) sees the command
that will actually run. The first command token is matched against the
commands supported by the project and other common developer tools.
"""

from __future__ import annotations

import re
import shutil
from difflib import SequenceMatcher
from typing import Callable, Optional


CommandSuggester = Callable[[str, tuple[str, ...]], Optional[tuple[str, str]]]


# Commands understood by the built-in plugins. Keeping these here means that
# a command such as pnpm can still be suggested even when it is not installed
# in the current environment.
_KNOWN_COMMANDS = frozenset(
    {
        "npm", "pnpm", "yarn", "npx", "node", "corepack",
        "docker", "git", "kubectl",
        "gcc", "g++", "clang", "clang++", "make", "cmake", "cc", "c++",
        "rustc", "cargo", "go", "python", "python3", "pip", "pip3", "uv", "poetry",
        "ls", "cat", "cd", "rm", "mv", "cp", "mkdir", "touch", "echo", "sudo",
        "bash", "sh", "curl", "wget", "tar", "grep", "find", "sed", "awk", "jq",
        "terraform", "aws", "gcloud", "psql", "mysql", "java", "mvn", "gradle",
        "dotnet", "ruby", "gem", "bundle", "deno", "bun",
    }
)

# A command token ends at whitespace or a common shell separator. Only this
# first token is replaced; arguments and the rest of the command stay intact.
_COMMAND_TOKEN_RE = re.compile(
    r"^(?P<leading>\s*)(?P<token>[^\s;&|]+)(?P<suffix>.*)$", re.DOTALL
)

# These subcommand corrections remain explicit because they are not command
# names and cannot be inferred safely from the executable vocabulary.
_SUBCOMMAND_CORRECTIONS = (
    (
        re.compile(r"^(\s*npm\s+)instal(?=\s|$)"),
        r"\1install",
        "npm instal -> npm install",
    ),
    (
        re.compile(r"^(\s*npm\s+)uninstal(?=\s|$)"),
        r"\1uninstall",
        "npm uninstal -> npm uninstall",
    ),
)

_MIN_SIMILARITY = 0.70
_SHORT_TOKEN_SIMILARITY = 0.65
_AMBIGUITY_MARGIN = 0.05


def _candidate_commands() -> frozenset[str]:
    """Return the command vocabulary understood by Terminal Copilot."""
    return _KNOWN_COMMANDS


def candidate_commands() -> tuple[str, ...]:
    """Return the supported command names available to the LLM matcher."""
    return tuple(sorted(_candidate_commands()))


def _similarity(token: str, candidate: str) -> float:
    """Return a bounded spelling similarity score for two command names."""
    return SequenceMatcher(None, token.casefold(), candidate.casefold()).ratio()


def _is_known_or_available(token: str) -> bool:
    """Return whether a token is already a supported or installed command."""
    return (
        token.casefold() in {command.casefold() for command in _KNOWN_COMMANDS}
        or shutil.which(token) is not None
    )


def _closest_command(token: str) -> Optional[str]:
    """Find one unambiguous close command name, if one exists."""
    candidates = _candidate_commands()
    token_folded = token.casefold()

    # Never replace a command that is already spelled correctly, whether or
    # not it is currently installed.
    if _is_known_or_available(token):
        return None

    scored = sorted(
        (
            (_similarity(token, candidate), candidate)
            for candidate in candidates
            if candidate.casefold() != token_folded
        ),
        key=lambda item: (-item[0], item[1]),
    )
    if not scored:
        return None

    best_score, best_candidate = scored[0]
    threshold = _SHORT_TOKEN_SIMILARITY if len(token) <= 3 else _MIN_SIMILARITY
    if best_score < threshold:
        return None

    # Do not guess when two commands are almost equally close. This keeps
    # inputs such as "npi" (which could mean npm, npx, or pip) unchanged.
    if len(scored) > 1 and best_score - scored[1][0] < _AMBIGUITY_MARGIN:
        return None

    return best_candidate


def _correct_subcommands(command: str) -> tuple[str, list[str]]:
    """Apply explicit corrections to known command subcommands."""
    corrected = command
    changes: list[str] = []

    for pattern, replacement, description in _SUBCOMMAND_CORRECTIONS:
        if pattern.search(corrected):
            corrected = pattern.sub(replacement, corrected, count=1)
            changes.append(description)

    return corrected, changes


def correct_command(
    command: str,
    command_suggester: Optional[CommandSuggester] = None,
) -> tuple[str, Optional[str]]:
    """Correct a misspelled first command token when it is unambiguous.

    The matcher is deliberately conservative. It uses the commands supported
    by this application and other common developer tools, and it does not
    change a command when the closest candidates are ambiguous. If a
    ``command_suggester`` callback is provided, it is consulted only when the
    local matcher cannot make a confident correction.

    Args:
        command: The command supplied by the user.
        command_suggester: Optional fallback callback receiving the unknown
            command token and the supported candidate names.

    Returns:
        A tuple containing the command to use and a short description of the
        corrections made, or ``None`` when the command was unchanged.
    """
    match = _COMMAND_TOKEN_RE.match(command)
    if not match:
        return command, None

    corrected = command
    changes: list[str] = []
    token = match.group("token")

    candidate = _closest_command(token)
    if candidate:
        corrected = match.group("leading") + candidate + match.group("suffix")
        changes.append(f"{token} -> {candidate}")
    elif command_suggester is not None and not _is_known_or_available(token):
        candidates = candidate_commands()
        suggestion = command_suggester(token, candidates)
        if suggestion is not None:
            suggested_command, reason = suggestion
            if suggested_command in candidates:
                corrected = (
                    match.group("leading")
                    + suggested_command
                    + match.group("suffix")
                )
                changes.append(f"{token} -> {suggested_command} (LLM: {reason})")

    corrected, subcommand_changes = _correct_subcommands(corrected)
    changes.extend(subcommand_changes)

    return corrected, "; ".join(changes) if changes else None

