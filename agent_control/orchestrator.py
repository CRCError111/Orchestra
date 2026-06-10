from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from .models import AgentResult, Task, TaskStatus, Worker, WorkerStatus
from .store import Store
from .text_plan import parse_numbered_plan


class Orchestrator:
    def __init__(self, store: Store) -> None:
        self.store = store

    def import_plan(self, path: str | Path) -> int:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        tasks = data["tasks"] if isinstance(data, dict) else data
        for item in tasks:
            self.store.upsert_task(Task.from_dict(item))
        self.store.add_event(None, "plan_imported", {"path": str(path), "count": len(tasks)})
        return len(tasks)

    def import_text_plan(
        self,
        path: str | Path,
        project_id: str = "default",
        default_permissions: list[str] | None = None,
        default_capabilities: list[str] | None = None,
    ) -> int:
        tasks = parse_numbered_plan(path, project_id, default_permissions, default_capabilities)
        for item in tasks:
            self.store.upsert_task(Task.from_dict(item))
        self.store.add_event(
            None,
            "text_plan_imported",
            {"path": str(path), "project_id": project_id, "count": len(tasks)},
        )
        return len(tasks)

    def next_task(self) -> Task | None:
        done = {task.id for task in self.store.list_tasks() if task.status == TaskStatus.DONE}
        candidates = [
            task
            for task in self.store.list_tasks()
            if task.status == TaskStatus.PENDING and all(dep in done for dep in task.depends_on)
        ]
        return sorted(candidates, key=lambda item: (item.priority, item.id))[0] if candidates else None

    def next_task_for_worker(self, worker_id: str, lease_minutes: int = 30) -> Task | None:
        self.store.expire_leases()
        worker = self.store.require_worker(worker_id)
        self.store.touch_worker(worker_id)
        if worker.status == WorkerStatus.BUSY and worker.current_task_id:
            return self.store.require_task(worker.current_task_id)

        task = self._eligible_task(worker)
        if task is None:
            return None

        self.store.start_assignment(task.id, worker.id, lease_minutes)
        assigned_task = self.store.require_task(task.id)
        self._write_worker_task(worker.id, assigned_task)
        return assigned_task

    def complete_worker_task(self, worker_id: str, result_path: str | Path | None = None) -> Task:
        worker = self.store.require_worker(worker_id)
        if not worker.current_task_id:
            raise RuntimeError(f"Worker {worker_id} has no active task")
        task = self.store.require_task(worker.current_task_id)
        result_file = Path(result_path) if result_path else self._outbox_dir(worker_id) / "result.json"
        if not result_file.exists():
            raise FileNotFoundError(f"Result JSON not found: {result_file}")
        result = AgentResult.from_dict(json.loads(result_file.read_text(encoding="utf-8")))

        if result.needs_consultation:
            self.store.set_status(task.id, TaskStatus.BLOCKED)
            self.store.finish_assignment(worker_id, task.id, "blocked")
            self.store.add_event(
                task.id,
                "consultation_requested",
                {"question": result.consultation_question, "summary": result.summary, "worker_id": worker_id},
            )
            return self.store.require_task(task.id)

        accepted_task = self._accept_or_review(task, result, self._run_tests(task))
        self.store.finish_assignment(worker_id, task.id, accepted_task.status.value)
        return accepted_task

    def block_worker_task(self, worker_id: str, reason: str) -> Task:
        worker = self.store.require_worker(worker_id)
        if not worker.current_task_id:
            raise RuntimeError(f"Worker {worker_id} has no active task")
        task = self.store.require_task(worker.current_task_id)
        self.store.add_note(task.id, f"Blocked by {worker_id}: {reason}")
        self.store.set_status(task.id, TaskStatus.BLOCKED)
        self.store.finish_assignment(worker_id, task.id, "blocked")
        return self.store.require_task(task.id)

    def run_once(self, agent_command: str | None = None, dry_run: bool = False) -> Task | None:
        task = self.next_task()
        if task is None:
            return None

        self.store.set_status(task.id, TaskStatus.RUNNING)
        self.store.add_event(task.id, "assigned", {"permissions": task.required_permissions})

        if dry_run:
            result = AgentResult(
                summary="Dry run completed without invoking an agent.",
                artifacts=[],
                criteria_checked=[criterion.id for criterion in task.acceptance_criteria],
            )
            return self._accept_or_review(task, result, test_ok=True)

        if not agent_command:
            self.store.set_status(task.id, TaskStatus.REVIEW)
            self.store.add_event(task.id, "review_required", {"reason": "No agent command configured"})
            return task

        run_dir = self._exchange_root() / "runs" / task.id
        run_dir.mkdir(parents=True, exist_ok=True)
        task_file = run_dir / "task.json"
        result_file = run_dir / "result.json"
        if result_file.exists():
            result_file.unlink()
        task_file.write_text(json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        command = agent_command.format(task_file=str(task_file), result_file=str(result_file))
        completed = subprocess.run(command, shell=True, text=True, capture_output=True)
        self.store.add_event(
            task.id,
            "agent_finished",
            {
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            },
        )
        if completed.returncode != 0:
            self.store.set_status(task.id, TaskStatus.FAILED)
            return task
        if not result_file.exists():
            self.store.set_status(task.id, TaskStatus.REVIEW)
            self.store.add_event(task.id, "review_required", {"reason": "Agent did not write result JSON"})
            return task
        result = AgentResult.from_dict(json.loads(result_file.read_text(encoding="utf-8")))

        if result.needs_consultation:
            self.store.set_status(task.id, TaskStatus.BLOCKED)
            self.store.add_event(
                task.id,
                "consultation_requested",
                {"question": result.consultation_question, "summary": result.summary},
            )
            return task

        return self._accept_or_review(task, result, self._run_tests(task))

    def _run_tests(self, task: Task) -> bool:
        if not task.test_command:
            return True
        completed = subprocess.run(task.test_command, shell=True, text=True, capture_output=True)
        self.store.add_event(
            task.id,
            "tests_finished",
            {
                "command": task.test_command,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            },
        )
        return completed.returncode == 0

    def _eligible_task(self, worker: Worker) -> Task | None:
        done = {task.id for task in self.store.list_tasks() if task.status == TaskStatus.DONE}
        worker_caps = set(worker.capabilities)
        candidates = []
        for task in self.store.list_tasks():
            if task.status != TaskStatus.PENDING:
                continue
            if task.project_id != worker.project_id:
                continue
            if task.assigned_worker_id and task.assigned_worker_id != worker.id:
                continue
            if not set(task.required_capabilities).issubset(worker_caps):
                continue
            if not all(dep in done for dep in task.depends_on):
                continue
            candidates.append(task)
        return sorted(candidates, key=lambda item: (item.priority, item.id))[0] if candidates else None

    def _write_worker_task(self, worker_id: str, task: Task) -> None:
        inbox = self._inbox_dir(worker_id)
        inbox.mkdir(parents=True, exist_ok=True)
        (self._outbox_dir(worker_id)).mkdir(parents=True, exist_ok=True)
        payload = task.to_dict()
        payload["worker_id"] = worker_id
        payload["assignment_notes"] = task.notes
        (inbox / "task.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.store.add_event(task.id, "task_written_to_inbox", {"worker_id": worker_id, "path": str(inbox / "task.json")})

    def _exchange_root(self) -> Path:
        return self.store.path.parent

    def _inbox_dir(self, worker_id: str) -> Path:
        return self._exchange_root() / "inbox" / worker_id

    def _outbox_dir(self, worker_id: str) -> Path:
        return self._exchange_root() / "outbox" / worker_id

    def _accept_or_review(self, task: Task, result: AgentResult, test_ok: bool) -> Task:
        criteria_ids = {criterion.id for criterion in task.acceptance_criteria}
        criteria_texts = {criterion.text for criterion in task.acceptance_criteria}
        checked = set(result.criteria_checked)
        criteria_ok = criteria_ids.issubset(checked) or criteria_texts.issubset(checked)
        self.store.add_event(
            task.id,
            "agent_result",
            {
                "summary": result.summary,
                "artifacts": result.artifacts,
                "criteria_checked": result.criteria_checked,
                "tests_ok": test_ok,
                "criteria_ok": criteria_ok,
            },
        )
        self.store.set_status(task.id, TaskStatus.DONE if test_ok and criteria_ok else TaskStatus.REVIEW)
        return self.store.require_task(task.id)


def quote_command(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)
