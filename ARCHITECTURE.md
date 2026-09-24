# Terminal Copilot — Architecture Diagram

## High-Level Overview

Terminal Copilot is a CLI tool that wraps shell commands with diagnostics and LLM-powered investigation. The core flow is:

1. **CLI parses user command** → runs preflight checks
2. **Command executed** via `runner.py`
3. **Plugin detection** matches the command to a domain-specific plugin
4. **Context collection** gathers project/environment metadata from the plugin
5. **If the command fails** → agent graph kicks in:
   - Initialize agent with prompt + tool schema
   - LLM reasons → requests a tool
   - Tool executes → result returned → LLM re-reason
   - Loop until LLM signals `finish` or max iterations reached
6. **Diagnosis rendered** to the user via Rich panels and tree views

---

## Module Dependency Map

```
cli.py
├── config.py           (load_config)
├── models.py           (CommandResult, InvestigationResult, AgentState)
├── agent.py            (run_agent, run_agent_streaming)
├── plugins.py          (get_all_plugins, get_plugin_by_name, find_matching_plugin,
│                        detect_project_type, validate_environment, PROJECT_MARKERS)
├── history.py          (record_failed_command, load_last_failed)
├── investigation.py    (run_investigation, build_investigation)
├── preflight.py        (run_preflight, format_preflight_result, predict_potential_issues,
│                        run_intelligent_warnings)
└── runner.py           (run_command)

agent.py
├── models.py           (AgentState, CommandResult, InvestigationResult, ToolResult, ToolRequest)
├── runner.py           (run_command)
├── plugins.py          (find_matching_plugin, get_plugin_by_name)
├── llm.py              (call_llm_for_tool_request, build_prompt)
└── tools.py            (get_all_tools, get_tool_by_name, get_tools_schema)

plugins.py
├── tools.py            (get_tools_schema — used by BasePlugin? no, only by agent)
└── _run_quick_command  (internal helper)

preflight.py
├── plugins.py          (find_matching_plugin, get_plugin_by_name,
│                        detect_project_type, validate_environment,
│                        _detect_framework, _find_project_root, PreflightCheck)
└── _run_quick_check    (internal helper)

tools.py
├── models.py           (ToolResult)
└── _find_project_root  (internal helper)

llm.py
├── models.py           (ToolRequest)
└── dotenv / google-genai

investigation.py
├── models.py           (InvestigationData, InvestigationResult, CommandResult)
└── llm.py              (diagnose_investigation, parse_diagnosis)

workflow.py  (legacy wrapper)
├── models.py           (WorkflowState)
└── agent.py            (run_agent)

history.py
## Function Interaction Diagram
### 1. CLI Entry Point (`cli.py`)

```
     [copilot command] ─────────────────────────► copilot_cmd()
                                                (cli.py)
                                                       │
          ┌────────────────────────────────────────────┼─────────────────────┐
          │                                            │                     │
          ▼                                            ▼                     ▼
   run_preflight()                              explain_cmd()        validate_env_cmd()
   (preflight.py)                                   (cli.py)             (cli.py)
          │                                            │                     │
          ▼                                            ▼                     ▼
   run_command() ◄────────────── load_last_failed()   Preflight checks   validate_environment()
   (runner.py)                  (history.py)             via plugin       (plugins.py)
          │
          ▼
   find_matching_plugin() ──► collect_context()
   (plugins.py)                  (plugins.py)
          │
          ▼
   run_agent()  ──► Agent Graph (see below)
   (agent.py)
          │
          ▼
   display_result() / _display_diagnosis() / _prompt_for_fix()
   (cli.py)
```

### 2. Agent Graph (`agent.py`)

```
build_agent_graph()
       │
       ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                    LangGraph StateGraph — compiled graph                    │
 │  ┌────────────────────┐   ┌────────────────────┐   ┌────────────────────┐  │
 │  │execute_command_node│──▶│find_plugin_node    │──▶│collect_context_node│  │
 │  │(run_command)       │   │(find_matching_     │   │(plugin.collect_    │  │
 │  │                    │   │ plugin)            │   │ context)           │  │
 │  └────────────────────┘   └────────────────────┘   └─────────┬──────────┘  │
 │                                                              │              │
 │                                        ┌─────────────────────┴──────────────┐│
 │                                        │ should_investigate()               ││
 │                                        │ → "initialize_agent" if failed     ││
 │                                        │ → END if success                   ││
 │                                        └─────────────────────┬──────────────┘│
 │                                                            │                 │
 │                                                            ▼                 │
 │  ┌─────────────────────────────────────────────────────────────────────────┐ │
 │  │ initialize_agent_node()                                                 │ │
 │  │   • build_prompt() — assembles system + user context + tool schema     │ │
 │  │   • seeds messages, tool_results=[], agent_iteration=0                 │ │
 │  └─────────────────────────────────┬───────────────────────────────────────┘ │
 │                                    │                                         │ │
 │                                    ▼                                         │ │
 │  ┌─────────────────────────────────────────────────────────────────────────┐ │
 │  │ reason_node()                                                           │ │
 │  │   • call_llm_for_tool_request(messages, tools_schema)                  │ │
 │  │   • stores ToolRequest or finish data in state                          │ │
 │  └─────────────────────────────────┬───────────────────────────────────────┘ │
 │                                    │                                         │ │
 │                              ┌─────┴───────────┐                             │ │
 │                              │should_continue  │                             │ │
 │                              │_agent()         │                             │ │
 │                              └─────┬──────┬────┘                             │ │
 │  Entry: execute_command            └──────┘      └──────┘                    │ │
 │  Exits: END (finish / handle_max_iterations)                                  │ │
 └─────────────────────────────────────────────────────────────────────────────────┘
```

#### Node functions in detail

| Node | File | Responsibility |
|---|---|---|
| `execute_command_node` | `agent.py:38` | Calls `run_command(state.command)` → stores `CommandResult` |
| `find_plugin_node` | `agent.py:44` | Calls `find_matching_plugin(state.command)` → stores plugin name |
| `collect_context_node` | `agent.py:50` | If plugin found, calls `plugin.collect_context(state.command)` → stores context dict |
| `should_investigate` | `agent.py:60` | If `command_result.success == False` → `"initialize_agent"`, else → `END` |
| `initialize_agent_node` | `agent.py:70` | Builds first LLM prompt via `build_prompt()`, seeds `messages`, `agent_iteration=0` |
| `reason_node` | `agent.py:102` | Calls `call_llm_for_tool_request()` with messages + tool schema → stores `ToolRequest` or finish data |
| `execute_tool_node` | `agent.py:152` | Looks up tool via `get_tool_by_name()`, calls `tool.execute()`, appends `ToolResult` to `tool_results` |
| `should_continue_agent` | `agent.py:178` | If tool requested and `finish=False` → `"execute_tool"`, else if finish → `"finish"`, else → `"end"` (→ `handle_max_iterations`) |
| `finish_node` | `agent.py:204` | Parses final LLM response → builds `InvestigationResult`, stores in `diagnosis` |
| `handle_max_iterations` | `agent.py:235` | Returns fallback diagnosis when loop exceeded `MAX_AGENT_ITERATIONS=10` |

#### Public API

- `build_agent_graph()` → `StateGraph` (compiled)
- `run_agent(command: str) -> AgentState` — invokes graph, returns final state
- `run_agent_streaming(command: str)` → generator yielding `{type, node, data}` events for live UI
│- `run_agent_streaming(command: str)` → generator yielding `{type, node, data}` events for live UI

## Plugin System (`plugins.py`)


├── models.py           (CommandResult, InvestigationData)
```
