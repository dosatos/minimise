function statusClass(status) {
    return "status status-" + status;
}

function isTerminalStatus(status) {
    return ["completed", "failed", "stopped"].includes(status);
}

function workerLabel(t) {
    if (t.assignee) return t.assignee;
    if (t.harness) return `${t.harness}/${t.model || "default"}`;
    return "default";
}

function startJobListPolling(intervalMs) {
    const page = new URLSearchParams(location.search).get("page") || 1;
    let intervalId = null;
    let stopped = false;
    function stopPolling() {
        stopped = true;
        if (intervalId !== null) clearInterval(intervalId);
    }
    async function refresh() {
        if (document.hidden) return;
        const resp = await fetch(`/jobs?page=${page}`);
        if (!resp.ok) return;
        const jobs = await resp.json();
        const tbody = document.getElementById("job-rows");
        if (!tbody) return;
        if (jobs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="empty">No jobs yet</td></tr>';
            stopPolling();
            return;
        }
        tbody.innerHTML = jobs.map(job => {
            const done = (job.tasks || []).filter(t => t.status === "completed").length;
            const total = (job.tasks || []).length;
            return `<tr>
                <td data-label="ID"><a href="/jobs/${job.id}/view">${job.id}</a></td>
                <td data-label="Name">${job.name}</td>
                <td data-label="Status"><span class="${statusClass(job.status)}">${job.status}</span></td>
                <td data-label="Tasks">${done}/${total}</td>
                <td data-label="Created">${job.created_at || ""}</td>
            </tr>`;
        }).join("");
        if (jobs.every(job => isTerminalStatus(job.status))) {
            stopPolling();
        }
    }
    document.addEventListener("visibilitychange", () => {
        if (!stopped && !document.hidden) refresh();
    });
    refresh();
    intervalId = setInterval(refresh, intervalMs);
}

function startJobDetailPolling(jobId, intervalMs) {
    let intervalId = null;
    let stopped = false;
    function stopPolling() {
        stopped = true;
        if (intervalId !== null) clearInterval(intervalId);
    }
    async function refresh() {
        if (document.hidden) return;
        const resp = await fetch(`/jobs/${jobId}`);
        if (!resp.ok) return;
        const job = await resp.json();
        const statusEl = document.getElementById("job-status");
        if (statusEl) {
            statusEl.textContent = job.status;
            statusEl.className = statusClass(job.status);
        }
        const tbody = document.getElementById("task-rows");
        if (tbody) {
            const taskRows = (job.tasks || []).map(t => ({
                started_at: t.started_at,
                html: `<tr>
                <td data-label="ID">${t.id}</td><td data-label="Name">${t.name}</td>
                <td data-label="Goal">${t.goal || ""}</td>
                <td data-label="Worker">${workerLabel(t)}</td>
                <td data-label="Status"><span class="${statusClass(t.status)}">${t.status}</span></td>
                <td data-label="Retries">${t.retries}</td>
                <td data-label="Type">task</td>
            </tr>`,
            }));
            const hookRows = (job.hooks || []).map(h => ({
                started_at: h.started_at,
                html: `<tr>
                <td data-label="ID">—</td><td data-label="Name">${h.hook_name}</td><td data-label="Goal">${h.execution_type}</td>
                <td data-label="Worker">—</td><td data-label="Status"><span class="${statusClass(h.status)}">${h.status}</span></td>
                <td data-label="Retries">—</td><td data-label="Type">hook</td>
            </tr>`,
            }));
            const rows = taskRows.concat(hookRows).sort((a, b) => {
                if (!a.started_at && !b.started_at) return 0;
                if (!a.started_at) return 1;
                if (!b.started_at) return -1;
                return a.started_at < b.started_at ? -1 : a.started_at > b.started_at ? 1 : 0;
            });
            tbody.innerHTML = rows.map(r => r.html).join("") || '<tr><td colspan="7" class="empty">No tasks</td></tr>';
        }
        if (isTerminalStatus(job.status)) {
            stopPolling();
        }
    }
    document.addEventListener("visibilitychange", () => {
        if (!stopped && !document.hidden) refresh();
    });
    refresh();
    intervalId = setInterval(refresh, intervalMs);
}

