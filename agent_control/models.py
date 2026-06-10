from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    REVIEW = "review"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"


class WorkerStatus(StrEnum):
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"


@dataclass(frozen=True)
class AcceptanceCriterion:
    id: str
    text: str


@dataclass
class Task:
    id: str
    title: str
    description: str
    required_permissions: list[str]
    expected_output: str
    acceptance_criteria: list[AcceptanceCriterion]
    project_id: str = "default"
    required_capabilities: list[str] = field(default_factory=list)
    assigned_worker_id: str | None = None
    priority: int = 100
    depends_on: list[str] = field(default_factory=list)
    test_command: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        criteria = data.get("acceptance_criteria", [])
        parsed_criteria = [
            AcceptanceCriterion(
                id=str(item.get("id") or f"criterion-{idx + 1}"),
                text=str(item["text"] if isinstance(item, dict) else item),
            )
            for idx, item in enumerate(criteria)
        ]
        return cls(
            id=str(data["id"]),
            title=str(data["title"]),
            description=str(data["description"]),
            required_permissions=[str(item) for item in data.get("required_permissions", [])],
            expected_output=str(data.get("expected_output", "")),
            acceptance_criteria=parsed_criteria,
            project_id=str(data.get("project_id", "default")),
            required_capabilities=[str(item) for item in data.get("required_capabilities", [])],
            assigned_worker_id=data.get("assigned_worker_id"),
            priority=int(data.get("priority", 100)),
            depends_on=[str(item) for item in data.get("depends_on", [])],
            test_command=data.get("test_command"),
            status=TaskStatus(data.get("status", TaskStatus.PENDING)),
            notes=[str(item) for item in data.get("notes", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "required_permissions": self.required_permissions,
            "expected_output": self.expected_output,
            "acceptance_criteria": [
                {"id": criterion.id, "text": criterion.text}
                for criterion in self.acceptance_criteria
            ],
            "project_id": self.project_id,
            "required_capabilities": self.required_capabilities,
            "assigned_worker_id": self.assigned_worker_id,
            "priority": self.priority,
            "depends_on": self.depends_on,
            "test_command": self.test_command,
            "status": self.status.value,
            "notes": self.notes,
        }


@dataclass
class AgentResult:
    summary: str
    artifacts: list[str]
    criteria_checked: list[str]
    needs_consultation: bool = False
    consultation_question: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentResult":
        return cls(
            summary=str(data.get("summary", "")),
            artifacts=[str(item) for item in data.get("artifacts", [])],
            criteria_checked=[str(item) for item in data.get("criteria_checked", [])],
            needs_consultation=bool(data.get("needs_consultation", False)),
            consultation_question=str(data.get("consultation_question", "")),
        )


@dataclass
class Worker:
    id: str
    project_id: str
    label: str
    capabilities: list[str]
    dialog_name: str = ""
    custom_name: str = ""
    status: WorkerStatus = WorkerStatus.IDLE
    current_task_id: str | None = None
    lease_expires_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Worker":
        return cls(
            id=str(data["id"]),
            project_id=str(data.get("project_id", "default")),
            label=str(data.get("label") or data["id"]),
            capabilities=[str(item) for item in data.get("capabilities", [])],
            dialog_name=str(data.get("dialog_name", "")),
            custom_name=str(data.get("custom_name", "")),
            status=WorkerStatus(data.get("status", WorkerStatus.IDLE)),
            current_task_id=data.get("current_task_id"),
            lease_expires_at=data.get("lease_expires_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "label": self.label,
            "capabilities": self.capabilities,
            "dialog_name": self.dialog_name,
            "custom_name": self.custom_name,
            "display_name": self.display_name,
            "status": self.status.value,
            "current_task_id": self.current_task_id,
            "lease_expires_at": self.lease_expires_at,
        }

    @property
    def display_name(self) -> str:
        if self.custom_name:
            return self.custom_name
        if self.dialog_name:
            return f"{self.project_id}/{self.dialog_name}"
        return self.label or self.id
