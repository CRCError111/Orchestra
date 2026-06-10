# Agent Control

MVP диспетчера задач для агентной работы.

Система принимает план задач, хранит состояние в SQLite, выбирает следующую доступную задачу, передает ее агенту, запускает проверки, принимает задачу или переводит ее на ревью/консультацию. Telegram используется как пульт управления: посмотреть очередь, оставить замечание, одобрить или заблокировать задачу.

## Быстрый старт

```powershell
python -m agent_control.cli init
python -m agent_control.cli import-plan .\examples\plan.json
python -m agent_control.cli run-once --dry-run
python -m agent_control.cli status
python -m agent_control.cli ui
```

## Запуск с реальным агентом

Команда агента задается шаблоном. В него можно вставить `{task_file}` и `{result_file}`:

```powershell
python -m agent_control.cli run-once --agent-command "python .\examples\fake_agent.py --task {task_file} --result {result_file}"
```

Агент получает JSON задачи и должен записать JSON результата:

```json
{
  "summary": "Что сделано",
  "artifacts": ["path/to/file"],
  "criteria_checked": ["criterion-id-or-text"],
  "needs_consultation": false,
  "consultation_question": ""
}
```

Если у задачи есть `test_command`, диспетчер запустит ее после агента. Задача автоматически принимается, когда:

- агент вернул успешный код;
- `needs_consultation` не выставлен;
- `test_command` отсутствует или завершился с кодом 0;
- все acceptance criteria отмечены в `criteria_checked`.

Иначе задача уходит в `review` или `blocked`.

## Telegram

Создайте бота через BotFather и передайте токен:

```powershell
python -m agent_control.cli telegram
```

The bot token and allowed chat IDs can be saved in the UI Settings tab. Environment variables `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_CHAT_IDS` are still supported as fallback.

To find chat IDs, send `/start` to the bot, then use **Settings -> Fetch recent chats**. Click a returned chat to add its ID to `Allowed chat IDs`.

Команды:

- `/tasks` - список задач
- `/task <id>` - подробности
- `/note <id> <text>` - добавить замечание к задаче
- `/approve <id>` - принять задачу вручную
- `/block <id> <reason>` - заблокировать задачу
- `/resume <id>` - вернуть задачу в очередь

Чтобы ограничить доступ, задайте `TELEGRAM_ALLOWED_CHAT_IDS` через запятую.

## Web UI

```powershell
python -m agent_control.cli ui --host 127.0.0.1 --port 8765
```

