# Terminal Copilot

Terminal Copilot is a CLI tool that wraps shell commands with intelligent diagnostics and an AI-powered workflow foundation. It helps developers catch issues before they become failures through proactive preflight checks and environment validation. When commands do fail, it uses AI to diagnose the root cause and guide you through an interactive repair loop with suggested fixes.

## Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install terminal-copilot-cli
```

## Setup

Set your Gemini API key using one of these methods:

### Option 1: Environment Variable

```bash
export GEMINI_API_KEY=your-api-key-here
```

### Option 2: .env File

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Then edit `.env` and add your API key:

```
GEMINI_API_KEY=your-actual-api-key-here
```

Get your API key from [Google AI Studio](https://makersuite.google.com/app/apikeys).

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

## Features

- **AI-Powered Diagnostics**: Automatically analyzes command failures and identifies root causes using AI
- **Interactive Repair Loop**: Guides you through suggested fixes with an interactive selection prompt, retrying until success
- **Proactive Preflight Checks**: Validates project environment before execution to catch issues early
- **Command Correction**: Uses local fuzzy matching first, then Gemini to resolve unknown executable-name typos
- **Multi-Plugin Support**: Built-in plugins for npm, Docker, Git, Rust, Go, and Python ecosystems
- **Project Type Detection**: Automatically detects project types from marker files (package.json, Cargo.toml, etc.)
- **Environment Validation**: Compares installed tool versions against project requirements (Node.js, Python, Docker)
- **Intelligent Warnings**: Proactively warns about merge conflicts, missing dependencies, port conflicts, and more
- **Rich Terminal Output**: Beautiful formatted output with exit codes, timing, and separated stdout/stderr panels
- **Configurable**: YAML-based configuration for customizing providers, plugins, and auto-execution behavior
- **Explain Mode**: Analyze previously failed commands to understand what went wrong


### Interactive Repair Flow

When a command fails, Terminal Copilot now guides you through an interactive repair process:

```
$ terminal-copilot run "npm install"

# ... command output ...
# Command fails

🔍 Root Cause

Node version mismatch in package.json engines requirement.

💡 Suggested Commands

1. corepack enable
2. pnpm install  
3. Cancel

> 1
Executing: corepack enable
# ... fix attempt output ...
✓ Fix succeeded!
```

### How It Works

1. **Failure**: Command executes and fails
2. **Diagnosis**: AI analyzes the failure and provides root cause + suggested fixes
3. **Choose Fix**: You select a suggested command to try
4. **Execute**: The chosen fix is executed
5. **Succeeded?**: 
   - Yes → Done! Returns success
   - No → Re-investigates using the new output, looping back to step 3

This creates an interactive repair loop that continues until either:
- A fix succeeds
- You choose to cancel

### Using with `explain`

The interactive repair loop is also available when analyzing a previously failed command:

```bash
terminal-copilot explain
# Shows diagnosis with suggested fixes
# You can select and execute a fix directly
```



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

### Environment Validation

Validate your environment against project requirements to catch compatibility issues:

- **Node.js**: Compares installed version against `package.json` `engines.node` requirement
- **Python**: Compares installed version against `pyproject.toml` `python_requires` requirement
- **Docker**: Checks if Docker daemon is running when Dockerfile/docker-compose.yml exists
- **Dependencies**: Detects missing dependency directories (node_modules, Cargo.lock)

```bash
# Validate environment compatibility
terminal-copilot validate

# Validate in a specific directory
terminal-copilot validate --path /path/to/project
```

### Plugin Preflight Checks

Each plugin now provides preflight checks that run automatically before command execution. These checks detect common issues specific to each tool ecosystem:

**NPM Plugin Example:**
```
✓ package.json
✓ node v22.2.1
✓ npm 9.2.0
✓ lockfile
⚠ node_modules missing (run npm install)
```

**Docker Plugin Example:**
```
✓ Docker installed Docker version 24.0.0
✓ daemon running
⚠ Dockerfile
⚠ compose plugin missing
```

### Available Plugin Checks

- **npm**: package.json, node version, npm version, lockfile, node_modules
- **docker**: Docker installed, daemon running, Dockerfile, compose plugin
- **git**: Git installed, in git repository
- **rust**: rustc, cargo, Cargo.toml
- **go**: Go installed, go.mod
- **python**: Python installed, virtual environment

### Intelligent Warnings

Instead of waiting for failure, Terminal Copilot proactively detects potential issues before command execution:

- **Git merge conflicts**: Warns when your working directory has unresolved merge conflicts
- **Docker daemon status**: Warns when Docker is not running before executing docker commands
- **Port conflicts**: Warns when common development ports (3000, 3001, 4000, 5000, 5173, 8000, 8080, 9000) are already in use

Example warnings displayed before execution:

```
⚠ Git has merge conflicts in your working directory
  This build is likely to fail. Resolve conflicts before proceeding.

⚠ Docker daemon is not running
  Start Docker: 'systemctl start docker' or launch Docker Desktop

⚠ Port 3000 is already in use
  Another process is using port 3000. Check with 'lsof -i :3000' or kill the process.
```

