# Terminal Copilot — Interview Preparation Guide

> **Scope:** This guide is grounded in the current repository at `/home/smit-bafna/Projects/terminal-copilot` and is written for a practical interview. The answers use first-person wording where appropriate, but keep only claims that match your actual contribution. The project is currently a beta/MVP (`0.1.5` in `pyproject.toml`), so limitations are called out instead of presenting them as finished features.

## 1. Project summary

**Project root:** `/home/smit-bafna/Projects/terminal-copilot`

**One-line description:** Terminal Copilot is a Python CLI that wraps shell commands with proactive environment checks and, when a command fails, uses a Gemini-powered, tool-calling diagnostic agent to investigate the failure and offer an interactive repair path.

**Technology choices:**

- Python 3.12+
- Typer and Rich for the CLI and terminal UX
- Pydantic v2 for validated data contracts
- LangGraph for the stateful investigation workflow
- `google-genai` for Gemini integration
- PyYAML for optional user configuration
- `subprocess` for command execution
- `pytest` for tests
- GitHub Actions plus `uv` for package building and PyPI publishing

**Terminology:**

- **Preflight:** checks performed before the requested command runs.
- **Plugin:** an ecosystem-specific adapter, such as npm, Docker, Git, Rust, Go, Python, or C/C++.
- **Agent tool:** an investigation operation, such as reading a file or checking Git status; it is different from the original user command.
- **Repair command:** a command proposed by the model. The current interactive flow requires the user to select it before executing it.

## 2. Elevator-pitch answers

### Q1. Tell me about this project.


## 3. Architecture and request flow

### Q4. Walk me through `terminal-copilot run npm install`.

**Answer:**

> The `run` command in `/home/smit-bafna/Projects/terminal-copilot/terminal_copilot/cli.py` joins the command arguments into one string and calls the shared implementation. Unless `--skip-preflight` is supplied, `run_preflight` identifies the npm plugin, runs plugin checks and command-specific checks, and displays warnings or stops on errors. The CLI then shows a prediction preview and asks for confirmation. Next, the LangGraph workflow executes the command through `/home/smit-bafna/Projects/terminal-copilot/terminal_copilot/runner.py`, which captures output, stderr, exit code, and elapsed time. On success, the workflow ends. On failure, the matching plugin collects context, the agent initializes with the failure data, and Gemini chooses either a tool call or a final diagnosis. The CLI renders the result and offers an interactive repair loop when suggestions are available.

### Q5. What are the main layers?

**Answer:**

> The implementation is layered by responsibility:

> - `/home/smit-bafna/Projects/terminal-copilot/terminal_copilot/cli.py`: Typer commands, Rich rendering, confirmation, and repair interaction.
> - `/home/smit-bafna/Projects/terminal-copilot/terminal_copilot/preflight.py`: deterministic preflight and warning checks.
> - `/home/smit-bafna/Projects/terminal-copilot/terminal_copilot/plugins.py`: ecosystem matching, context collection, and plugin checks.
> - `/home/smit-bafna/Projects/terminal-copilot/terminal_copilot/agent.py`: LangGraph nodes, conditional edges, and the bounded agent loop.
> - `/home/smit-bafna/Projects/terminal-copilot/terminal_copilot/llm.py`: Gemini requests, tool-call prompt construction, and JSON parsing.
> - `/home/smit-bafna/Projects/terminal-copilot/terminal_copilot/tools.py`: investigation tools exposed to the model.
> - `/home/smit-bafna/Projects/terminal-copilot/terminal_copilot/models.py`: Pydantic state and result contracts.
> - `/home/smit-bafna/Projects/terminal-copilot/terminal_copilot/history.py`: persistence of the most recent failed command.
> - `/home/smit-bafna/Projects/terminal-copilot/terminal_copilot/runner.py`: the subprocess boundary.
>
> This separation lets me test or replace the LLM without rewriting command execution, and keeps ecosystem knowledge out of the core workflow.

### Q6. Why use a graph instead of a simple `while` loop?

## 4. Plugins and deterministic diagnostics

### Q9. What problem do plugins solve?

**Answer:**

