const statuses = ["pending", "running", "review", "blocked", "done", "failed"];
let state = {
  tasks: [],
  events: [],
  workers: [],
  reports: { completed: [], incomplete: [], totals: {} },
  settings: {},
  counts: {},
  next_task_id: null,
};
let selectedTaskId = null;
let currentView = "board";
let refreshTimer = null;
let isEditing = false;
let showOfflineWorkers = JSON.parse(localStorage.getItem("agentControlShowOfflineWorkers") || "false");

const defaultCollapsed = Object.fromEntries(statuses.map((status) => [status, status !== "pending"]));
let collapsedColumns = JSON.parse(localStorage.getItem("agentControlCollapsedColumns") || "null") || defaultCollapsed;

const els = {
  metrics: document.querySelector("#metrics"),
  workers: document.querySelector("#workers"),
  nextTask: document.querySelector("#nextTask"),
  refreshStatus: document.querySelector("#refreshStatus"),
  kanban: document.querySelector("#kanban"),
  taskDetail: document.querySelector("#taskDetail"),
  emptyState: document.querySelector("#emptyState"),
  refreshBtn: document.querySelector("#refreshBtn"),
  runDryBtn: document.querySelector("#runDryBtn"),
  importForm: document.querySelector("#importForm"),
  importProject: document.querySelector("#importProject"),
  importCapabilities: document.querySelector("#importCapabilities"),
  importPermissions: document.querySelector("#importPermissions"),
  importText: document.querySelector("#importText"),
  importResult: document.querySelector("#importResult"),
  reports: document.querySelector("#reports"),
  workerForm: document.querySelector("#workerForm"),
  workerId: document.querySelector("#workerId"),
  workerProject: document.querySelector("#workerProject"),
  workerDialog: document.querySelector("#workerDialog"),
  workerLabel: document.querySelector("#workerLabel"),
  workerCustomName: document.querySelector("#workerCustomName"),
  workerCapabilities: document.querySelector("#workerCapabilities"),
  workerResult: document.querySelector("#workerResult"),
  workerEditorList: document.querySelector("#workerEditorList"),
  showOfflineWorkers: document.querySelector("#showOfflineWorkers"),
  settingsForm: document.querySelector("#settingsForm"),
  fetchTelegramChats: document.querySelector("#fetchTelegramChats"),
  telegramChatsResult: document.querySelector("#telegramChatsResult"),
  telegramChatsList: document.querySelector("#telegramChatsList"),
  telegramToken: document.querySelector("#telegramToken"),
  telegramChats: document.querySelector("#telegramChats"),
  telegramProxyUrl: document.querySelector("#telegramProxyUrl"),
  telegramApiIpOverride: document.querySelector("#telegramApiIpOverride"),
  autoRefreshSeconds: document.querySelector("#autoRefreshSeconds"),
  settingsResult: document.querySelector("#settingsResult"),
  projectColorList: document.querySelector("#projectColorList"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok || data.error) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

async function loadState({ silent = false } = {}) {
  if (isEditing && silent) return;
  state = await api("/api/state");
  if (!selectedTaskId && state.tasks.length) {
    selectedTaskId = state.next_task_id || state.tasks[0].id;
  }
  render();
  scheduleRefresh();
}

function render() {
  renderMetrics();
  renderWorkers();
  renderKanban();
  renderDetail();
  renderReports();
  renderSettings();
  renderProjectColors();
}

function renderMetrics() {
  els.metrics.innerHTML = statuses.map((status) => `
    <div class="metric">
      <span>${escapeHtml(status)}</span>
      <strong>${state.counts[status] || 0}</strong>
    </div>
  `).join("");
  const next = state.tasks.find((task) => task.id === state.next_task_id);
  els.nextTask.textContent = next ? `Next: ${next.id} | ${next.title}` : "No available next task";
}

function renderWorkers() {
  const visibleWorkers = state.workers.filter((worker) => showOfflineWorkers || worker.status !== "offline");
  els.workers.innerHTML = visibleWorkers.map((worker) => `
    <button class="worker" data-edit-worker="${escapeAttr(worker.id)}" style="--project-color: ${projectColor(worker.project_id)}">
      <div class="workerHead">
        <div>
          <div class="workerTitle">${escapeHtml(worker.display_name || worker.label)}</div>
          <div class="workerMeta"><span class="projectDot"></span>${escapeHtml(worker.id)} | ${escapeHtml(worker.project_id)}</div>
        </div>
        <span class="badge ${escapeAttr(worker.status)}">${escapeHtml(worker.status)}</span>
      </div>
      <div class="workerMeta">current: ${escapeHtml(worker.current_task_id || "-")}</div>
      <div class="workerMeta">lease: ${escapeHtml(worker.lease_expires_at || "-")}</div>
      <div class="chips">${chips(worker.capabilities)}</div>
    </button>
  `).join("");

  document.querySelectorAll("[data-edit-worker]").forEach((button) => {
    button.addEventListener("click", () => {
      fillWorkerForm(button.dataset.editWorker);
      showView("settings");
    });
  });
}

function renderKanban() {
  els.kanban.innerHTML = statuses.map((status) => {
    const tasks = state.tasks.filter((task) => task.status === status);
    const collapsed = Boolean(collapsedColumns[status]);
    return `
      <section class="kanbanColumn ${collapsed ? "collapsed" : ""}">
        <button class="columnHead" data-toggle-column="${escapeAttr(status)}">
          <h2>${escapeHtml(status)}</h2>
          <span>${tasks.length}</span>
        </button>
        <div class="columnTasks" data-drop-status="${escapeAttr(status)}">
          ${tasks.map(taskCard).join("") || `<div class="columnEmpty">No tasks</div>`}
        </div>
      </section>
    `;
  }).join("");

  document.querySelectorAll("[data-toggle-column]").forEach((button) => {
    button.addEventListener("click", () => {
      const status = button.dataset.toggleColumn;
      collapsedColumns[status] = !collapsedColumns[status];
      localStorage.setItem("agentControlCollapsedColumns", JSON.stringify(collapsedColumns));
      renderKanban();
    });
  });

  document.querySelectorAll(".kanbanCard").forEach((button) => {
    button.addEventListener("click", () => {
      selectedTaskId = button.dataset.taskId;
      render();
    });
    button.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData("text/plain", button.dataset.taskId);
      event.dataTransfer.effectAllowed = "move";
      button.classList.add("dragging");
    });
    button.addEventListener("dragend", () => button.classList.remove("dragging"));
  });

  document.querySelectorAll("[data-drop-status]").forEach((zone) => {
    zone.addEventListener("dragover", (event) => {
      event.preventDefault();
      zone.classList.add("dropTarget");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("dropTarget"));
    zone.addEventListener("drop", async (event) => {
      event.preventDefault();
      zone.classList.remove("dropTarget");
      const taskId = event.dataTransfer.getData("text/plain");
      const status = zone.dataset.dropStatus;
      if (taskId && status) {
        selectedTaskId = taskId;
        await setStatus(taskId, status);
      }
    });
  });
}

