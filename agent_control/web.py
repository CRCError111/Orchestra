from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .models import Task, TaskStatus, Worker, WorkerStatus
from .orchestrator import Orchestrator
from .store import Store
from .telegram import TelegramBot
from .text_plan import parse_numbered_plan_text


STATIC_ROOT = Path(__file__).parent / "static"


def task_payload(task: Task) -> dict:
    data = task.to_dict()
    data["criteria_count"] = len(task.acceptance_criteria)
    data["notes_count"] = len(task.notes)
    return data


def event_payload(event) -> dict:
    payload = json.loads(event["payload"])
    return {
        "id": event["id"],
        "task_id": event["task_id"],
        "kind": event["kind"],
        "payload": payload,
        "created_at": event["created_at"],
    }


def state_payload(store: Store) -> dict:
    orchestrator = Orchestrator(store)
    next_task = orchestrator.next_task()
    tasks = store.list_tasks()
    counts = {status.value: 0 for status in TaskStatus}
    for task in tasks:
        counts[task.status.value] += 1
    return {
        "tasks": [task_payload(task) for task in tasks],
        "events": [event_payload(event) for event in store.events(40)],
        "workers": [worker.to_dict() for worker in store.list_workers()],
        "assignments": [dict(assignment) for assignment in store.list_assignments(25)],
        "settings": settings_payload(store),
        "reports": reports_payload(store, tasks),
        "next_task_id": next_task.id if next_task else None,
        "counts": counts,
    }


def settings_payload(store: Store) -> dict:
    settings = {
        "telegram_bot_token": "",
        "telegram_allowed_chat_ids": "",
        "auto_refresh_seconds": "5",
        "project_colors": "{}",
    }
    settings.update(store.get_settings())
    if settings.get("telegram_bot_token"):
        settings["telegram_bot_token_set"] = "true"
        settings["telegram_bot_token"] = ""
    else:
        settings["telegram_bot_token_set"] = "false"
    return settings


def reports_payload(store: Store, tasks: list[Task] | None = None) -> dict:
    tasks = tasks or store.list_tasks()
    events = [event_payload(event) for event in store.events(500)]
    events_by_task: dict[str, list[dict]] = {}
    for event in events:
        task_id = event["task_id"]
        if task_id:
            events_by_task.setdefault(task_id, []).append(event)

    completed = []
    incomplete = []
    for task in tasks:
        task_events = events_by_task.get(task.id, [])
        last_result = _last_event(task_events, {"agent_result"})
        last_reason = _last_event(
            task_events,
            {"consultation_requested", "review_required", "agent_finished", "tests_finished", "note", "lease_expired"},
        )
        row = {
            "id": task.id,
            "project_id": task.project_id,
            "title": task.title,
            "status": task.status.value,
            "worker_id": task.assigned_worker_id,
            "notes": task.notes,
            "last_result": last_result,
            "last_reason": last_reason,
        }
        if task.status == TaskStatus.DONE:
            completed.append(row)
        else:
            incomplete.append(row)

    return {
        "completed": completed,
        "incomplete": incomplete,
        "totals": {
            "completed": len(completed),
            "incomplete": len(incomplete),
            "all": len(tasks),
        },
    }


def _last_event(events: list[dict], kinds: set[str]) -> dict | None:
    for event in events:
        if event["kind"] in kinds:
            return event
    return None


