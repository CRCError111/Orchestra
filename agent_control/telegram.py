from __future__ import annotations

import http.client
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from .models import TaskStatus
from .store import Store


class TelegramBot:
    def __init__(
        self,
        store: Store,
        token: str,
        allowed_chat_ids: set[int] | None = None,
        proxy_url: str = "",
        api_ip_override: str = "",
    ) -> None:
        self.store = store
        self.token = token
        self.allowed_chat_ids = allowed_chat_ids
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.proxy_url = proxy_url
        self.api_ip_override = api_ip_override
        if proxy_url.lower().startswith(("socks://", "socks5://", "socks4://")):
            raise RuntimeError("SOCKS proxies are not supported by the built-in HTTP client. Use an HTTP proxy URL.")
        if proxy_url:
            self.opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            )
        else:
            # Do not inherit broken or placeholder proxies from the parent process.
            self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

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
        proxy_url = settings.get("telegram_proxy_url", "").strip()
        api_ip_override = settings.get("telegram_api_ip_override", "").strip()
        return cls(store, token, allowed, proxy_url, api_ip_override)

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
        if self.api_ip_override and not self.proxy_url:
            return self._api_via_ip_override(method, data)
        request = urllib.request.Request(f"{self.base_url}/{method}", data=data)
        try:
            with self.opener.open(request, timeout=35) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            target = self.proxy_url or "direct connection"
            raise RuntimeError(f"Telegram API request failed via {target}: {exc.reason}") from exc

    def _api_via_ip_override(self, method: str, data: bytes) -> dict:
        connection = _SniIpHTTPSConnection(
            host="api.telegram.org",
            ip_override=self.api_ip_override,
            timeout=35,
        )
        try:
            connection.request(
                "POST",
                f"/bot{self.token}/{method}",
                body=data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Host": "api.telegram.org",
                },
            )
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            if response.status >= 400:
                raise RuntimeError(f"Telegram API HTTP {response.status}: {body[:500]}")
            return json.loads(body)
        except OSError as exc:
            raise RuntimeError(
                f"Telegram API request failed via IP override {self.api_ip_override}: {exc}"
            ) from exc
        finally:
            connection.close()


class _SniIpHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, ip_override: str, timeout: int) -> None:
        super().__init__(host=host, timeout=timeout, context=ssl.create_default_context())
        self.ip_override = ip_override

    def connect(self) -> None:
        sock = socket.create_connection((self.ip_override, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