function taskCard(task) {
  return `
    <button class="kanbanCard ${task.id === selectedTaskId ? "active" : ""}" draggable="true" data-task-id="${escapeAttr(task.id)}" style="--project-color: ${projectColor(task.project_id)}">
      <div class="taskTitle">${escapeHtml(task.title)}</div>
      <div class="taskMeta"><span class="projectDot"></span>${escapeHtml(task.project_id)} | ${escapeHtml(task.id)}</div>
      <div class="taskMeta">priority ${task.priority} | ${task.criteria_count} criteria | ${task.notes_count} notes</div>
      <div class="chips compact">${chipsInline(task.required_capabilities)}</div>
    </button>
  `;
}

function renderDetail() {
  const task = state.tasks.find((item) => item.id === selectedTaskId);
  els.emptyState.classList.toggle("hidden", Boolean(task));
  els.taskDetail.classList.toggle("hidden", !task);
  if (!task) return;

  const events = state.events.filter((event) => event.task_id === task.id).slice(0, 10);
  const allTaskEvents = state.events.filter((event) => event.task_id === task.id);
  els.taskDetail.innerHTML = `
    <div class="detailHead">
      <div>
        <h2>${escapeHtml(task.title)}</h2>
        <div class="idLine" style="--project-color: ${projectColor(task.project_id)}"><span class="projectDot"></span>${escapeHtml(task.project_id)} | ${escapeHtml(task.id)} | priority ${task.priority}</div>
      </div>
      <span class="badge ${escapeAttr(task.status)}">${escapeHtml(task.status)}</span>
    </div>

    <div class="buttonRow">
      ${statusButton(task, "pending", "Queue")}
      ${statusButton(task, "done", "Approve")}
      ${statusButton(task, "review", "Review")}
      ${statusButton(task, "blocked", "Block")}
      ${statusButton(task, "failed", "Fail", "danger")}
    </div>

    <form class="priorityForm" id="priorityForm">
      <label>
        Priority
        <input id="priorityInput" type="number" value="${escapeAttr(task.priority)}">
      </label>
      <button type="submit">Save priority</button>
    </form>

    ${section("Description", `<p>${escapeHtml(task.description)}</p>`)}
    ${section("Expected Output", `<p>${escapeHtml(task.expected_output || "-")}</p>`)}
    ${section("Permissions", `<div class="chips">${chips(task.required_permissions)}</div>`)}
    ${section("Routing", `
      <div class="chips">
        <span class="chip">worker: ${escapeHtml(task.assigned_worker_id || "auto")}</span>
        ${chipsInline(task.required_capabilities) || `<span class="chip">capabilities: -</span>`}
      </div>
    `)}
    ${section("Acceptance Criteria", `
      <div class="criteria">
        ${task.acceptance_criteria.map((criterion) => `
          <div class="criterion">
            <strong>${escapeHtml(criterion.id)}</strong>
            ${escapeHtml(criterion.text)}
          </div>
        `).join("") || "-"}
      </div>
    `)}
    ${workResultSection(task, allTaskEvents)}
    ${section("Notes", `
      <div class="notes">${task.notes.map((note) => `<div class="note">${escapeHtml(note)}</div>`).join("") || `<div class="note">-</div>`}</div>
      <form class="formRow" id="noteForm">
        <textarea id="noteText" placeholder="Task note"></textarea>
        <button class="primary" type="submit">Add note</button>
      </form>
    `)}
    ${section("Events", `
      <div class="events">
        ${events.map((event) => `
          <div class="event">
            <strong>#${event.id} | ${escapeHtml(event.created_at)} | ${escapeHtml(event.kind)}</strong>
            <pre>${escapeHtml(JSON.stringify(event.payload, null, 2))}</pre>
          </div>
        `).join("") || `<div class="event">-</div>`}
      </div>
    `)}
  `;

  document.querySelectorAll("[data-status]").forEach((button) => {
    button.addEventListener("click", async () => {
      await setStatus(task.id, button.dataset.status);
    });
  });

  document.querySelector("#noteForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = document.querySelector("#noteText").value.trim();
    if (!text) return;
    await api(`/api/tasks/${encodeURIComponent(task.id)}/note`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    await loadState();
  });

  document.querySelector("#priorityForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const priority = Number(document.querySelector("#priorityInput").value);
    if (Number.isFinite(priority)) {
      await setPriority(task.id, priority);
    }
  });
}

