from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

from .models import TaskStatus
from .store import Store


class TelegramBot:
    def __init__(self, store: Store, token: str, allowed_chat_ids: set[int] | None = None) -> None:
        self.store = store
        self.token = token
        self.allowed_chat_ids = allowed_chat_ids
        self.base_url = f"https://api.telegram.org/bot{token}"

    @classmethod
    def from_env(cls, store: Store) -> "TelegramBot":
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError("Set TELEGRAM_BOT_TOKEN first.")
        raw_ids = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
        allowed = {int(item.strip()) for item in raw_ids.split(",") if item.strip()} or None
        return cls(store, token, allowed)

    @classmethod
    def from_settings(cls, store: Store) -> "TelegramBot":
        settings = store.get_settings()
        token = settings.get("telegram_bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError("Set telegram_bot_token in Settings or TELEGRAM_BOT_TOKEN.")
        raw_ids = settings.get("telegram_allowed_chat_ids") or os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
        allowed = {int(item.strip()) for item in raw_ids.split(",") if item.strip()} or None
        return cls(store, token, allowed)

    def recent_chats(self) -> list[dict]:
        updates = self._api("getUpdates", {"timeout": 1})
        chats: dict[int, dict] = {}
        for update in updates.get("result", []):
            message = update.get("message") or update.get("edited_message")
            if not message or "chat" not in message:
                continue
            chat = message["chat"]
            chat_id = int(chat["id"])
            title = chat.get("title") or " ".join(
                part for part in [chat.get("first_name"), chat.get("last_name")] if part
            )
            chats[chat_id] = {
                "id": chat_id,
                "type": chat.get("type", ""),
                "title": title or chat.get("username") or str(chat_id),
                "username": chat.get("username", ""),
            }
        return list(chats.values())

    def serve_forever(self) -> None:
        offset = 0
        while True:
            updates = self._api("getUpdates", {"timeout": 25, "offset": offset})
            for update in updates.get("result", []):
                offset = max(offset, int(update["update_id"]) + 1)
                message = update.get("message") or update.get("edited_message")
                if message:
                    self._handle_message(message)
            time.sleep(1)

    def _handle_message(self, message: dict) -> None:
        chat_id = int(message["chat"]["id"])
        if self.allowed_chat_ids is not None and chat_id not in self.allowed_chat_ids:
            self._send(chat_id, "Access denied.")
            return
        text = str(message.get("text", "")).strip()
        try:
            reply = self._dispatch(text)
        except Exception as exc:
            reply = f"Error: {exc}"
        self._send(chat_id, reply)

    def _dispatch(self, text: str) -> str:
        if text in {"/start", "/help"}:
            return "/tasks, /task <id>, /note <id> <text>, /approve <id>, /block <id> <reason>, /resume <id>"
        if text == "/tasks":
            return self._tasks()
        if text.startswith("/task "):
            return self._task(text.split(maxsplit=1)[1])
        if text.startswith("/note "):
            _, task_id, note = text.split(maxsplit=2)
            self.store.add_note(task_id, note)
            return f"Note added to {task_id}."
        if text.startswith("/approve "):
            task_id = text.split(maxsplit=1)[1]
            self.store.set_status(task_id, TaskStatus.DONE)
            return f"Task {task_id} approved."
        if text.startswith("/block "):
            _, task_id, reason = text.split(maxsplit=2)
            self.store.add_note(task_id, f"Blocked by user: {reason}")
            self.store.set_status(task_id, TaskStatus.BLOCKED)
            return f"Task {task_id} blocked."
        if text.startswith("/resume "):
            task_id = text.split(maxsplit=1)[1]
            self.store.set_status(task_id, TaskStatus.PENDING)
            return f"Task {task_id} returned to queue."
        return "Unknown command. Send /help."

    def _tasks(self) -> str:
        tasks = self.store.list_tasks()
        if not tasks:
            return "No tasks yet."
        return "\n".join(f"{task.status.value:8} {task.id}: {task.title}" for task in tasks)

    def _task(self, task_id: str) -> str:
        task = self.store.require_task(task_id)
        criteria = "\n".join(f"- [{item.id}] {item.text}" for item in task.acceptance_criteria)
        notes = "\n".join(f"- {note}" for note in task.notes) or "-"
        return (
            f"{task.id}: {task.title}\n"
            f"Status: {task.status.value}\n"
            f"Permissions: {', '.join(task.required_permissions) or '-'}\n"
            f"Output: {task.expected_output or '-'}\n"
            f"Criteria:\n{criteria or '-'}\n"
            f"Notes:\n{notes}"
        )

    def _send(self, chat_id: int, text: str) -> None:
        self._api("sendMessage", {"chat_id": chat_id, "text": text[:3900]})

    def _api(self, method: str, params: dict) -> dict:
        data = urllib.parse.urlencode(params).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}/{method}", data=data)
        with urllib.request.urlopen(request, timeout=35) as response:
            return json.loads(response.read().decode("utf-8"))
