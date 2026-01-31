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

## Milestone 4: Project Health & Preflight Checks

- [x] Preflight checks before command execution
- [x] Plugin-specific health checks (npm, docker, git, python, rust, go)
- [x] Warning system for potential issues
- [x] Error blocking for preventing dangerous commands
- [x] `--skip-preflight` flag to bypass checks

### Project Detection

Terminal Copilot can automatically detect your project type based on common marker files:

- **package.json** → Node
- **Cargo.toml** → Rust
- **go.mod** → Go
- **requirements.txt** or **pyproject.toml** → Python
- **Dockerfile** → Docker
- **docker-compose.yml** → Docker Compose

```bash
# Detect project type in current directory
terminal-copilot detect

# Detect project type in a specific directory
terminal-copilot detect --path /path/to/project
```

### Preflight Checks

Before executing a command, Terminal Copilot now inspects for common problems:

- **npm/pnpm/yarn**: Checks for package.json, node/npm availability, common typos
- **docker**: Checks for Docker daemon status, missing images
- **git**: Checks for git installation and repository presence
- **python**: Checks for Python availability, file existence, virtualenv status
- **rust**: Checks for cargo installation, Cargo.toml presence
- **go**: Checks for go installation, go.mod presence
- **generic**: Checks for sudo usage, dangerous rm commands

```bash
# Preflight warnings will be shown
terminal-copilot run "npm install"  # Warns if package.json missing

# Skip preflight checks with -s flag
terminal-copilot run -s "npm install"  # Skip checks
```

## Future Milestones

- Auto-fixing failed commands
- File scanning and project analysis
- Streaming output improvements
- Watchdog mode
