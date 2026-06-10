from __future__ import annotations

import re
from pathlib import Path


NUMBERED_LINE = re.compile(r"^\s*(?P<number>\d+(?:\.\d+)*)(?:[.)])?\s+(?P<title>.+?)\s*$")


def parse_numbered_plan(
    path: str | Path,
    project_id: str = "default",
    default_permissions: list[str] | None = None,
    default_capabilities: list[str] | None = None,
) -> list[dict]:
    return parse_numbered_plan_text(
        Path(path).read_text(encoding="utf-8"),
        project_id,
        default_permissions,
        default_capabilities,
    )


def parse_numbered_plan_text(
    text: str,
    project_id: str = "default",
    default_permissions: list[str] | None = None,
    default_capabilities: list[str] | None = None,
) -> list[dict]:
    tasks: list[dict] = []
    current: dict | None = None
    description_lines: list[str] = []
    number_to_task_id: dict[str, str] = {}
    default_permissions = default_permissions or []
    default_capabilities = default_capabilities or []

    def flush() -> None:
        nonlocal current, description_lines
        if current is None:
            return
        description = "\n".join(line.strip() for line in description_lines if line.strip()).strip()
        if description:
            current["description"] = description
            current["expected_output"] = description
        tasks.append(current)
        current = None
        description_lines = []

    for line in text.splitlines():
        match = NUMBERED_LINE.match(line)
        if match:
            flush()
            number = match.group("number")
            title = match.group("title").strip()
            task_id = _task_id(number, title)
            depends_on = []
            parent_number = _parent_number(number)
            if parent_number and parent_number in number_to_task_id:
                depends_on.append(number_to_task_id[parent_number])
            number_to_task_id[number] = task_id
            current = {
                "id": task_id,
                "project_id": project_id,
                "title": title,
                "description": title,
                "required_permissions": list(default_permissions),
                "required_capabilities": list(default_capabilities),
                "expected_output": title,
                "acceptance_criteria": [
                    {
                        "id": "done",
                        "text": f"Task '{title}' is completed according to its description.",
                    }
                ],
                "priority": _priority(number),
                "depends_on": depends_on,
            }
            continue
        if current is not None:
            description_lines.append(line)

    flush()
    return tasks


def _parent_number(number: str) -> str | None:
    parts = number.split(".")
    return ".".join(parts[:-1]) if len(parts) > 1 else None


def _priority(number: str) -> int:
    parts = [int(part) for part in number.split(".")]
    value = 0
    for idx, part in enumerate(parts):
        value += part * (1000 ** max(0, 3 - idx))
    return value


def _task_id(number: str, title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ]+", "-", title.lower()).strip("-")
    slug = slug[:48].strip("-") or "task"
    return f"{number.replace('.', '-')}-{slug}"