> npm, Docker, Git, Rust, Go, Python, and C/C++ have different failure modes and useful context. A plugin owns that ecosystem-specific knowledge through three main operations: `supports`, `collect_context`, and `preflight_checks`. The core workflow does not need to know whether a command is Docker or Rust; it only asks the registry for a matching plugin and consumes the common result. This is a strategy-like abstraction with a small registry rather than a large `if command.startswith(...)` block in the CLI.

### Q10. How does plugin matching work?

**Answer:**

> `/home/smit-bafna/Projects/terminal-copilot/terminal_copilot/plugins.py` returns a list of plugin instances and selects the first plugin whose `supports` method recognizes the command. The current plugins use command prefixes such as `npm`, `pnpm`, `yarn`, `docker`, and `git`. If nothing matches, the plugin name is `unknown`. This is intentionally simple and predictable for the MVP. A later improvement would be to use a parser and a score or priority system, especially for commands that contain multiple tools or shell operators.

### Q11. What context does a plugin collect?

**Answer:**

> Context is structured rather than a raw terminal dump. Depending on the plugin, it can include the project root and current directory, tool versions, relevant marker files, dependency or lockfile status, Docker daemon state, Git branch and working-tree state, compiler-related files, or environment information. The agent receives this as JSON-like data, which makes it easier to reason about individual fields and keeps prompts smaller than dumping every available file.

### Q12. What is the difference between preflight and diagnosis?

**Answer:**

> Preflight is deterministic and runs before the user command. It can stop a clearly invalid request, such as a missing required project file or a dangerous known target, or show a warning. Diagnosis runs after a command has failed and is adaptive. The agent can decide which evidence to request based on the error and previous tool results. Keeping these paths separate prevents an LLM from being used for checks that a regular expression or subprocess exit code can answer reliably and more cheaply.

### Q13. How does project detection work?

## 5. Gemini and the agent loop

### Q15. How does the agent call tools?

**Answer:**

> The tool registry exposes a fixed set of tool names, descriptions, and argument schemas. The system prompt tells the model to request one tool at a time and return JSON. The LLM response is parsed into the `ToolRequest` model. The graph then looks up the tool in `/home/smit-bafna/Projects/terminal-copilot/terminal_copilot/tools.py` and executes it with the supplied arguments. The result is added to the conversation as a new message, and the model decides what to do next. This is a controlled observation loop rather than giving the model unrestricted access to the machine.

### Q16. Which tools are available?

**Answer:**

> The registry currently includes `read_file`, `list_directory`, `file_exists`, `git_status`, `git_diff`, `git_branch`, `run_command`, `which`, `find_file`, and `read_package_json`. The tools cover common project and environment questions while keeping the schema small. A production version should add stronger permissioning, per-tool read/write capabilities, output limits, and a better abstraction for shell execution. The presence of a tool in the registry does not by itself make it safe for arbitrary use.

### Q17. Why request one tool at a time?

**Answer:**

> One tool at a time makes the conversation causal and easier to inspect. The model sees the result before choosing the next step, and the CLI can show a clear progress event. It also reduces the amount of state that must be reconstructed after a malformed multi-tool response. The trade-off is latency: several sequential tool calls take longer than batching them. For a small, interactive diagnostic tool, predictability and observability are more valuable than minimizing every round trip.

### Q18. How do you prevent an infinite agent loop?

**Answer:**

> There are three protections: a maximum iteration count, an explicit `finish` decision, and error handling. `should_continue_agent` ends the loop when the iteration reaches `MAX_AGENT_ITERATIONS`, when an error is present, or when there is no valid tool request. If the limit is reached, the workflow returns a zero-confidence diagnosis rather than pretending that it found a definitive answer. I would also add per-run token and cost budgets in a production version.

### Q19. Why use structured JSON instead of parsing free-form Markdown?

## 6. Security, safety, and privacy

### Q23. Is it safe to execute commands?

**Answer:**

> There are two different command paths that must be distinguished. The main `run` command is intentionally a command wrapper, so the user is explicitly asking Terminal Copilot to execute a command. A suggested repair is different: the model proposes it, but the current CLI requires the user to select it before the repair runner executes it. The investigation `run_command` tool has a prefix-based blocklist and a read-only-oriented prompt, but it still invokes a shell. That is not a sandbox. I would describe the current implementation as having safety guardrails, not as a hardened security boundary.

