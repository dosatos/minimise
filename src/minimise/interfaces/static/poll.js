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

function setPlanTasksExpanded(expanded) {
    document.querySelectorAll(".plan-task-card").forEach(task => {
        task.open = expanded;
    });
}

function selectDetailTab(root, name, updateHash = true) {
    const tabs = Array.from(root.querySelectorAll("[data-detail-tab]"));
    if (!tabs.some(tab => tab.dataset.detailTab === name)) {
        name = root.dataset.defaultTab || tabs[0]?.dataset.detailTab;
    }
    if (!name) return;

    tabs.forEach(tab => {
        const selected = tab.dataset.detailTab === name;
        tab.setAttribute("aria-selected", String(selected));
        tab.tabIndex = selected ? 0 : -1;
    });
    root.querySelectorAll("[data-detail-panel]").forEach(panel => {
        panel.hidden = panel.dataset.detailPanel !== name;
    });

    if (updateHash && location.hash !== `#${name}`) {
        history.replaceState(null, "", `#${name}`);
    }
    root.dispatchEvent(new CustomEvent("detailtabchange", {detail: {name}}));
}

function initDetailTabs() {
    const roots = Array.from(document.querySelectorAll("[data-detail-tabs]"));
    if (!roots.length) return;

    function selectedName(root) {
        const requested = location.hash.slice(1);
        const exists = Array.from(root.querySelectorAll("[data-detail-tab]"))
            .some(tab => tab.dataset.detailTab === requested);
        return exists ? requested : root.dataset.defaultTab;
    }

    roots.forEach(root => {
        const tabList = root.querySelector("[role='tablist']");
        const tabs = Array.from(root.querySelectorAll("[data-detail-tab]"));
        tabs.forEach(tab => {
            tab.addEventListener(
                "click",
                () => selectDetailTab(root, tab.dataset.detailTab),
            );
        });
        tabList?.addEventListener("keydown", event => {
            const currentIndex = tabs.indexOf(document.activeElement);
            if (currentIndex === -1) return;

            let nextIndex;
            if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
            if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
            if (event.key === "Home") nextIndex = 0;
            if (event.key === "End") nextIndex = tabs.length - 1;
            if (nextIndex === undefined) return;

            event.preventDefault();
            tabs[nextIndex].focus();
            tabs[nextIndex].click();
        });
    });
    window.addEventListener("hashchange", () => {
        roots.forEach(root => selectDetailTab(root, selectedName(root), false));
    });
    roots.forEach(root => selectDetailTab(root, selectedName(root), false));
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

const HOOK_PHASES = {
    pre_plan: "before all tasks",
    pre_task: "before task",
    post_task: "after task",
    post_plan: "after all tasks",
};

// Compact duration matching the CLI's humanize_duration, minus zero units:
// 42s, 7m 18s, 5m, 1h 15m, 2d 3h.
function formatSecs(secs) {
    if (secs == null || !isFinite(secs)) return "—";
    const s = Math.max(0, Math.round(secs));
    if (s < 60) return `${s}s`;
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    const rest = s % 60;
    if (d) return h ? `${d}d ${h}h` : `${d}d`;
    if (h) return m ? `${h}h ${m}m` : `${h}h`;
    return rest ? `${m}m ${rest}s` : `${m}m`;
}

// The API serializes naive UTC datetimes; read them as UTC.
function parseUtc(iso) {
    if (!iso) return null;
    return new Date(/(Z|[+-]\d\d:\d\d)$/.test(iso) ? iso : iso + "Z");
}

function clockTime(date) {
    return date ? date.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "";
}

function localizeTimes(root = document) {
    root.querySelectorAll("time[data-local-time]").forEach(el => {
        const date = parseUtc(el.getAttribute("datetime"));
        if (date && !isNaN(date)) {
            el.textContent = date.toLocaleString([], {dateStyle: "medium", timeStyle: "short"});
        }
    });
}

// Server time as seconds from job start, advanced by the browser's monotonic
// clock between polls so running timers tick without trusting the local clock.
const jobClock = {nowOffset: null, fetchedAt: 0, live: false};

function liveNowOffset() {
    if (jobClock.nowOffset == null) return null;
    const drift = jobClock.live ? (performance.now() - jobClock.fetchedAt) / 1000 : 0;
    return jobClock.nowOffset + drift;
}

function tickLiveDurations() {
    const now = liveNowOffset();
    if (now == null) return;
    document.querySelectorAll("[data-live-from]").forEach(el => {
        el.textContent = formatSecs(now - Number(el.dataset.liveFrom));
    });
}

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

function setHtml(id, html) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
}

