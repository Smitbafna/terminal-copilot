"""Shell command execution using subprocess."""

from __future__ import annotations

import subprocess
import time
import shlex

from terminal_copilot.models import CommandResult


def run_command(command: str) -> CommandResult:
    """Execute a shell command and capture its output.

    Args:
        command: The shell command to execute.

    Returns:
        A CommandResult with stdout, stderr, exit code, and execution time.
    """
    start = time.perf_counter()

    try:
        process = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )
        exit_code = process.returncode
        stdout = process.stdout or ""
        stderr = process.stderr or ""
    except subprocess.TimeoutExpired:
        exit_code = -1
        stdout = ""
        stderr = "Command timed out after 300 seconds."
    except Exception as exc:
        exit_code = -1
        stdout = ""
        stderr = str(exc)

    execution_time = time.perf_counter() - start

    return CommandResult(
        command=command,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        execution_time=round(execution_time, 3),
        success=exit_code == 0,
    )