### Q24. What would you improve about command execution?

**Answer:**

> I would replace the prefix blocklist with an explicit capability model: an allowlist of diagnostic binaries, no arbitrary shell by default, `exec`-style argument arrays, resource and time limits, a separate opt-in permission for state-changing commands, and a dry-run preview. For investigation, I would run in an isolated temporary environment or a container with a read-only filesystem. I would also redact secrets, limit output, record an audit trail, and make the distinction between read-only and mutating operations visible in the UI. A denylist is easy to bypass and should not be the final safety design.

### Q25. Does the model have unrestricted access to the filesystem?

**Answer:**

> The tool descriptions say that relative paths are resolved from the project root, and many file operations use that root as their base. However, some tools accept absolute paths, so the current code is not a strict project-root sandbox. The system prompt also asks the model to avoid dangerous tools, but prompts are guidance, not enforcement. I would add path canonicalization, an allowed-root check, symlink handling, and tool-specific permissions before treating this as production-grade isolation.

### Q26. How would you protect an API key and user data?

**Answer:**

> The key is read from `GEMINI_API_KEY` or `GOOGLE_API_KEY`, and the repository ignores `.env`. In production I would document secret scanning, avoid sending unnecessary source code or environment variables to the model, redact tokens and credentials from tool output, encrypt or tightly permission local history, and provide a retention/deletion policy. The `doctor` command can tell a user whether a key is present, but it should never print the key value.

### Q27. What are the biggest current security limitations?

**Answer:**

## 7. Data, error handling, and engineering trade-offs

### Q28. How is failure data represented?

**Answer:**

> `CommandResult` stores the command, stdout, stderr, exit code, execution time, and a success flag. `InvestigationData` carries the command, exit code, output, plugin name, and structured context to the diagnostic layer. This avoids passing loose strings through the workflow and makes it possible to render, persist, and test the data independently. History serializes the same information to `~/.terminal-copilot/history.json`, allowing `explain` to analyze the last failure without executing the original command.

### Q29. How are timeouts and subprocess errors handled?

**Answer:**

> The main runner uses `subprocess.run` with a 300-second timeout, captures stdout and stderr, and converts timeout or other exceptions into a `CommandResult` with a nonzero exit code and an error message. The diagnostic tools use shorter timeouts and return an error or `(timed out)` result instead of crashing the entire CLI. A production version would distinguish timeout, launch failure, cancellation, and signal termination, and it would consider streaming output for long-running commands.

### Q30. Why use a plugin registry rather than dynamic Python entry points?

**Answer:**

> The current registry is a simple, explicit list of plugin instances. It is easy to inspect, deterministic, and convenient for an MVP. The trade-off is that adding a plugin requires editing the central registry and does not support third-party distribution. I would keep the common plugin interface and add entry-point discovery later, along with version compatibility, duplicate-name handling, and per-plugin configuration.

### Q31. How is configuration handled?

**Answer:**

> `/home/smit-bafna/Projects/terminal-copilot/terminal_copilot/config.py` loads YAML with `safe_load` into an `AppConfig` Pydantic model. A missing default config produces an empty configuration, while an explicitly requested file must exist. The model supports provider, auto-execute, and plugin-list fields, but the current workflow does not use every field to alter behavior everywhere. I would either wire those settings into the runtime or document them as planned configuration so users do not assume they are fully implemented.

### Q32. How would you make the system observable?

**Answer:**

> I would record structured events for command start and finish, selected plugin, preflight issues, tool requests, tool duration and result status, model errors, latency, token usage, and the final diagnosis. Logs should redact command output and secrets while retaining enough metadata to debug failures. I would also add counters for success rate, false diagnosis rate, average investigation steps, and repair acceptance. The existing streaming UI is a useful first observability surface, but it is not a substitute for durable telemetry.

## 8. Testing and quality

### Q33. What is your testing strategy?

## 9. Common follow-up questions

### Q37. Why not just send all project files to the LLM?

**Answer:**

> It would be expensive, slow, and risky. Files may contain secrets or irrelevant code, and a large context can exceed limits without improving the diagnosis. The plugin context and targeted tools let the agent retrieve only evidence relevant to the failure. I would still add a safe retrieval strategy for larger projects, but it should be selective, bounded, and auditable.