function renderJobSummary(job) {
    const tl = job.timeline;
    const running = job.status === "running";
    const jobStart = parseUtc(job.started_at);
    const jobEnd = parseUtc(job.completed_at);

    if (tl.now_offset == null) {
        setText("job-elapsed", "—");
        setText("job-elapsed-note", "not started yet");
    } else if (running) {
        setHtml("job-elapsed", `<span data-live-from="0">${formatSecs(tl.now_offset)}</span>`);
        setText("job-elapsed-note", `started ${clockTime(jobStart)}`);
    } else {
        setText("job-elapsed", formatSecs(tl.now_offset));
        setText("job-elapsed-note", jobEnd
            ? `${clockTime(jobStart)} → ${clockTime(jobEnd)}`
            : `started ${clockTime(jobStart)}`);
    }

    const tasks = job.tasks || [];
    const done = tasks.filter(t => t.status === "completed").length;
    setText("job-progress", `${done}/${tasks.length}`);
    const hooks = tl.steps.filter(s => s.kind === "hook");
    const failed = tl.steps.filter(s => s.status === "failed").length;
    const hooksDone = hooks.filter(s => s.status === "completed").length;
    setText("job-progress-note", [
        hooks.length ? `${hooksDone}/${hooks.length} hooks passed` : "",
        failed ? `${failed} failed` : "",
    ].filter(Boolean).join(" · "));

    const current = tl.steps.filter(s => s.status === "running").pop();
    if (current) {
        setText("job-current", current.kind === "hook" ? `${current.name} (hook)` : current.name);
        document.getElementById("job-current")?.setAttribute("title", current.name);
        const est = current.estimate_secs ? ` of ${formatSecs(current.estimate_secs)} est.` : "";
        setHtml("job-current-note", `for <span data-live-from="${current.start_offset}">`
            + `${formatSecs(tl.now_offset - current.start_offset)}</span>${est}`);
    } else {
        setText("job-current", "—");
        setText("job-current-note", running ? "between steps" : job.status);
    }

    if (running && tl.now_offset != null) {
        const remaining = Math.max(0, tl.total_secs - tl.now_offset);
        setText("job-remaining-label", "Remaining (est.)");
        setText("job-remaining", `≈ ${formatSecs(remaining)}`);
        setText("job-remaining-note", `ETA ${clockTime(new Date(Date.now() + remaining * 1000))}`);
    } else {
        setText("job-remaining-label", "Planned");
        setText("job-remaining", formatSecs(tl.planned_secs));
        setText("job-remaining-note", tl.now_offset != null && tl.planned_secs
            ? `took ${Math.round((tl.now_offset / tl.planned_secs) * 100)}% of plan`
            : "sum of estimates");
    }
}

