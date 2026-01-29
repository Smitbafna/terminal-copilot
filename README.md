# Terminal Copilot

A CLI tool that wraps shell commands with diagnostics and an AI-ready workflow foundation.

## Installation

```bash
uv sync
```

## Usage

```bash
terminal-copilot run "<command>"
```

### Examples

```bash
# Run a simple command
terminal-copilot run "echo hello world"

# Run a command that fails
terminal-copilot run "ls /nonexistent"

# Run with plugin detection
terminal-copilot run "git status"
terminal-copilot run "npm install"
terminal-copilot run "docker ps"
```

### Output

The tool displays:
- The command that was executed
- The matching plugin (npm, docker, git, or unknown)
- Exit code (green for success, red for failure)
- Execution time
- stdout and stderr in separate panels

## Configuration

Create `~/.terminal-copilot/config.yaml`:

```yaml
provider: gemini
auto_execute: false
plugins:
  - npm
  - docker
  - git
```

## Project Structure

```
terminal_copilot/
    __init__.py    # Package init
    cli.py         # Typer CLI entry point
    runner.py      # Shell command execution
    config.py      # YAML config loader
    plugins.py     # Plugin interface (npm, docker, git)
    workflow.py    # LangGraph workflow skeleton
    models.py      # Pydantic models

pyproject.toml
README.md
```

## Tech Stack

- Python 3.12
- [Typer](https://typer.tiangolo.com/) – CLI framework
- [Rich](https://rich.readthedocs.io/) – Terminal UI
- [Pydantic v2](https://docs.pydantic.dev/) – Data validation
- [PyYAML](https://pyyaml.org/) – Config parsing
- [LangGraph](https://langchain-ai.github.io/langgraph/) – Workflow graph

## Milestone 1

- [x] CLI with `run` command
- [x] Shell command execution via subprocess
- [x] Rich output with panels
- [x] YAML config loader
- [x] Plugin skeleton (npm, docker, git)
- [x] LangGraph workflow skeleton

## Future Milestones

- LLM integration
- AI reasoning and diagnostics
- Auto-fixing failed commands
- File scanning and project analysis
- Streaming output
- Watchdog mode
- Terminal interception