### Q38. Why not make the model output one final answer immediately?

**Answer:**

> A one-shot prompt is simpler and cheaper, but it cannot adapt when the initial context is insufficient. The agent loop is valuable for cases where the model needs to check a lockfile, inspect Git state, or confirm a tool version. For simple failures, deterministic preflight and a one-shot diagnosis are enough. A useful optimization would be to route obvious errors to the deterministic path and reserve the full agent for ambiguous failures.

### Q39. How would you evaluate diagnosis quality?

**Answer:**

> I would build a benchmark of real failures with expected root causes, relevant evidence, and acceptable fix families. I would measure top-level root-cause accuracy, whether the diagnosis is evidence-supported, invalid-fix rate, average tool calls, latency, and cost. I would also have reviewers score whether suggestions are safe and useful. Accuracy alone is not enough if a high percentage of suggestions cannot be executed or require destructive actions.

### Q40. How do you explain the value of LangGraph in a non-LLM interview?

**Answer:**

> LangGraph is not doing the domain reasoning; it is coordinating a stateful process with explicit branches and bounded repetition. The value is testability and visibility: I can inspect the state, stream progress, enforce a maximum number of steps, and replace the model without rewriting the subprocess or plugin layers. If the graph only handled one request, an ordinary function would be simpler. I chose a graph because the project has a genuine observe-decide-act loop.

### Q41. What was the hardest part?

**Answer:**

> The hardest part was making the agent reliable at the boundary between untrusted model output and real machine actions. The model needed enough freedom to investigate, but the application needed typed requests, bounded execution, clear errors, and a confirmation step for repairs. I also had to account for two paths that are easy to confuse: the user-requested command and the model's investigation tools. I would describe the solution as useful guardrails, not as a complete sandbox.

### Q42. What would you change if you started again?

## 10. Resume-to-evidence mapping

Use this table to connect resume claims to concrete evidence.

| Resume claim | Evidence to point to | Safe wording |
|---|---|---|
| Built an AI-powered terminal assistant | `terminal_copilot/agent.py`, `llm.py`, `tools.py` | “Implemented a Gemini-backed LangGraph diagnostic agent” |
| Added preflight validation | `preflight.py`, plugin `preflight_checks` methods | “Added deterministic and plugin-aware preflight checks” |
| Supported multiple ecosystems | `plugins.py` registry and plugin classes | “Added adapters for npm, Docker, Git, Rust, Go, Python, and C/C++” |
| Improved developer experience | `cli.py`, `runner.py`, `history.py` | “Added interactive CLI feedback, failure history, and guided repairs” |
| Used modern AI orchestration | `agent.py` and `pyproject.toml` | “Used LangGraph for bounded, stateful tool-calling” |
| Packaged the project | `.github/workflows/publish.yml`, `pyproject.toml` | “Added an automated PyPI publishing workflow” |

Avoid saying “securely executes arbitrary commands,” “100% accurate diagnosis,” or “fully autonomous” for the current implementation. Say “guardrails,” “suggests,” and “requires user confirmation.”

## 11. Five-minute self-check

Before an interview, be able to answer these without opening the repository:

- [ ] What problem does the project solve?
- [ ] What are the CLI, workflow, plugin, LLM, tool, model, history, and runner layers?
- [ ] What does LangGraph add, and what does it not add?
- [ ] How are preflight checks different from agent diagnosis?
- [ ] How does the agent prevent an unbounded loop?
- [ ] What evidence does the model receive?
- [ ] Which tools can the model request?
- [ ] Where is user confirmation required?
- [ ] What are the main security limitations?
- [ ] How do you test without calling Gemini?
- [ ] What would you improve in the next release?
- [ ] Which parts of the project are implemented today versus planned?

## 12. Suggested opening statement

> “I’m presenting Terminal Copilot as a focused developer tool, not as a claim of a fully autonomous coding agent. Its core value is combining deterministic preflight checks with a bounded, evidence-driven investigation loop. The model can inspect a controlled set of project and environment facts, but the user remains in control of the original command and suggested repairs. The main engineering work is the boundary design: typed state, plugin isolation, bounded tool calls, predictable errors, and testability without a network dependency.”