function renderTimelineRows(job) {
    const tbody = document.getElementById("timeline-rows");
    if (!tbody) return;
    const tl = job.timeline;
    if (!tl.steps.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="empty">No tasks</td></tr>';
        return;
    }
    const live = ["running", "pending"].includes(job.status);
    const nowOff = tl.now_offset;
    const pct = secs => `${Math.min(100, Math.max(0, (secs / tl.total_secs) * 100)).toFixed(2)}%`;
    const workers = Object.fromEntries((job.tasks || []).map(t => [t.id, t]));
    const attempts = {};
    tl.steps.forEach(s => {
        if (s.kind === "task" && s.task_id) attempts[s.task_id] = (attempts[s.task_id] || 0) + 1;
    });

    setText("timeline-scale", `0 → ${formatSecs(tl.total_secs)}${live && nowOff != null ? " projected" : ""}`);

    tbody.innerHTML = tl.steps.map(s => {
        const isHook = s.kind === "hook";
        const classes = ["timeline-row", `timeline-${s.kind}`, `timeline-${s.status}`];
        if (isHook && !s.task_id) classes.push("timeline-plan-hook");

        let step;
        if (isHook) {
            step = `<span class="timeline-name">${escapeHtml(s.name)}`
                + `<span class="timeline-sub">${HOOK_PHASES[s.phase] || s.phase}</span></span>`;
        } else {
            const task = workers[s.task_id];
            const worker = task && (task.assignee || task.harness) ? ` · ${escapeHtml(workerLabel(task))}` : "";
            const badge = attempts[s.task_id] > 1 && s.attempt ? `<span class="timeline-attempt">try ${s.attempt}</span>` : "";
            step = `<span class="timeline-name">${escapeHtml(s.name)}${badge}</span>`
                + `<span class="timeline-sub">${escapeHtml(s.task_id || "")}${worker}</span>`;
        }

        const reason = ["failed", "stopped"].includes(s.status) && s.exit_reason
            ? `<span class="timeline-sub">${escapeHtml(s.exit_reason)}</span>` : "";
        const status = `<span class="${statusClass(s.status)}">${s.status}</span>${reason}`;

        let duration = "—";
        if (s.status === "running" && s.start_offset != null && nowOff != null) {
            const elapsed = nowOff - s.start_offset;
            const over = s.estimate_secs && elapsed > s.estimate_secs ? " timeline-over" : "";
            duration = `<span class="timeline-live${over}" data-live-from="${s.start_offset}">${formatSecs(elapsed)}</span>`;
        } else if (s.duration != null) {
            const over = s.estimate_secs && s.duration > s.estimate_secs;
            duration = `<span class="${over ? "timeline-over" : ""}"`
                + `${over ? ` title="Over estimate by ${formatSecs(s.duration - s.estimate_secs)}"` : ""}>`
                + `${formatSecs(s.duration)}</span>`;
        }
        const estimate = s.estimate_secs ? formatSecs(s.estimate_secs) : "—";

        let track = '<div class="timeline-track">';
        if (s.bar) {
            if (s.bar.actual_end > s.bar.start) {
                track += `<span class="timeline-actual status-${s.status}" `
                    + `style="left:${pct(s.bar.start)};width:${pct(s.bar.actual_end - s.bar.start)}"></span>`;
            }
            if (live && s.bar.projected_end > s.bar.actual_end) {
                track += `<span class="timeline-projected" `
                    + `style="left:${pct(s.bar.actual_end)};width:${pct(s.bar.projected_end - s.bar.actual_end)}"></span>`;
            }
        }
        if (live && nowOff != null) track += `<span class="timeline-now" style="left:${pct(nowOff)}"></span>`;
        track += "</div>";

        return `<tr class="${classes.join(" ")}">
            <td class="timeline-step" data-label="Step">${step}</td>
            <td data-label="Status">${status}</td>
            <td data-label="Duration">${duration}</td>
            <td class="timeline-estimate" data-label="Estimate">${estimate}</td>
            <td data-label="Timeline">${track}</td>
        </tr>`;
    }).join("");
}

function startJobDetailPolling(jobId, intervalMs) {
    let intervalId = null;
    let tickId = null;
    let stopped = false;
    function stopPolling() {
        stopped = true;
        if (intervalId !== null) clearInterval(intervalId);
        if (tickId !== null) clearInterval(tickId);
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
        jobClock.nowOffset = job.timeline.now_offset;
        jobClock.fetchedAt = performance.now();
        jobClock.live = job.status === "running";
        renderJobSummary(job);
        renderTimelineRows(job);
        if (isTerminalStatus(job.status)) {
            stopPolling();
        }
    }
    document.addEventListener("visibilitychange", () => {
        if (!stopped && !document.hidden) refresh();
    });
    localizeTimes();
    refresh();
    intervalId = setInterval(refresh, intervalMs);
    tickId = setInterval(tickLiveDurations, 1000);
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
