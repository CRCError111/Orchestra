from __future__ import annotations

import json
import unittest
import uuid
from pathlib import Path

from agent_control.models import TaskStatus, Worker, WorkerStatus
from agent_control.orchestrator import Orchestrator
from agent_control.store import Store
from agent_control.telegram import TelegramBot
from agent_control.text_plan import parse_numbered_plan, parse_numbered_plan_text
from agent_control.web import reports_payload, state_payload


def test_root() -> Path:
    root = Path(".agent_control/test_runs") / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root


class OrchestratorTests(unittest.TestCase):
    def test_import_and_dry_run_accepts_first_task(self) -> None:
        root = test_root()
        plan = root / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "t1",
                            "title": "Task 1",
                            "description": "Do task 1",
                            "required_permissions": ["write_docs"],
                            "expected_output": "Done work",
                            "acceptance_criteria": [{"id": "done", "text": "Work is done."}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        store = Store(root / "state.sqlite3")
        orchestrator = Orchestrator(store)

        self.assertEqual(orchestrator.import_plan(plan), 1)
        task = orchestrator.run_once(dry_run=True)

        self.assertIsNotNone(task)
        self.assertEqual(store.require_task("t1").status, TaskStatus.DONE)

    def test_dependency_blocks_second_task_until_first_done(self) -> None:
        root = test_root()
        store = Store(root / "state.sqlite3")
        orchestrator = Orchestrator(store)
        plan = root / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "a",
                            "title": "A",
                            "description": "First",
                            "required_permissions": [],
                            "expected_output": "",
                            "acceptance_criteria": [{"id": "ok", "text": "OK"}],
                            "priority": 20,
                        },
                        {
                            "id": "b",
                            "title": "B",
                            "description": "Second",
                            "required_permissions": [],
                            "expected_output": "",
                            "acceptance_criteria": [{"id": "ok", "text": "OK"}],
                            "priority": 10,
                            "depends_on": ["a"],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )

        orchestrator.import_plan(plan)
        self.assertEqual(orchestrator.next_task().id, "a")
        orchestrator.run_once(dry_run=True)
        self.assertEqual(orchestrator.next_task().id, "b")

    def test_web_state_payload_exposes_counts_and_next_task(self) -> None:
        root = test_root()
        store = Store(root / "state.sqlite3")
        orchestrator = Orchestrator(store)
        plan = root / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "ui-task",
                            "title": "UI task",
                            "description": "Visible in UI",
                            "required_permissions": ["read_state"],
                            "expected_output": "Rendered state",
                            "acceptance_criteria": [{"id": "visible", "text": "Task is visible."}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        orchestrator.import_plan(plan)
        payload = state_payload(store)

        self.assertEqual(payload["counts"]["pending"], 1)
        self.assertEqual(payload["next_task_id"], "ui-task")
        self.assertEqual(payload["tasks"][0]["criteria_count"], 1)

    def test_task_priority_can_be_updated(self) -> None:
        root = test_root()
        store = Store(root / "state.sqlite3")
        orchestrator = Orchestrator(store)
        plan = root / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "priority-task",
                            "title": "Priority task",
                            "description": "Change priority",
                            "required_permissions": [],
                            "expected_output": "",
                            "acceptance_criteria": [{"id": "ok", "text": "OK"}],
                            "priority": 100,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        orchestrator.import_plan(plan)
        store.set_priority("priority-task", 5)

        self.assertEqual(store.require_task("priority-task").priority, 5)

    def test_worker_can_claim_and_complete_compatible_task(self) -> None:
        root = test_root()
        store = Store(root / "state.sqlite3")
        orchestrator = Orchestrator(store)
        plan = root / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "ui",
                            "project_id": "orchestrator",
                            "title": "Build UI",
                            "description": "Build the UI",
                            "required_permissions": ["write_files"],
                            "required_capabilities": ["frontend"],
                            "expected_output": "UI files",
                            "acceptance_criteria": [{"id": "rendered", "text": "UI renders."}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        orchestrator.import_plan(plan)
        store.register_worker(
            Worker(
                id="ui-1",
                project_id="orchestrator",
                label="UI dialog",
                capabilities=["frontend", "python"],
            )
        )

        task = orchestrator.next_task_for_worker("ui-1", lease_minutes=15)

        self.assertIsNotNone(task)
        self.assertEqual(task.id, "ui")
        self.assertEqual(store.require_task("ui").status, TaskStatus.RUNNING)
        self.assertTrue((root / "inbox" / "ui-1" / "task.json").exists())

        result = root / "outbox" / "ui-1" / "result.json"
        result.write_text(
            json.dumps(
                {
                    "summary": "Done",
                    "artifacts": ["agent_control/static/index.html"],
                    "criteria_checked": ["rendered"],
                    "needs_consultation": False,
                }
            ),
            encoding="utf-8",
        )

        completed = orchestrator.complete_worker_task("ui-1")

        self.assertEqual(completed.status, TaskStatus.DONE)
        self.assertIsNone(store.require_worker("ui-1").current_task_id)

    def test_worker_does_not_claim_wrong_project(self) -> None:
        root = test_root()
        store = Store(root / "state.sqlite3")
        orchestrator = Orchestrator(store)
        plan = root / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "api",
                            "project_id": "other",
                            "title": "API",
                            "description": "Build API",
                            "required_permissions": [],
                            "required_capabilities": ["backend"],
                            "expected_output": "API",
                            "acceptance_criteria": [{"id": "ok", "text": "OK"}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        orchestrator.import_plan(plan)
        store.register_worker(Worker(id="api-1", project_id="orchestrator", label="API", capabilities=["backend"]))

        self.assertIsNone(orchestrator.next_task_for_worker("api-1"))

    def test_worker_display_name_and_update(self) -> None:
        root = test_root()
        store = Store(root / "state.sqlite3")
        store.register_worker(
            Worker(
                id="dialog-1",
                project_id="New project2",
                label="Internal",
                dialog_name="Create agent orchestrator",
                capabilities=["python"],
            )
        )

        worker = store.require_worker("dialog-1")
        self.assertEqual(worker.display_name, "New project2/Create agent orchestrator")

        updated = store.update_worker(
            worker_id="dialog-1",
            project_id="New project2",
            label="Internal",
            dialog_name="Create agent orchestrator",
            custom_name="Main Executor",
            capabilities=["python", "frontend"],
        )

        self.assertEqual(updated.display_name, "Main Executor")
        self.assertEqual(updated.capabilities, ["python", "frontend"])

    def test_worker_can_be_set_offline_and_reactivated(self) -> None:
        root = test_root()
        store = Store(root / "state.sqlite3")
        store.register_worker(Worker(id="dialog-1", project_id="project", label="Dialog", capabilities=["python"]))

        offline = store.set_worker_status("dialog-1", WorkerStatus.OFFLINE)
        self.assertEqual(offline.status, WorkerStatus.OFFLINE)

        active = store.set_worker_status("dialog-1", WorkerStatus.IDLE)
        self.assertEqual(active.status, WorkerStatus.IDLE)

    def test_settings_are_exposed_in_state_payload_without_token(self) -> None:
        root = test_root()
        store = Store(root / "state.sqlite3")
        store.set_settings(
            {
                "telegram_bot_token": "secret-token",
                "telegram_allowed_chat_ids": "1,2",
                "auto_refresh_seconds": "7",
                "project_colors": "{\"default\":\"#123456\"}",
            }
        )

        payload = state_payload(store)

        self.assertEqual(payload["settings"]["telegram_bot_token"], "")
        self.assertEqual(payload["settings"]["telegram_bot_token_set"], "true")
        self.assertEqual(payload["settings"]["telegram_allowed_chat_ids"], "1,2")
        self.assertEqual(payload["settings"]["auto_refresh_seconds"], "7")
        self.assertEqual(payload["settings"]["project_colors"], "{\"default\":\"#123456\"}")

    def test_telegram_bot_reads_settings(self) -> None:
        root = test_root()
        store = Store(root / "state.sqlite3")
        store.set_settings(
            {
                "telegram_bot_token": "123:abc",
                "telegram_allowed_chat_ids": "100,-200",
            }
        )

        bot = TelegramBot.from_settings(store)

        self.assertEqual(bot.token, "123:abc")
        self.assertEqual(bot.allowed_chat_ids, {100, -200})

    def test_parse_single_level_numbered_plan(self) -> None:
        root = test_root()
        plan = root / "plan.txt"
        plan.write_text(
            "1. Первая задача\nОписание первой.\n\n2) Вторая задача\nОписание второй.",
            encoding="utf-8",
        )

        tasks = parse_numbered_plan(plan, project_id="orchestrator", default_capabilities=["docs"])

        self.assertEqual([task["title"] for task in tasks], ["Первая задача", "Вторая задача"])
        self.assertEqual(tasks[0]["project_id"], "orchestrator")
        self.assertEqual(tasks[0]["required_capabilities"], ["docs"])
        self.assertEqual(tasks[1]["depends_on"], [])

    def test_parse_nested_numbered_plan_adds_parent_dependency(self) -> None:
        root = test_root()
        plan = root / "plan.txt"
        plan.write_text(
            "1. Родитель\n\n1.1 Дочерняя A\n\n1.2 Дочерняя B\n\n2. Отдельная",
            encoding="utf-8",
        )

        tasks = parse_numbered_plan(plan)

        self.assertEqual(tasks[1]["depends_on"], [tasks[0]["id"]])
        self.assertEqual(tasks[2]["depends_on"], [tasks[0]["id"]])
        self.assertEqual(tasks[3]["depends_on"], [])

    def test_import_text_plan_creates_tasks(self) -> None:
        root = test_root()
        store = Store(root / "state.sqlite3")
        orchestrator = Orchestrator(store)
        plan = root / "plan.txt"
        plan.write_text("1. Импортировать\nПроверить импорт.", encoding="utf-8")

        count = orchestrator.import_text_plan(plan, project_id="orchestrator")

        self.assertEqual(count, 1)
        self.assertEqual(store.list_tasks()[0].project_id, "orchestrator")

    def test_parse_numbered_plan_text_for_ui_import(self) -> None:
        tasks = parse_numbered_plan_text(
            "1. Build board\nShow kanban columns.\n\n1.1 Add reports",
            project_id="orchestrator",
            default_permissions=["write_files"],
            default_capabilities=["frontend"],
        )

        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["title"], "Build board")
        self.assertEqual(tasks[1]["depends_on"], [tasks[0]["id"]])
        self.assertEqual(tasks[0]["required_permissions"], ["write_files"])

    def test_reports_payload_splits_completed_and_incomplete(self) -> None:
        root = test_root()
        store = Store(root / "state.sqlite3")
        orchestrator = Orchestrator(store)
        plan = root / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "done-task",
                            "title": "Done task",
                            "description": "Done",
                            "required_permissions": [],
                            "expected_output": "",
                            "acceptance_criteria": [{"id": "ok", "text": "OK"}],
                        },
                        {
                            "id": "blocked-task",
                            "title": "Blocked task",
                            "description": "Blocked",
                            "required_permissions": [],
                            "expected_output": "",
                            "acceptance_criteria": [{"id": "ok", "text": "OK"}],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        orchestrator.import_plan(plan)
        store.add_event("done-task", "agent_result", {"summary": "Finished"})
        store.set_status("done-task", TaskStatus.DONE)
        store.add_note("blocked-task", "Waiting for operator decision")
        store.set_status("blocked-task", TaskStatus.BLOCKED)

        payload = reports_payload(store)

        self.assertEqual(payload["totals"]["completed"], 1)
        self.assertEqual(payload["totals"]["incomplete"], 1)
        self.assertEqual(payload["completed"][0]["last_result"]["payload"]["summary"], "Finished")
        self.assertEqual(payload["incomplete"][0]["last_reason"]["kind"], "note")


if __name__ == "__main__":
    raise SystemExit(unittest.main())
