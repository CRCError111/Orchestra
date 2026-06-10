# Orchestra / Agent Control

Orchestra is a lightweight local orchestrator for agent-driven work. It stores project tasks in SQLite, assigns compatible tasks to registered executors, tracks task status, records structured results, exposes a web UI, and can ask for human input through Telegram.

The project is intentionally small: Python standard library, SQLite, static HTML/CSS/JavaScript, and no required third-party Python packages.

## What It Does

- Imports task plans from JSON or a human-readable numbered list.
- Tracks tasks by status: `pending`, `running`, `review`, `blocked`, `done`, `failed`.
- Assigns tasks to workers/dialogs by `project_id`, capabilities, dependencies, priority, and optional explicit worker assignment.
- Uses an inbox/outbox file protocol for executor communication.
- Records structured task results: summary, artifacts, checked criteria, and test output.
- Provides a web UI with Kanban board, import screen, reports, executor settings, Telegram settings, project colors, and task details.
- Supports Telegram operator commands for task inspection, notes, approval, blocking, and resuming.
- Supports portable packaging for transfer to another Windows machine.

## Typical Use Cases

- Feature chains: backend -> UI -> tests -> docs.
- Parallel work across several Codex dialogs.
- Code review flow: one worker implements, another reviews.
- Regression tests and smoke-test runs.
- Release preparation: changelog, build, smoke-test, packaging.
- Documentation workflows: plan, draft, edit, final check.
- Hardware/firmware pipelines: schematic, ERC/DRC, firmware build, reports.
- Research tasks: collect facts, verify sources, summarize.
- Human approval workflows: stop and ask in Telegram.

## Requirements

- Python 3.11+
- Windows PowerShell for the helper scripts in `packaging/`
- A modern browser for the UI

No external Python dependencies are required.

## Quick Start

From the repository root:

```powershell
python -m agent_control.cli init
python -m agent_control.cli import-plan .\examples\plan.json
python -m agent_control.cli ui --host 127.0.0.1 --port 8767
```

Open:

```text
http://127.0.0.1:8767
```

Run tests:

```powershell
python -m unittest discover -s tests
```

## Core Concepts

### Task

A task is a structured unit of work. It has:

- `id`
- `project_id`
- `title`
- `description`
- `required_permissions`
- `required_capabilities`
- `expected_output`
- `acceptance_criteria`
- `priority`
- `depends_on`
- optional `assigned_worker_id`
- optional `test_command`
- `status`
- `notes`

The orchestrator always chooses lower `priority` values first. Dependencies must be completed before a dependent task becomes eligible.

### Worker / Executor

A worker represents an executor such as a Codex dialog, another local agent process, or a manually operated session.

Workers have:

- `id`
- `project_id`
- `dialog_name`
- `custom_name`
- `capabilities`
- `status`: `idle`, `busy`, `offline`
- `current_task_id`
- `lease_expires_at`

If `custom_name` is empty, the UI displays the worker as:

```text
Project/Dialog
```

Example:

```text
New project2/Create agent orchestrator
```

### Project

Tasks and workers are scoped by `project_id`. A worker only receives tasks from its own project, unless you register another worker for another project.

Each project gets a stable automatic color in the UI. Project colors appear on Kanban cards, task details, reports, and worker cards. Colors can be overridden in **Settings -> Project Colors**.

### Lease

When a worker claims a task, the orchestrator creates a lease and marks the worker as `busy`. If a lease expires, the task can be returned to `pending` and assigned again.

## Web UI

Start the UI:

```powershell
python -m agent_control.cli ui --host 127.0.0.1 --port 8767
```

The UI contains:

- **Board**: Kanban board by task status, workers, selected task details, status actions, priority editor, notes, and work results.
- **Import**: paste a numbered task list and import it as structured tasks.
- **Reports**: completed tasks with recorded results, and incomplete tasks with latest reasons or notes.
- **Settings**: worker registration/editing, offline/active worker management, Telegram credentials, auto-refresh, and project color overrides.

Board features:

- Kanban columns can be collapsed by clicking their headers.
- On first page load, only `pending` is expanded.
- Tasks can be dragged between status columns.
- Dragging a task to another column changes its status.
- Task priority can be edited from the task detail panel.
- The detail panel keeps the last selected task even if its Kanban column is outside the visible horizontal scroll.