function togglePlanView() {
    const raw = document.getElementById("plan-raw");
    const btn = document.getElementById("plan-toggle-btn");
    const showing = raw.style.display !== "none";
    raw.style.display = showing ? "none" : "block";
    btn.textContent = showing ? "Show raw plan YAML" : "Hide raw plan YAML";
}

async function loadPlan(jobId) {
    const resp = await fetch(`/jobs/${jobId}/plan`);
    if (!resp.ok) return;
    const data = await resp.json();
    document.getElementById("plan-raw").textContent = data.raw_yaml;
}

function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s ?? "";
    return d.innerHTML;
}

function logColumnClass(col) {
    return "log-col-" + col;
}

function applyLogColumnVisibility() {
    document.querySelectorAll(".log-col-toggle").forEach(cb => {
        const cells = document.querySelectorAll("." + logColumnClass(cb.dataset.col));
        cells.forEach(cell => cell.classList.toggle("col-hidden", !cb.checked));
    });
}

function syncHookOptions(hookNames) {
    const optgroup = document.getElementById("log-hook-optgroup");
    if (!optgroup || !hookNames) return;
    const existing = new Set(Array.from(optgroup.children).map(o => o.value));
    hookNames.forEach(name => {
        if (existing.has(name)) return;
        const opt = document.createElement("option");
        opt.value = name;
        opt.dataset.kind = "hook";
        opt.textContent = name;
        optgroup.appendChild(opt);
    });
}

async function refreshLogs(jobId) {
    const select = document.getElementById("log-task-filter");
    const opt = select?.selectedOptions[0];
    const filter = opt?.value || "all";
    const kind = opt?.dataset.kind;
    let url = `/jobs/${jobId}/logs?limit=100`;
    if (filter !== "all") {
        url += kind === "hook"
            ? `&hook_name=${encodeURIComponent(filter)}`
            : `&task_id=${encodeURIComponent(filter)}`;
    }
    const resp = await fetch(url);
    if (!resp.ok) return;
    const data = await resp.json();
    const tbody = document.getElementById("log-rows");
    if (!tbody) return;
    const records = data.records || [];
    tbody.innerHTML = records.map(r => `<tr>
        <td class="log-col-timestamp" data-label="Timestamp">${r.timestamp}</td><td class="log-col-task_id" data-label="Task ID">${r.task_id ?? ""}</td>
        <td class="log-col-level" data-label="Level">${r.level}</td>
        <td class="log-col-message" data-label="Message">${escapeHtml(r.message)}</td>
    </tr>`).join("") || '<tr><td colspan="4" class="empty">No logs yet</td></tr>';
    applyLogColumnVisibility();
    syncHookOptions(data.hook_names);
}

function startLogPolling(jobId, intervalMs) {
    let intervalId = null;
    let stopped = false;
    function stopPolling() {
        stopped = true;
        if (intervalId !== null) clearInterval(intervalId);
    }
    function tick() {
        if (document.hidden) return;
        const statusEl = document.getElementById("job-status");
        if (!statusEl) return;
        const status = statusEl.className.replace("status status-", "");
        if (isTerminalStatus(status)) {
            stopPolling();
            return;
        }
        if (statusEl.className.includes("status-running")) {
            refreshLogs(jobId);
        }
    }
    document.addEventListener("visibilitychange", () => {
        if (!stopped && !document.hidden) tick();
    });
    refreshLogs(jobId);
    intervalId = setInterval(tick, intervalMs);
}

function refreshLogsNow() {
    const jobId = document.querySelector("[data-job-id]")?.dataset.jobId;
    if (jobId) refreshLogs(jobId);
}