function workResultSection(task, events) {
  const result = latestEvent(events, "agent_result");
  const tests = latestEvent(events, "tests_finished");
  if (!result && !tests) {
    return task.status === "done"
      ? section("Work Result", `<p>No structured result has been recorded for this completed task.</p>`)
      : "";
  }
  const resultPayload = result?.payload || {};
  const testsPayload = tests?.payload || {};
  return section("Work Result", `
    <div class="workResult">
      ${result ? `
        <div class="resultBlock">
          <strong>Summary</strong>
          <p>${escapeHtml(resultPayload.summary || "-")}</p>
        </div>
        <div class="resultBlock">
          <strong>Artifacts</strong>
          <div class="chips">${chips(resultPayload.artifacts || [])}</div>
        </div>
        <div class="resultBlock">
          <strong>Acceptance</strong>
          <div class="chips">${chips(resultPayload.criteria_checked || [])}</div>
          <p>tests_ok: ${escapeHtml(String(resultPayload.tests_ok ?? "-"))} | criteria_ok: ${escapeHtml(String(resultPayload.criteria_ok ?? "-"))}</p>
        </div>
      ` : ""}
      ${tests ? `
        <div class="resultBlock">
          <strong>Tests</strong>
          <p>${escapeHtml(testsPayload.command || "-")}</p>
          <p>returncode: ${escapeHtml(String(testsPayload.returncode))}</p>
          ${testsPayload.stdout ? `<pre>${escapeHtml(testsPayload.stdout)}</pre>` : ""}
          ${testsPayload.stderr ? `<pre>${escapeHtml(testsPayload.stderr)}</pre>` : ""}
        </div>
      ` : ""}
    </div>
  `);
}