## Task Statuses

- `pending`: ready for assignment when dependencies and capability requirements are satisfied.
- `running`: assigned to a worker.
- `review`: completed by an agent, but acceptance or tests did not fully pass.
- `blocked`: waiting for operator decision or external input.
- `done`: accepted as complete.
- `failed`: failed execution or manually marked failed.

## Importing Task Plans

### Import JSON

```powershell
python -m agent_control.cli import-plan .\examples\plan.json
```

Example task:

```json
{
  "id": "build-web-ui",
  "project_id": "orchestrator",
  "title": "Build web UI",
  "description": "Create the operator UI.",
  "required_permissions": ["read_workspace", "write_files"],
  "required_capabilities": ["frontend", "python"],
  "expected_output": "A usable web UI.",
  "acceptance_criteria": [
    { "id": "ui-renders", "text": "The UI renders in the browser." }
  ],
  "priority": 10,
  "depends_on": [],
  "test_command": "python -m unittest discover -s tests"
}
```

### Import Human-Readable Text

Preferred: use the **Import** tab in the UI.

CLI alternative:

```powershell
python -m agent_control.cli import-text-plan .\examples\plan.txt --project orchestrator --cap docs,python --perm read_workspace,write_files
```

Supported numbering:

```text
1. Prepare architecture
Describe lifecycle, status transitions, workers, and acceptance.

1.1 Implement worker registration
Add commands and UI for registering dialogs as workers.

1.2 Implement task assignment
Workers should receive compatible tasks only.

2) Build operator UI
```

Each numbered item becomes a task. Unnumbered lines after an item become the task description and expected output.

Nested items automatically depend on their parent:

- `1.1` depends on `1`
- `2.3.1` depends on `2.3`

## Worker Protocol

Register a dialog as a worker:

```powershell
python -m agent_control.cli worker-register `
  --id ui-1 `
  --project "New project2" `
  --dialog "UI improvements" `
  --label "UI improvements" `
  --cap frontend,python,tests