class WebHandler(BaseHTTPRequestHandler):
    server_version = "AgentControlUI/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self._with_store(lambda store: self._json(state_payload(store)))
            return
        if parsed.path == "/api/reports":
            self._with_store(lambda store: self._json(reports_payload(store)))
            return
        if parsed.path == "/api/settings":
            self._with_store(lambda store: self._json(settings_payload(store)))
            return
        if parsed.path.startswith("/api/tasks/"):
            task_id = self._task_id(parsed.path)
            self._with_store(lambda store: self._json(task_payload(store.require_task(task_id))))
            return
        self._static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/run-once":
            body = self._body()

            def action(store: Store) -> None:
                task = Orchestrator(store).run_once(
                    agent_command=body.get("agent_command") or None,
                    dry_run=bool(body.get("dry_run", False)),
                )
                self._json({"task": task_payload(task) if task else None, "state": state_payload(store)})

            self._with_store(action)
            return
        if parsed.path == "/api/import-plan":
            body = self._body()
            path = body.get("path")
            if not path:
                self._json({"error": "path is required"}, HTTPStatus.BAD_REQUEST)
                return
            self._with_store(lambda store: self._json({"imported": Orchestrator(store).import_plan(path)}))
            return
        if parsed.path == "/api/import-text-plan":
            body = self._body()
            text = str(body.get("text", "")).strip()
            if not text:
                self._json({"error": "text is required"}, HTTPStatus.BAD_REQUEST)
                return
            project_id = str(body.get("project_id") or "default")
            permissions = _csv(body.get("permissions"))
            capabilities = _csv(body.get("capabilities"))

            def action(store: Store) -> None:
                tasks = parse_numbered_plan_text(text, project_id, permissions, capabilities)
                for item in tasks:
                    store.upsert_task(Task.from_dict(item))
                store.add_event(
                    None,
                    "text_plan_imported",
                    {"source": "ui", "project_id": project_id, "count": len(tasks)},
                )
                self._json({"imported": len(tasks), "state": state_payload(store)})

            self._with_store(action)
            return
        if parsed.path == "/api/settings":
            body = self._body()

            def action(store: Store) -> None:
                values = {
                    "telegram_allowed_chat_ids": str(body.get("telegram_allowed_chat_ids", "")),
                    "auto_refresh_seconds": str(body.get("auto_refresh_seconds", "5")),
                    "project_colors": str(body.get("project_colors", "{}")),
                }
                token = str(body.get("telegram_bot_token", "")).strip()
                if token:
                    values["telegram_bot_token"] = token
                store.set_settings(values)
                self._json({"settings": settings_payload(store), "state": state_payload(store)})

            self._with_store(action)
            return
        if parsed.path == "/api/telegram/recent-chats":
            def action(store: Store) -> None:
                chats = TelegramBot.from_settings(store).recent_chats()
                self._json({"chats": chats})

            self._with_store(action)
            return
        if parsed.path == "/api/workers/register":
            body = self._body()
            worker_id = str(body.get("id", "")).strip()
            if not worker_id:
                self._json({"error": "id is required"}, HTTPStatus.BAD_REQUEST)
                return

            def action(store: Store) -> None:
                worker = Worker(
                    id=worker_id,
                    project_id=str(body.get("project_id") or "default"),
                    label=str(body.get("label") or worker_id),
                    dialog_name=str(body.get("dialog_name") or ""),
                    custom_name=str(body.get("custom_name") or ""),
                    capabilities=_csv(body.get("capabilities")),
                )
                store.register_worker(worker)
                self._json({"worker": worker.to_dict(), "state": state_payload(store)})

            self._with_store(action)
            return
        if parsed.path.startswith("/api/workers/") and parsed.path.endswith("/update"):
            worker_id = self._worker_id(parsed.path)
            body = self._body()

            def action(store: Store) -> None:
                existing = store.require_worker(worker_id)
                worker = store.update_worker(
                    worker_id=worker_id,
                    project_id=str(body.get("project_id") or existing.project_id),
                    label=str(body.get("label") or existing.label),
                    dialog_name=str(body.get("dialog_name") or ""),
                    custom_name=str(body.get("custom_name") or ""),
                    capabilities=_csv(body.get("capabilities")),
                )
                self._json({"worker": worker.to_dict(), "state": state_payload(store)})

            self._with_store(action)
            return
        if parsed.path.startswith("/api/workers/") and parsed.path.endswith("/status"):
            worker_id = self._worker_id(parsed.path)
            body = self._body()
            status = WorkerStatus(body["status"])
            if status == WorkerStatus.BUSY:
                self._json({"error": "busy status is managed by assignments"}, HTTPStatus.BAD_REQUEST)
                return

            def action(store: Store) -> None:
                worker = store.set_worker_status(worker_id, status)
                self._json({"worker": worker.to_dict(), "state": state_payload(store)})

            self._with_store(action)
            return
        if parsed.path.startswith("/api/tasks/"):
            task_id = self._task_id(parsed.path)
            if parsed.path.endswith("/status"):
                body = self._body()
                status = TaskStatus(body["status"])
                self._with_store(lambda store: self._set_status(store, task_id, status))
                return
            if parsed.path.endswith("/note"):
                body = self._body()
                note = str(body.get("text", "")).strip()
                if not note:
                    self._json({"error": "text is required"}, HTTPStatus.BAD_REQUEST)
                    return
                self._with_store(lambda store: self._add_note(store, task_id, note))
                return
            if parsed.path.endswith("/priority"):
                body = self._body()
                priority = int(body["priority"])
                self._with_store(lambda store: self._set_priority(store, task_id, priority))
                return
        self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args) -> None:
        return

    def _with_store(self, callback) -> None:
        try:
            store = Store(self.server.db_path)  # type: ignore[attr-defined]
            try:
                callback(store)
            finally:
                store.close()
        except Exception as exc:
            self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _set_status(self, store: Store, task_id: str, status: TaskStatus) -> None:
        store.set_status(task_id, status)
        self._json({"task": task_payload(store.require_task(task_id)), "state": state_payload(store)})

    def _add_note(self, store: Store, task_id: str, note: str) -> None:
        store.add_note(task_id, note)
        self._json({"task": task_payload(store.require_task(task_id)), "state": state_payload(store)})

    def _set_priority(self, store: Store, task_id: str, priority: int) -> None:
        store.set_priority(task_id, priority)
        self._json({"task": task_payload(store.require_task(task_id)), "state": state_payload(store)})

    def _task_id(self, path: str) -> str:
        parts = [part for part in path.split("/") if part]
        if len(parts) < 3:
            raise ValueError("Task id is missing")
        return parts[2]

    def _worker_id(self, path: str) -> str:
        parts = [part for part in path.split("/") if part]
        if len(parts) < 3:
            raise ValueError("Worker id is missing")
        return parts[2]

    def _body(self) -> dict:
        size = int(self.headers.get("Content-Length") or "0")
        if size == 0:
            return {}
        raw = self.rfile.read(size).decode("utf-8")
        content_type = self.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return json.loads(raw)
        return {key: values[-1] for key, values in parse_qs(raw).items()}

    def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: str) -> None:
        target = "index.html" if path in {"", "/"} else path.lstrip("/")
        file_path = (STATIC_ROOT / target).resolve()
        if not file_path.is_file() or STATIC_ROOT.resolve() not in file_path.parents:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = file_path.read_bytes()
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve_ui(db_path: str | Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = HTTPServer((host, port), WebHandler)
    server.db_path = str(db_path)  # type: ignore[attr-defined]
    print(f"Agent Control UI: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")


def _csv(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").split(",") if item.strip()]