**Answer:**

> I would define the security and configuration contracts earlier. I would introduce a dedicated execution policy interface, make all model-facing tool arguments typed, keep the legacy `explain` path separate from the agent path more clearly, and start with a small evaluation set before expanding the number of plugins. I would also keep the deterministic preflight checks small and focused, because every additional heuristic needs a test and a clear explanation of when it should be overridden.

### Q43. What should the interviewer test in a demo?

**Answer:**

> I would demo a harmless failing command in a temporary project, show preflight warnings, display the tool calls made by the agent, inspect a plugin's structured context, and then show the user-selected repair flow. I would separately run the non-AI commands such as `doctor`, `detect`, and `validate` to show that the product still provides useful value without an LLM. I would avoid using destructive commands in the demo and be explicit about the Gemini API key requirement.


**Answer:**

> The repository has `tests/test_cli.py` and `tests/test_preflight.py`, covering CLI behavior, data models, preflight helpers, plugin checks, and some mocked environment interactions. The most important pattern is dependency injection through patching: tests do not need real Docker, Git, or a Gemini call. I would expand coverage around the graph transitions, tool execution boundaries, malformed LLM JSON, history corruption, runner timeouts, and user confirmation paths. A production-quality suite should also use a fake LLM and temporary directories for deterministic end-to-end tests.

### Q34. How do you test an LLM-dependent feature without making the suite flaky?

**Answer:**

> I separate the pure orchestration contract from the provider call. The agent can be tested with a stub that returns a scripted sequence of `ToolRequest` objects: first request a tool, then finish. That lets the test assert the graph calls the tool, feeds the result back, and produces the expected diagnosis without network access. I would also test malformed JSON, an unknown tool, a tool exception, and the iteration limit. Provider integration tests can be opt-in and run only when an API key is available.

### Q35. What are the most important edge cases?

**Answer:**

> Important cases include successful commands, failures with empty stderr, commands with no matching plugin, missing project files, a stopped Docker daemon, a timeout, an unknown LLM tool, malformed model output, an interrupted prompt, a corrupt history file, and a suggested fix that itself fails. The current repair loop handles repeated failures interactively and allows cancellation, which is important because the first suggested fix is not guaranteed to resolve the problem.

### Q36. What would you improve in the next release?

**Answer:**

> My priority order would be: replace shell-based tool execution with a least-privilege execution service; add native structured output and retry handling to the Gemini client; improve agent evaluation with a fixed failure dataset; make configuration affect behavior consistently; expand the tests; and add redaction and history-retention controls. I would not prioritize adding more model autonomy before those foundations are in place.


> The most important limitations are shell-based execution, the prefix-based tool blocklist, acceptance of absolute paths, and the fact that the original command itself is intentionally powerful. History also contains command output and context on disk, so it may contain sensitive information. I would make these explicit in a security section of the README and prioritize a least-privilege execution layer before adding more autonomous behavior.


**Answer:**

> A structured `ToolRequest` makes the control flow explicit: the model chooses a tool, supplies arguments, or finishes with a root cause and commands. Pydantic validates the shape and gives the rest of the application typed fields. The legacy `explain` path still parses a text format for backwards compatibility, but the main agent path is designed around JSON. Native provider structured-output/schema enforcement would be a stronger next step than relying only on prompt instructions plus client-side validation.

### Q20. How do you reduce hallucinations?

**Answer:**

> I use a few complementary strategies: give the model the actual command, exit code, stdout, stderr, and structured plugin context; expose concrete tools for verification; require one tool call at a time; cap iterations; and make the UI show the evidence and the model's confidence. The system prompt also requires suggested commands to be executable commands rather than prose. These measures reduce hallucination but do not eliminate it, so the product keeps user confirmation before running a suggested repair.

### Q21. What happens if Gemini is unavailable or returns invalid JSON?

**Answer:**

> The API key is validated before the request. If the response cannot be parsed into a `ToolRequest`, the reason node records a zero-confidence error and stops instead of blindly executing an arbitrary string. The application surfaces the failure to the user. In a stronger implementation I would add retries with backoff for transient API errors, structured-output schema support, request timeouts, and a clear offline fallback that provides deterministic checks without an LLM call.