```

Claim the next compatible task:

```powershell
python -m agent_control.cli worker-next ui-1
```

The orchestrator writes:

```text
.agent_control/inbox/ui-1/task.json
```

The worker reads that file, performs the task, then writes:

```text
.agent_control/outbox/ui-1/result.json
```

Complete the task:

```powershell
python -m agent_control.cli worker-complete ui-1
```

Block the task and request operator input:

```powershell
python -m agent_control.cli worker-block ui-1 "Need a decision about Telegram auth storage"
```

### Worker Result Format

```json
{
  "summary": "Implemented the web UI and connected it to /api/state.",
  "artifacts": [
    "agent_control/web.py",
    "agent_control/static/app.js",
    "agent_control/static/styles.css"
  ],
  "criteria_checked": ["ui-renders", "state-loads"],
  "needs_consultation": false,
  "consultation_question": ""
}
```

The UI shows this in the selected task detail panel under **Work Result**. If the task had a `test_command`, the latest test command, return code, stdout, and stderr are also shown.

## One-Off Agent Command

For simple automation, the orchestrator can run a command for one task:

```powershell
python -m agent_control.cli run-once --agent-command "python .\examples\fake_agent.py --task {task_file} --result {result_file}"
```

The command receives:

- `{task_file}`: JSON task file
- `{result_file}`: where it must write the result JSON

Acceptance requires:

- agent command returns code `0`;
- `needs_consultation` is false;
- `test_command` is absent or returns code `0`;
- all acceptance criteria are listed in `criteria_checked`.

Otherwise the task goes to `review`, `blocked`, or `failed`.

## Telegram

Start Telegram polling:

```powershell
python -m agent_control.cli telegram
```

The bot token and allowed chat IDs can be saved in **Settings**. Environment variables are still supported as fallback:

```powershell
$env:TELEGRAM_BOT_TOKEN="123:abc"
$env:TELEGRAM_ALLOWED_CHAT_IDS="123456789,-1001234567890"
python -m agent_control.cli telegram
```

To find chat IDs:

1. Send `/start` to your bot in Telegram.
2. Open **Settings**.
3. Click **Fetch recent chats**.
4. Click a returned chat to add its ID to `Allowed chat IDs`.
5. Click **Save settings**.

Telegram commands:

- `/tasks`: list tasks
- `/task <id>`: show task details
- `/note <id> <text>`: add a task note
- `/approve <id>`: mark task as done
- `/block <id> <reason>`: block task
- `/resume <id>`: return task to pending

If `Allowed chat IDs` is set, all other chats are denied.

## Reports

The Reports view separates:

- completed tasks with their latest structured `agent_result`;
- incomplete tasks with their latest note, blocking reason, review reason, lease expiration, or test event.

This makes it possible to review what was done, which files were changed, which criteria were checked, and why unfinished tasks are still open.

## Settings

Settings are stored in SQLite:

```text
.agent_control/state.sqlite3
```

Current settings include:

- Telegram bot token
- allowed Telegram chat IDs
- auto-refresh interval
- project color overrides
- worker display metadata

Bot tokens are never shown back in the UI after being saved.

## Portable Package

Create or use the portable package:

```text
dist/agent-control-portable.zip
```

Package helper scripts:

- `packaging/install.ps1`
- `packaging/run-ui.ps1`
- `packaging/register-worker.ps1`
- `packaging/README-portable.md`

Install on another Windows computer:

```powershell
.\packaging\install.ps1
.\packaging\run-ui.ps1 -Port 8767
```

By default, portable runtime data is stored outside the app folder:

```text
%USERPROFILE%\.agent_control\state.sqlite3
```

The portable archive should not include the original machine's `.agent_control` runtime state.

## CLI Reference

General:

```powershell
python -m agent_control.cli init
python -m agent_control.cli status --events
python -m agent_control.cli ui --host 127.0.0.1 --port 8767
python -m agent_control.cli telegram
```

Plans:

```powershell
python -m agent_control.cli import-plan .\examples\plan.json
python -m agent_control.cli import-text-plan .\examples\plan.txt --project orchestrator --cap python,docs --perm read_workspace,write_files
```

Tasks:

```powershell
python -m agent_control.cli set-status <task-id> done
python -m agent_control.cli note <task-id> "Operator note"
python -m agent_control.cli task-assign <task-id> <worker-id>
python -m agent_control.cli task-unassign <task-id>
```

Workers:

```powershell
python -m agent_control.cli worker-register --id ui-1 --project orchestrator --dialog "UI dialog" --cap frontend,python,tests
python -m agent_control.cli workers
python -m agent_control.cli worker-next ui-1
python -m agent_control.cli worker-complete ui-1
python -m agent_control.cli worker-block ui-1 "Need operator decision"
```

Run one task:

```powershell
python -m agent_control.cli run-once --dry-run
python -m agent_control.cli run-once --agent-command "python .\examples\fake_agent.py --task {task_file} --result {result_file}"
```

Use a custom database path:

```powershell
python -m agent_control.cli --db C:\path\state.sqlite3 status
```

The `--db` option is global and must be placed before the subcommand.

## Repository Layout

```text
agent_control/
  cli.py             CLI entry point
  models.py          task, worker, and result models
  orchestrator.py    task selection and execution logic
  store.py           SQLite storage
  telegram.py        Telegram bot integration
  text_plan.py       numbered text-plan parser
  web.py             HTTP server and JSON API
  static/            browser UI
examples/
  plan.json
  plan.txt
  fake_agent.py
packaging/
  install.ps1
  run-ui.ps1
  register-worker.ps1
tests/
  test_orchestrator.py
```

## Notes And Limitations

- This is a local orchestrator, not a distributed queue.
- SQLite is the source of truth.
- Workers communicate through CLI and JSON files.
- Current worker push into an already-open Codex dialog is not automatic; dialogs pull tasks with `worker-next`.
- Offline workers are hidden from the worker strip by default but can be shown in Settings.
- Dragging cards between Kanban columns changes task status immediately.
- Priority is numeric: lower number means earlier selection.
- Telegram polling is a foreground process started with `python -m agent_control.cli telegram`.
- The project currently targets local Windows/Python workflows first, though the core Python code is mostly platform-neutral.