function latestEvent(events, kind) {
  return events.find((event) => event.kind === kind) || null;
}

function renderReports() {
  const reports = state.reports || { completed: [], incomplete: [], totals: {} };
  els.reports.innerHTML = `
    <div class="reportSummary">
      <div class="metric"><span>completed</span><strong>${reports.totals.completed || 0}</strong></div>
      <div class="metric"><span>incomplete</span><strong>${reports.totals.incomplete || 0}</strong></div>
      <div class="metric"><span>all</span><strong>${reports.totals.all || 0}</strong></div>
    </div>
    <div class="reportGrid">
      <section class="reportPanel">
        <h2>Completed With Results</h2>
        ${reportRows(reports.completed, true)}
      </section>
      <section class="reportPanel">
        <h2>Incomplete With Reasons</h2>
        ${reportRows(reports.incomplete, false)}
      </section>
    </div>
  `;
}

function renderSettings() {
  const settings = state.settings || {};
  if (!document.activeElement || !document.activeElement.closest("#settingsView")) {
    els.telegramChats.value = settings.telegram_allowed_chat_ids || "";
    els.telegramProxyUrl.value = settings.telegram_proxy_url || "";
    els.telegramApiIpOverride.value = settings.telegram_api_ip_override || "";
    els.autoRefreshSeconds.value = settings.auto_refresh_seconds || "5";
  }
  els.refreshStatus.textContent = `Auto refresh: ${settings.auto_refresh_seconds || 5}s`;
  els.showOfflineWorkers.checked = showOfflineWorkers;
  const visibleWorkers = state.workers.filter((worker) => showOfflineWorkers || worker.status !== "offline");
  els.workerEditorList.innerHTML = visibleWorkers.map((worker) => `
    <div class="settingsRow">
      <button class="settingsRowMain" data-edit-worker="${escapeAttr(worker.id)}">
        <strong>${escapeHtml(worker.display_name || worker.label)}</strong>
        <span>${escapeHtml(worker.id)} | ${escapeHtml(worker.status)} | ${escapeHtml((worker.capabilities || []).join(","))}</span>
      </button>
      <button data-worker-status="${escapeAttr(worker.id)}" data-status="${worker.status === "offline" ? "idle" : "offline"}">
        ${worker.status === "offline" ? "Reactivate" : "Set offline"}
      </button>
    </div>
  `).join("") || `<div class="columnEmpty">No workers registered</div>`;

  document.querySelectorAll("#workerEditorList [data-edit-worker]").forEach((button) => {
    button.addEventListener("click", () => fillWorkerForm(button.dataset.editWorker));
  });
  document.querySelectorAll("[data-worker-status]").forEach((button) => {
    button.addEventListener("click", async () => {
      const workerId = button.dataset.workerStatus;
      const status = button.dataset.status;
      const result = await api(`/api/workers/${encodeURIComponent(workerId)}/status`, {
        method: "POST",
        body: JSON.stringify({ status }),
      });
      state = result.state;
      render();
    });
  });
}

function reportRows(rows, completed) {
  return rows.map((row) => {
    const event = completed ? row.last_result : row.last_reason;
    return `
      <article class="reportRow">
        <div class="reportTitle">${escapeHtml(row.title)}</div>
        <div class="taskMeta" style="--project-color: ${projectColor(row.project_id)}"><span class="projectDot"></span>${escapeHtml(row.project_id)} | ${escapeHtml(row.id)} | ${escapeHtml(row.status)}</div>
        <pre>${escapeHtml(event ? JSON.stringify(event.payload, null, 2) : fallbackReason(row))}</pre>
      </article>
    `;
  }).join("") || `<div class="columnEmpty">No rows</div>`;
}