### Q22. How do you keep prompts within a reasonable size?

**Answer:**

> The investigation pipeline caps stderr at 2,000 characters, stdout at 500 characters, and the number of context keys at 30; long selected fields and long lists are also shortened. The agent keeps a bounded conversation window. This is a practical first step, but a better design would summarize or select evidence based on the command, preserve the beginning and end of an error when useful, and report what was omitted. Silent truncation can itself lose important information.


**Answer:**

> The code walks upward from the current directory to find a project root and checks marker files. Files such as `package.json`, `Cargo.toml`, `go.mod`, `pyproject.toml`, `requirements.txt`, `Dockerfile`, and compose files identify common project types. The `detect` and `validate` CLI commands expose this information. The limitation is that marker-based detection is heuristic; a monorepo or a command that operates on a different directory than the current one may need more explicit configuration in a future release.

### Q14. What does `doctor` check?

**Answer:**

> `doctor` provides a user-facing environment check for Git, Docker, Node, Python, a Gemini API key, configuration loading, plugin loading, and the history file. It is useful for installation support because it separates a missing local dependency from an application failure. The check is intentionally diagnostic; it does not attempt to repair the machine automatically.


**Answer:**

> The investigation naturally has conditional transitions: execute, match a plugin, collect context, decide whether failure investigation is needed, reason, execute a tool, return to reasoning, or finish. LangGraph makes those transitions explicit and gives us a state object that can be streamed. It also provides a natural place to enforce the iteration limit and add future nodes such as validation, observability, or human approval. The trade-off is a dependency and more state-management concepts than a small imperative loop would require.

### Q7. Describe the LangGraph state machine.

**Answer:**

> The main stages are: `execute_command`, `find_plugin`, `collect_context`, and `should_investigate`. Successful commands end there. Failed commands enter `initialize_agent`, then `reason`. The reason node asks Gemini for one JSON `ToolRequest`; `execute_tool` runs the selected investigation tool; the graph returns to `reason` with the result. This repeats until the model requests `finish`, an error occurs, or `MAX_AGENT_ITERATIONS` is reached. The finish node converts the response into an `InvestigationResult`. The bounded loop is important because an agent should not investigate forever or spend unlimited API calls on one failure.

### Q8. What is stored in `AgentState`?

**Answer:**

> `AgentState` contains the original command, its `CommandResult`, the matched plugin, plugin context, structured investigation data, diagnosis, error information, conversation messages, tool results, iteration count, and the latest `ToolRequest`. These are Pydantic models rather than an untyped dictionary, so boundaries between workflow stages are explicit. The state also makes the live CLI possible: the streaming wrapper emits node, tool-request, tool-result, and completion events.

**Answer:**

> I built Terminal Copilot to reduce the time spent diagnosing terminal failures. The CLI accepts a normal command, performs ecosystem-aware preflight checks, runs the command, and captures stdout, stderr, exit status, and timing. If it fails, a plugin collects structured project context and a Gemini-backed LangGraph agent can call read-only investigation tools such as file inspection, Git status, and version checks. The agent returns a root cause, confidence, and suggested fix commands. The user can select a suggested fix, run it through the same command runner, and continue interactively. I also added project detection, environment validation, a `doctor` command, failure history, and an `explain` command that diagnoses the last failure without re-running it.

### Q2. Why did you build it?

**Answer:**

> Terminal errors are often scattered across the command output, working directory, project configuration, and installed tool versions. A generic chatbot answer can miss that local context, while manually running several diagnostic commands is slow. I wanted a tool that catches predictable setup problems and then investigates unexpected failures using the actual environment. The design principle was to keep deterministic checks deterministic and use the LLM only where interpretation and adaptive investigation are useful.

### Q3. What are the three main capabilities?

**Answer:**

> First, the tool provides proactive checks: missing files, unavailable executables, Docker daemon problems, dependency directories, version requirements, merge conflicts, and port conflicts. Second, it provides failure diagnosis: it executes the command, identifies the relevant plugin, collects structured context, and lets an agent request more evidence. Third, it provides a guided repair loop: the user chooses a suggested command, sees its output, and can retry or cancel.
