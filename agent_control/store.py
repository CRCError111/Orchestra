from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import Task, TaskStatus, Worker, WorkerStatus


SCHEMA = """
create table if not exists tasks (
    id text primary key,
    payload text not null,
    status text not null,
    priority integer not null default 100,
    updated_at text not null default current_timestamp
);

create table if not exists events (
    id integer primary key autoincrement,
    task_id text,
    kind text not null,
    payload text not null,
    created_at text not null default current_timestamp
);

create table if not exists workers (
    id text primary key,
    project_id text not null,
    label text not null,
    dialog_name text not null default '',
    custom_name text not null default '',
    capabilities text not null,
    status text not null,
    current_task_id text,
    lease_expires_at text,
    last_seen_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);

create table if not exists assignments (
    id integer primary key autoincrement,
    task_id text not null,
    worker_id text not null,
    status text not null,
    lease_expires_at text,
    created_at text not null default current_timestamp,
    finished_at text
);

create table if not exists settings (
    key text primary key,
    value text not null,
    updated_at text not null default current_timestamp
);
"""


class Store:
    def __init__(self, path: str | Path = ".agent_control/state.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def upsert_task(self, task: Task) -> None:
        payload = json.dumps(task.to_dict(), ensure_ascii=False, indent=2)
        self.conn.execute(
            """
            insert into tasks (id, payload, status, priority)
            values (?, ?, ?, ?)
            on conflict(id) do update set
                payload = excluded.payload,
                status = excluded.status,
                priority = excluded.priority,
                updated_at = current_timestamp
            """,
            (task.id, payload, task.status.value, task.priority),
        )
        self.conn.commit()

    def get_task(self, task_id: str) -> Task | None:
        row = self.conn.execute("select payload from tasks where id = ?", (task_id,)).fetchone()
        return Task.from_dict(json.loads(row["payload"])) if row else None

    def list_tasks(self) -> list[Task]:
        rows = self.conn.execute("select payload from tasks order by priority asc, id asc").fetchall()
        return [Task.from_dict(json.loads(row["payload"])) for row in rows]

    def assign_task_to_worker(self, task_id: str, worker_id: str | None) -> None:
        task = self.require_task(task_id)
        task.assigned_worker_id = worker_id
        self.upsert_task(task)
        self.add_event(task_id, "task_assigned_worker", {"worker_id": worker_id})

    def set_status(self, task_id: str, status: TaskStatus) -> None:
        task = self.require_task(task_id)
        task.status = status
        self.upsert_task(task)
        self.add_event(task_id, "status", {"status": status.value})

    def set_priority(self, task_id: str, priority: int) -> None:
        task = self.require_task(task_id)
        task.priority = priority
        self.upsert_task(task)
        self.add_event(task_id, "priority", {"priority": priority})

    def add_note(self, task_id: str, note: str) -> None:
        task = self.require_task(task_id)
        task.notes.append(note)
        self.upsert_task(task)
        self.add_event(task_id, "note", {"text": note})

    def add_event(self, task_id: str | None, kind: str, payload: dict[str, Any]) -> None:
        self.conn.execute(
            "insert into events (task_id, kind, payload) values (?, ?, ?)",
            (task_id, kind, json.dumps(payload, ensure_ascii=False)),
        )
        self.conn.commit()

    def register_worker(self, worker: Worker) -> None:
        self.conn.execute(
            """
            insert into workers (id, project_id, label, dialog_name, custom_name, capabilities, status, current_task_id, lease_expires_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
                project_id = excluded.project_id,
                label = excluded.label,
                dialog_name = excluded.dialog_name,
                custom_name = excluded.custom_name,
                capabilities = excluded.capabilities,
                status = case
                    when workers.status = 'busy' then workers.status
                    else excluded.status
                end,
                updated_at = current_timestamp,
                last_seen_at = current_timestamp
            """,
            (
                worker.id,
                worker.project_id,
                worker.label,
                worker.dialog_name,
                worker.custom_name,
                json.dumps(worker.capabilities, ensure_ascii=False),
                worker.status.value,
                worker.current_task_id,
                worker.lease_expires_at,
            ),
        )
        self.conn.commit()
        self.add_event(None, "worker_registered", worker.to_dict())

    def update_worker(
        self,
        worker_id: str,
        project_id: str,
        label: str,
        dialog_name: str,
        custom_name: str,
        capabilities: list[str],
    ) -> Worker:
        self.require_worker(worker_id)
        self.conn.execute(
            """
            update workers
            set project_id = ?,
                label = ?,
                dialog_name = ?,
                custom_name = ?,
                capabilities = ?,
                updated_at = current_timestamp
            where id = ?
            """,
            (
                project_id,
                label,
                dialog_name,
                custom_name,
                json.dumps(capabilities, ensure_ascii=False),
                worker_id,
            ),
        )
        self.conn.commit()
        worker = self.require_worker(worker_id)
        self.add_event(None, "worker_updated", worker.to_dict())
        return worker

    def set_worker_status(self, worker_id: str, status: WorkerStatus) -> Worker:
        worker = self.require_worker(worker_id)
        if worker.status == WorkerStatus.BUSY and status != WorkerStatus.BUSY:
            raise RuntimeError(f"Worker {worker_id} is busy with {worker.current_task_id}")
        self.conn.execute(
            """
            update workers
            set status = ?, updated_at = current_timestamp, last_seen_at = current_timestamp
            where id = ?
            """,
            (status.value, worker_id),
        )
        self.conn.commit()
        worker = self.require_worker(worker_id)
        self.add_event(None, "worker_status_updated", worker.to_dict())
        return worker

    def get_worker(self, worker_id: str) -> Worker | None:
        row = self.conn.execute("select * from workers where id = ?", (worker_id,)).fetchone()
        return self._worker_from_row(row) if row else None

    def require_worker(self, worker_id: str) -> Worker:
        worker = self.get_worker(worker_id)
        if worker is None:
            raise KeyError(f"Unknown worker: {worker_id}")
        return worker

    def list_workers(self) -> list[Worker]:
        rows = self.conn.execute("select * from workers order by project_id asc, id asc").fetchall()
        return [self._worker_from_row(row) for row in rows]

    def touch_worker(self, worker_id: str) -> None:
        self.conn.execute(
            "update workers set last_seen_at = current_timestamp, updated_at = current_timestamp where id = ?",
            (worker_id,),
        )
        self.conn.commit()

    def expire_leases(self) -> int:
        rows = self.conn.execute(
            """
            select worker_id, task_id from assignments
            where status = 'active'
              and lease_expires_at is not null
              and datetime(lease_expires_at) <= datetime(current_timestamp)
            """
        ).fetchall()
        for row in rows:
            self.set_status(row["task_id"], TaskStatus.PENDING)
            self.conn.execute(
                "update assignments set status = 'expired', finished_at = current_timestamp where worker_id = ? and task_id = ? and status = 'active'",
                (row["worker_id"], row["task_id"]),
            )
            self.conn.execute(
                """
                update workers
                set status = ?, current_task_id = null, lease_expires_at = null, updated_at = current_timestamp
                where id = ?
                """,
                (WorkerStatus.IDLE.value, row["worker_id"]),
            )
            self.add_event(row["task_id"], "lease_expired", {"worker_id": row["worker_id"]})
        self.conn.commit()
        return len(rows)

    def start_assignment(self, task_id: str, worker_id: str, lease_minutes: int) -> int:
        worker = self.require_worker(worker_id)
        if worker.status == WorkerStatus.BUSY and worker.current_task_id:
            raise RuntimeError(f"Worker {worker_id} is busy with {worker.current_task_id}")
        self.set_status(task_id, TaskStatus.RUNNING)
        self.conn.execute(
            """
            insert into assignments (task_id, worker_id, status, lease_expires_at)
            values (?, ?, 'active', datetime(current_timestamp, ?))
            """,
            (task_id, worker_id, f"+{lease_minutes} minutes"),
        )
        assignment_id = int(self.conn.execute("select last_insert_rowid()").fetchone()[0])
        lease = self.conn.execute(
            "select lease_expires_at from assignments where id = ?",
            (assignment_id,),
        ).fetchone()["lease_expires_at"]
        self.conn.execute(
            """
            update workers
            set status = ?, current_task_id = ?, lease_expires_at = ?, last_seen_at = current_timestamp, updated_at = current_timestamp
            where id = ?
            """,
            (WorkerStatus.BUSY.value, task_id, lease, worker_id),
        )
        self.conn.commit()
        self.add_event(task_id, "worker_assigned", {"worker_id": worker_id, "assignment_id": assignment_id})
        return assignment_id

    def finish_assignment(self, worker_id: str, task_id: str, status: str) -> None:
        self.conn.execute(
            """
            update assignments
            set status = ?, finished_at = current_timestamp
            where worker_id = ? and task_id = ? and status = 'active'
            """,
            (status, worker_id, task_id),
        )
        self.conn.execute(
            """
            update workers
            set status = ?, current_task_id = null, lease_expires_at = null, last_seen_at = current_timestamp, updated_at = current_timestamp
            where id = ?
            """,
            (WorkerStatus.IDLE.value, worker_id),
        )
        self.conn.commit()

    def list_assignments(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "select * from assignments order by id desc limit ?",
            (limit,),
        ).fetchall()

    def get_settings(self) -> dict[str, str]:
        rows = self.conn.execute("select key, value from settings order by key asc").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def set_settings(self, values: dict[str, str]) -> None:
        for key, value in values.items():
            self.conn.execute(
                """
                insert into settings (key, value)
                values (?, ?)
                on conflict(key) do update set value = excluded.value, updated_at = current_timestamp
                """,
                (key, value),
            )
        self.conn.commit()
        self.add_event(None, "settings_updated", {"keys": sorted(values)})

    def events(self, limit: int = 25) -> list[sqlite3.Row]:
        return self.conn.execute(
            "select * from events order by id desc limit ?",
            (limit,),
        ).fetchall()

    def require_task(self, task_id: str) -> Task:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Unknown task: {task_id}")
        return task

    def _worker_from_row(self, row: sqlite3.Row) -> Worker:
        return Worker(
            id=row["id"],
            project_id=row["project_id"],
            label=row["label"],
            dialog_name=row["dialog_name"],
            custom_name=row["custom_name"],
            capabilities=json.loads(row["capabilities"]),
            status=WorkerStatus(row["status"]),
            current_task_id=row["current_task_id"],
            lease_expires_at=row["lease_expires_at"],
        )

    def _migrate(self) -> None:
        worker_columns = {
            row["name"] for row in self.conn.execute("pragma table_info(workers)").fetchall()
        }
        if "dialog_name" not in worker_columns:
            self.conn.execute("alter table workers add column dialog_name text not null default ''")
        if "custom_name" not in worker_columns:
            self.conn.execute("alter table workers add column custom_name text not null default ''")
        self.conn.commit()