function projectColor(projectId) {
  const overrides = projectColorOverrides();
  if (overrides[projectId]) return overrides[projectId];
  const palette = ["#1f7a68", "#6c5fba", "#a15c18", "#357342", "#2e6f9e", "#a4343a", "#6f6a2e", "#8b4c7a"];
  let hash = 0;
  for (const char of String(projectId || "default")) {
    hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
  }
  return palette[Math.abs(hash) % palette.length];
}

function projectColorOverrides() {
  try {
    return JSON.parse(state.settings?.project_colors || "{}");
  } catch {
    return {};
  }
}

function projectIds() {
  const ids = new Set();
  state.tasks.forEach((task) => ids.add(task.project_id || "default"));
  state.workers.forEach((worker) => ids.add(worker.project_id || "default"));
  return [...ids].sort();
}

function renderProjectColors() {
  if (!els.projectColorList) return;
  const overrides = projectColorOverrides();
  els.projectColorList.innerHTML = projectIds().map((projectId) => {
    const color = projectColor(projectId);
    return `
      <div class="settingsRow" style="--project-color: ${color}">
        <div class="settingsRowMain">
          <strong><span class="projectDot"></span>${escapeHtml(projectId)}</strong>
          <span>${overrides[projectId] ? "Custom color" : "Automatic color"}</span>
        </div>
        <input class="colorInput" type="color" data-project-color="${escapeAttr(projectId)}" value="${escapeAttr(color)}">
      </div>
    `;
  }).join("") || `<div class="columnEmpty">No projects yet</div>`;

  document.querySelectorAll("[data-project-color]").forEach((input) => {
    input.addEventListener("change", async () => {
      const next = projectColorOverrides();
      next[input.dataset.projectColor] = input.value;
      await saveSettings({ project_colors: JSON.stringify(next) });
    });
  });
}

function fallbackReason(row) {
  if (row.notes && row.notes.length) return row.notes[row.notes.length - 1];
  return "No result or blocking reason recorded yet.";
}

function fillWorkerForm(workerId) {
  const worker = state.workers.find((item) => item.id === workerId);
  if (!worker) return;
  els.workerId.value = worker.id;
  els.workerProject.value = worker.project_id || "";
  els.workerDialog.value = worker.dialog_name || "";
  els.workerLabel.value = worker.label || "";
  els.workerCustomName.value = worker.custom_name || "";
  els.workerCapabilities.value = (worker.capabilities || []).join(",");
}

function section(title, body) {
  return `<section class="section"><h3>${escapeHtml(title)}</h3>${body}</section>`;
}

function statusButton(task, status, label, klass = "") {
  const disabled = task.status === status ? "disabled" : "";
  return `<button class="${klass}" data-status="${escapeAttr(status)}" ${disabled}>${escapeHtml(label)}</button>`;
}

function chips(items) {
  return chipsInline(items) || `<span class="chip">-</span>`;
}

function chipsInline(items) {
  return (items || []).map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("");
}

async function setStatus(taskId, status) {
  await api(`/api/tasks/${encodeURIComponent(taskId)}/status`, {
    method: "POST",
    body: JSON.stringify({ status }),
  });
  await loadState();
}

async function setPriority(taskId, priority) {
  await api(`/api/tasks/${encodeURIComponent(taskId)}/priority`, {
    method: "POST",
    body: JSON.stringify({ priority }),
  });
  await loadState();
}

function showView(view) {
  currentView = view;
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  document.querySelectorAll(".view").forEach((panel) => {
    panel.classList.toggle("hidden", panel.id !== `${view}View`);
  });
}

function scheduleRefresh() {
  clearTimeout(refreshTimer);
  const seconds = Math.max(2, Number(state.settings?.auto_refresh_seconds || 5));
  refreshTimer = setTimeout(() => {
    loadState({ silent: true }).catch((error) => {
      els.nextTask.textContent = error.message;
      scheduleRefresh();
    });
  }, seconds * 1000);
}

function markEditing(event) {
  isEditing = ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName);
}