Откройте [http://127.0.0.1:8765](http://127.0.0.1:8765).

UI is the main operator surface:

- **Board** - Kanban board by task status, workers, task details, notes, events, and status actions.
- **Import** - paste a human-readable numbered task list and import it into a project with default permissions/capabilities.
- **Reports** - completed tasks with agent results, and incomplete tasks with the latest recorded reason.
- **Settings** - register/edit executors, set Telegram bot credentials, and configure auto-refresh.
- **Project colors** - each `project_id` gets a stable automatic color, visible on Kanban cards, Reports, and Workers; colors can be overridden in Settings.

From the board you can queue, approve, review, block, fail, comment on tasks, and run the next task in `dry-run`.
Kanban columns can be collapsed by clicking their headers. On first page load, only `pending` is expanded.

## Диалоги как workers

Каждый открытый Codex-диалог можно зарегистрировать как worker в конкретном проекте:

```powershell
python -m agent_control.cli worker-register --id ui-1 --project orchestrator --label "UI dialog" --cap frontend,python,tests
python -m agent_control.cli worker-register --id telegram-1 --project orchestrator --label "Telegram dialog" --cap python,telegram
python -m agent_control.cli workers
```

Executor display names use `Project/Dialog` when no custom name is set. A custom display name can be edited in the UI Settings tab.

## Useful workflows

- Feature chains: backend -> UI -> tests -> docs.
- Parallel branches across several Codex dialogs.
- Code review flow: one worker writes, another reviews.
- Regression and smoke test runs.
- Release prep: changelog, build, smoke-test, packaging.
- Documentation: plan, draft, edit, final check.
- Hardware/firmware pipelines: schematic, ERC/DRC, firmware build, reports.
- Research tasks: collect facts, verify sources, summarize.
- Human approval tasks: stop and ask in Telegram.

Задачи можно оставить на автоматический подбор по `project_id` и `required_capabilities`, либо закрепить за конкретным worker:

```powershell
python -m agent_control.cli task-assign build-web-ui ui-1
python -m agent_control.cli task-unassign build-web-ui
```

Worker забирает следующую совместимую задачу:

```powershell
python -m agent_control.cli worker-next ui-1
```

Оркестратор ставит lease, переводит задачу в `running` и пишет файл:

```text
.agent_control/inbox/ui-1/task.json
```

После выполнения worker пишет результат:

```text
.agent_control/outbox/ui-1/result.json
```

И завершает задачу:

```powershell
python -m agent_control.cli worker-complete ui-1
```

Если нужно решение оператора:

```powershell
python -m agent_control.cli worker-block ui-1 "Нужно выбрать Telegram auth strategy"
```

Минимальный `result.json`:

```json
{
  "summary": "Что сделано",
  "artifacts": ["path/to/file"],
  "criteria_checked": ["criterion-id"],
  "needs_consultation": false,
  "consultation_question": ""
}
```

## Формат плана

Смотрите [examples/plan.json](examples/plan.json).

The preferred way to import a human-readable plan is the **Import** tab in the UI. CLI import is also available:

```powershell
python -m agent_control.cli import-text-plan .\examples\plan.txt --project orchestrator --cap docs,python --perm read_workspace,write_files
```

Поддерживается одноуровневая и многоуровневая нумерация:

```text
1. Подготовить архитектуру
Описание задачи.

1.1 Реализовать регистрацию worker
Детали подзадачи.

1.2 Реализовать выдачу задач

2) Сделать UI
```
Что писать в других диалогах

В новом Codex-диалоге для подключения к оркестратору можно написать примерно так:

Зарегистрируй этот диалог как исполнителя в оркестраторе.

Project: New project2
Dialog name: <название этого диалога>
Worker ID: <короткий-id-без-пробелов>
Capabilities: python,frontend,tests,docs,orchestrator

После регистрации возьми следующую задачу через worker-next, прочитай task.json из inbox, выполни задачу, запиши result.json в outbox и закрой задачу через worker-complete. Если нужна консультация, используй worker-block.
Например для UI-диалога:

Зарегистрируй этот диалог как исполнителя в оркестраторе.

Project: New project2
Dialog name: UI improvements
Worker ID: ui-improvements-1
Capabilities: frontend,python,tests

Работай только через протокол оркестратора:
1. worker-next ui-improvements-1
2. прочитай .agent_control/inbox/ui-improvements-1/task.json
3. выполни задачу
4. запиши .agent_control/outbox/ui-improvements-1/result.json
5. worker-complete ui-improvements-1

Если задача заблокирована или нужно мое решение, вызови worker-block ui-improvements-1 "<причина>".
Команды, которые такой диалог должен выполнить:

python -m agent_control.cli worker-register `
  --id ui-improvements-1 `
  --project "New project2" `
  --dialog "UI improvements" `
  --cap frontend,python,tests
Получить задачу:

python -m agent_control.cli worker-next ui-improvements-1
После выполнения создать:

.agent_control/outbox/ui-improvements-1/result.json
Формат:

{
  "summary": "What was done",
  "artifacts": ["path/to/changed/file"],
  "criteria_checked": ["done"],
  "needs_consultation": false,
  "consultation_question": ""
}
Закрыть задачу:

python -m agent_control.cli worker-complete ui-improvements-1
Если нужна консультация:

python -m agent_control.cli worker-block ui-improvements-1 "Need decision about Telegram auth storage"
Важное правило для других диалогов

Им не надо “знать” историю этого чата. Они должны считать оркестратор источником истины:

Read task.json. Do exactly that task. Write result.json. Complete or block.
Так параллельные диалоги не конфликтуют и не зависят от памяти друг друга.
Каждый нумерованный пункт становится задачей. Ненумерованные строки после пункта становятся описанием и ожидаемым результатом. Для вложенных пунктов оркестратор автоматически добавляет зависимость от родителя: `1.1` зависит от `1`, `2.3.1` зависит от `2.3`.
