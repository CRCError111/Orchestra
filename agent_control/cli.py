from __future__ import annotations

import argparse
from pathlib import Path

from .models import TaskStatus, Worker
from .orchestrator import Orchestrator
from .store import Store
from .telegram import TelegramBot
from .web import serve_ui


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-control")
    parser.add_argument("--db", default=".agent_control/state.sqlite3")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init")

    import_plan = sub.add_parser("import-plan")
    import_plan.add_argument("path")

    import_text_plan = sub.add_parser("import-text-plan")
    import_text_plan.add_argument("path")
    import_text_plan.add_argument("--project", default="default")
    import_text_plan.add_argument("--perm", default="")
    import_text_plan.add_argument("--cap", default="")

    run_once = sub.add_parser("run-once")
    run_once.add_argument("--agent-command")
    run_once.add_argument("--dry-run", action="store_true")

    task_status = sub.add_parser("status")
    task_status.add_argument("--events", action="store_true")

    note = sub.add_parser("note")
    note.add_argument("task_id")
    note.add_argument("text")

    set_status = sub.add_parser("set-status")
    set_status.add_argument("task_id")
    set_status.add_argument("status", choices=[item.value for item in TaskStatus])

    assign = sub.add_parser("task-assign")
    assign.add_argument("task_id")
    assign.add_argument("worker_id")

    unassign = sub.add_parser("task-unassign")
    unassign.add_argument("task_id")

    register = sub.add_parser("worker-register")
    register.add_argument("--id", required=True)
    register.add_argument("--project", default="default")
    register.add_argument("--label", default="")
    register.add_argument("--dialog", default="")
    register.add_argument("--name", default="")
    register.add_argument("--cap", default="")

    worker_next = sub.add_parser("worker-next")
    worker_next.add_argument("worker_id")
    worker_next.add_argument("--lease-minutes", type=int, default=30)

    worker_complete = sub.add_parser("worker-complete")
    worker_complete.add_argument("worker_id")
    worker_complete.add_argument("--result")

    worker_block = sub.add_parser("worker-block")
    worker_block.add_argument("worker_id")
    worker_block.add_argument("reason")

    sub.add_parser("workers")

    ui = sub.add_parser("ui")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)

    sub.add_parser("telegram")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = Store(args.db)
    orchestrator = Orchestrator(store)

    if args.command == "init":
        print(f"Initialized {Path(args.db).resolve()}")
        return 0
    if args.command == "import-plan":
        count = orchestrator.import_plan(args.path)
        print(f"Imported {count} tasks")
        return 0
    if args.command == "import-text-plan":
        permissions = [item.strip() for item in args.perm.split(",") if item.strip()]
        capabilities = [item.strip() for item in args.cap.split(",") if item.strip()]
        count = orchestrator.import_text_plan(args.path, args.project, permissions, capabilities)
        print(f"Imported {count} tasks from text plan")
        return 0
    if args.command == "run-once":
        task = orchestrator.run_once(args.agent_command, args.dry_run)
        print("No available task" if task is None else f"{task.id}: {task.status.value}")
        return 0
    if args.command == "status":
        for task in store.list_tasks():
            print(f"{task.status.value:8} {task.id:16} {task.title}")
        if args.events:
            print("\nRecent events:")
            for event in store.events():
                print(f"#{event['id']} {event['created_at']} {event['task_id'] or '-'} {event['kind']} {event['payload']}")
        return 0
    if args.command == "note":
        store.add_note(args.task_id, args.text)
        print(f"Added note to {args.task_id}")
        return 0
    if args.command == "set-status":
        store.set_status(args.task_id, TaskStatus(args.status))
        print(f"{args.task_id}: {args.status}")
        return 0
    if args.command == "task-assign":
        store.assign_task_to_worker(args.task_id, args.worker_id)
        print(f"{args.task_id} assigned to {args.worker_id}")
        return 0
    if args.command == "task-unassign":
        store.assign_task_to_worker(args.task_id, None)
        print(f"{args.task_id} unassigned")
        return 0
    if args.command == "worker-register":
        capabilities = [item.strip() for item in args.cap.split(",") if item.strip()]
        worker = Worker(
            id=args.id,
            project_id=args.project,
            label=args.label or args.id,
            dialog_name=args.dialog,
            custom_name=args.name,
            capabilities=capabilities,
        )
        store.register_worker(worker)
        print(f"Registered worker {worker.id} in project {worker.project_id}")
        return 0
    if args.command == "worker-next":
        task = orchestrator.next_task_for_worker(args.worker_id, args.lease_minutes)
        if task is None:
            print("No compatible task")
        else:
            inbox = store.path.parent / "inbox" / args.worker_id / "task.json"
            print(f"{task.id}: {task.status.value}")
            print(f"Task file: {inbox}")
        return 0
    if args.command == "worker-complete":
        task = orchestrator.complete_worker_task(args.worker_id, args.result)
        print(f"{task.id}: {task.status.value}")
        return 0
    if args.command == "worker-block":
        task = orchestrator.block_worker_task(args.worker_id, args.reason)
        print(f"{task.id}: {task.status.value}")
        return 0
    if args.command == "workers":
        for worker in store.list_workers():
            current = worker.current_task_id or "-"
            caps = ",".join(worker.capabilities) or "-"
            print(f"{worker.status.value:5} {worker.display_name:40} {worker.id:16} {current:16} {caps}")
        return 0
    if args.command == "ui":
        store.close()
        serve_ui(args.db, args.host, args.port)
        return 0
    if args.command == "telegram":
        TelegramBot.from_settings(store).serve_forever()
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