function clearEditing(event) {
  if (["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) {
    isEditing = false;
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}

els.refreshBtn.addEventListener("click", () => loadState());
els.runDryBtn.addEventListener("click", async () => {
  await api("/api/run-once", {
    method: "POST",
    body: JSON.stringify({ dry_run: true }),
  });
  await loadState();
});

els.importForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  els.importResult.textContent = "Importing...";
  try {
    const result = await api("/api/import-text-plan", {
      method: "POST",
      body: JSON.stringify({
        text: els.importText.value,
        project_id: els.importProject.value || "default",
        capabilities: els.importCapabilities.value,
        permissions: els.importPermissions.value,
      }),
    });
    state = result.state;
    els.importResult.textContent = `Imported ${result.imported} tasks`;
    render();
    showView("board");
  } catch (error) {
    els.importResult.textContent = error.message;
  }
});

els.workerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const workerId = els.workerId.value.trim();
  if (!workerId) {
    els.workerResult.textContent = "Worker ID is required";
    return;
  }
  const body = {
    id: workerId,
    project_id: els.workerProject.value || "default",
    dialog_name: els.workerDialog.value,
    label: els.workerLabel.value || workerId,
    custom_name: els.workerCustomName.value,
    capabilities: els.workerCapabilities.value,
  };
  const exists = state.workers.some((worker) => worker.id === workerId);
  const path = exists ? `/api/workers/${encodeURIComponent(workerId)}/update` : "/api/workers/register";
  const result = await api(path, { method: "POST", body: JSON.stringify(body) });
  state = result.state;
  els.workerResult.textContent = "Saved";
  render();
});

els.settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveSettings({
      telegram_bot_token: els.telegramToken.value,
      telegram_allowed_chat_ids: els.telegramChats.value,
      telegram_proxy_url: els.telegramProxyUrl.value,
      telegram_api_ip_override: els.telegramApiIpOverride.value,
      auto_refresh_seconds: els.autoRefreshSeconds.value || "5",
  });
  els.telegramToken.value = "";
  els.settingsResult.textContent = "Saved";
});

async function saveSettings(patch) {
  const result = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({
      telegram_allowed_chat_ids: els.telegramChats.value,
      telegram_proxy_url: els.telegramProxyUrl.value,
      telegram_api_ip_override: els.telegramApiIpOverride.value,
      auto_refresh_seconds: els.autoRefreshSeconds.value || "5",
      project_colors: state.settings?.project_colors || "{}",
      ...patch,
    }),
  });
  state = result.state;
  render();
  return result;
}

els.fetchTelegramChats.addEventListener("click", async () => {
  els.telegramChatsResult.textContent = "Fetching...";
  els.telegramChatsList.innerHTML = "";
  try {
    const result = await api("/api/telegram/recent-chats", { method: "POST", body: "{}" });
    els.telegramChatsResult.textContent = result.chats.length ? `Found ${result.chats.length}` : "No chats found";
    els.telegramChatsList.innerHTML = result.chats.map((chat) => `
      <button class="settingsRowMain" data-chat-id="${escapeAttr(chat.id)}">
        <strong>${escapeHtml(chat.title || chat.id)}</strong>
        <span>${escapeHtml(chat.id)} | ${escapeHtml(chat.type || "-")} ${chat.username ? `| @${escapeHtml(chat.username)}` : ""}</span>
      </button>
    `).join("") || `<div class="columnEmpty">Send /start to the bot, then fetch again.</div>`;
    document.querySelectorAll("[data-chat-id]").forEach((button) => {
      button.addEventListener("click", () => {
        const chatId = button.dataset.chatId;
        const existing = els.telegramChats.value.split(",").map((item) => item.trim()).filter(Boolean);
        if (!existing.includes(chatId)) {
          existing.push(chatId);
          els.telegramChats.value = existing.join(",");
        }
      });
    });
  } catch (error) {
    els.telegramChatsResult.textContent = `${error.message}. Check Telegram token, network, VPN, firewall, or Telegram proxy URL.`;
  }
});

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => showView(button.dataset.view));
});

els.showOfflineWorkers.addEventListener("change", () => {
  showOfflineWorkers = els.showOfflineWorkers.checked;
  localStorage.setItem("agentControlShowOfflineWorkers", JSON.stringify(showOfflineWorkers));
  renderWorkers();
  renderSettings();
});

document.addEventListener("focusin", markEditing);
document.addEventListener("focusout", clearEditing);

showView(currentView);
loadState().catch((error) => {
  els.nextTask.textContent = error.message;
  scheduleRefresh();
});
