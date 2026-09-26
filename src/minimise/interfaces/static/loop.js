function loopListRow(loop) {
    return `<tr>
        <td data-label="ID"><a href="/loops/${encodeURIComponent(loop.loop_id)}/view">${escapeHtml(loop.loop_id)}</a></td>
        <td data-label="Name">${escapeHtml(loop.name)}</td>
        <td data-label="Status"><span class="${statusClass(loop.status)}">${escapeHtml(loop.status)}</span></td>
        <td data-label="Iteration">${escapeHtml(`${loop.iteration}/${loop.max_iterations}`)}</td>
        <td data-label="Stage">${escapeHtml(loop.stage)}</td>
        <td data-label="Created">${escapeHtml(loop.created_at || "")}</td>
    </tr>`;
}

async function refreshLoopList() {
    const page = new URLSearchParams(location.search).get("page") || 1;
    const response = await fetch(`/api/loops?page=${encodeURIComponent(page)}`);
    if (!response.ok) return null;

    const loops = await response.json();
    const rows = document.getElementById("loop-rows");
    if (rows) {
        rows.innerHTML = loops.map(loopListRow).join("")
            || '<tr><td colspan="6" class="empty">No loops yet</td></tr>';
    }
    return loops;
}

function startLoopListPolling(intervalMs) {
    let intervalId = null;
    async function tick() {
        if (document.hidden) return;
        const loops = await refreshLoopList();
        if (loops && (loops.length === 0 || loops.every(loop => isTerminalStatus(loop.status)))) {
            clearInterval(intervalId);
        }
    }
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) tick();
    });
    tick();
    intervalId = setInterval(tick, intervalMs);
}

function loopStepRow(step) {
    return `<tr>
        <td data-label="Iteration">${escapeHtml(step.iteration)}</td>
        <td data-label="Stage">${escapeHtml(step.stage)}</td>
        <td data-label="Dimension">${escapeHtml(step.dimension || "-")}</td>
        <td data-label="Status"><span class="${statusClass(step.status)}">${escapeHtml(step.status)}</span></td>
        <td data-label="Retries">${escapeHtml(step.retries)}</td>
        <td data-label="Duration">${escapeHtml(step.duration)}</td>
    </tr>`;
}

async function refreshLoopDetail(loopId) {
    const response = await fetch(`/api/loops/${encodeURIComponent(loopId)}`);
    if (!response.ok) return null;

    const loop = await response.json();
    const status = document.getElementById("loop-status");
    if (status) {
        status.textContent = loop.status;
        status.className = statusClass(loop.status);
    }
    const values = {
        "loop-iteration": `${loop.iteration}/${loop.max_iterations}`,
        "loop-stage": loop.stage,
        "loop-plan-version": loop.plan_version ? `v${loop.plan_version}` : "-",
        "loop-elapsed": loop.elapsed,
    };
    Object.entries(values).forEach(([id, value]) => {
        const element = document.getElementById(id);
        if (element) element.textContent = value;
    });

    const rows = document.getElementById("loop-step-rows");
    if (rows) {
        rows.innerHTML = loop.steps.map(loopStepRow).join("")
            || '<tr><td colspan="6" class="empty">No loop steps yet</td></tr>';
    }
    return loop;
}

function startLoopDetailPolling(loopId, intervalMs) {
    let intervalId = null;
    async function tick() {
        if (document.hidden) return;
        const loop = await refreshLoopDetail(loopId);
        if (loop && isTerminalStatus(loop.status)) {
            clearInterval(intervalId);
        }
    }
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) tick();
    });
    tick();
    intervalId = setInterval(tick, intervalMs);
}

function loopJournalEntry(entry) {
    const iteration = entry.iteration === null ? "Loop" : `Iteration ${entry.iteration}`;
    return `<article class="loop-journal-entry">
        <header>
            <span class="loop-outcome loop-outcome-${escapeHtml(entry.tone)}">${escapeHtml(entry.outcome)}</span>
            <strong>${escapeHtml(entry.stage)}</strong>
            <span>${escapeHtml(iteration)}</span>
            <time>${escapeHtml(entry.timestamp)}</time>
        </header>
        <p>${escapeHtml(entry.message)}</p>
        <details class="loop-record-raw">
            <summary>Raw record</summary>
            <pre><code>${escapeHtml(entry.raw)}</code></pre>
        </details>
    </article>`;
}

async function refreshLoopJournal(loopId) {
    const response = await fetch(`/api/loops/${encodeURIComponent(loopId)}/journal?limit=100`);
    if (!response.ok) return;
    const data = await response.json();
    const entries = document.getElementById("loop-journal-entries");
    if (entries) {
        entries.innerHTML = data.records.map(loopJournalEntry).join("")
            || '<p class="empty">No journal entries yet</p>';
    }
}

function loopLogRow(entry) {
    const iteration = entry.iteration === null ? "-" : entry.iteration;
    return `<tr>
        <td data-label="Timestamp">${escapeHtml(entry.timestamp)}</td>
        <td data-label="Iteration">${escapeHtml(iteration)}</td>
        <td data-label="Stage">${escapeHtml(entry.stage)}</td>
        <td data-label="Level">${escapeHtml(entry.level)}</td>
        <td data-label="Message" class="loop-log-message">${escapeHtml(entry.message)}</td>
    </tr>`;
}

async function refreshLoopLogs(loopId) {
    const params = new URLSearchParams({limit: "100"});
    const filters = {
        iteration: document.getElementById("loop-log-iteration-filter")?.value,
        step_type: document.getElementById("loop-log-stage-filter")?.value,
        dimension: document.getElementById("loop-log-dimension-filter")?.value,
    };
    Object.entries(filters).forEach(([name, value]) => {
        if (value && value !== "all") params.set(name, value);
    });

    const response = await fetch(
        `/api/loops/${encodeURIComponent(loopId)}/logs?${params}`,
    );
    if (!response.ok) return;
    const data = await response.json();
    syncLoopLogFilterOptions(data.filter_options);

    const rows = document.getElementById("loop-log-rows");
    if (rows) {
        rows.innerHTML = data.records.map(loopLogRow).join("")
            || '<tr><td colspan="5" class="empty">No logs yet</td></tr>';
    }
}

function syncLoopLogSelect(selectId, values, label) {
    const select = document.getElementById(selectId);
    if (!select) return;

    const selected = select.value;
    const defaultOption = select.options[0];
    const options = values.map(value => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label(value);
        return option;
    });
    select.replaceChildren(defaultOption, ...options);
    if (values.some(value => String(value) === selected)) {
        select.value = selected;
    }
}

function syncLoopLogFilterOptions(options) {
    if (!options) return;
    syncLoopLogSelect(
        "loop-log-iteration-filter",
        options.iterations || [],
        iteration => `Iteration ${iteration}`,
    );
    syncLoopLogSelect(
        "loop-log-dimension-filter",
        options.dimensions || [],
        dimension => dimension,
    );
}

function refreshLoopLogsNow() {
    const loopId = document.querySelector("[data-loop-id]")?.dataset.loopId;
    if (loopId) refreshLoopLogs(loopId);
}

function initLoopLogFilters() {
    const iteration = document.getElementById("loop-log-iteration-filter");
    const stage = document.getElementById("loop-log-stage-filter");
    const dimension = document.getElementById("loop-log-dimension-filter");

    iteration?.addEventListener("change", refreshLoopLogsNow);
    stage?.addEventListener("change", () => {
        if (stage.value !== "evaluate" && dimension) {
            dimension.value = "all";
        }
        refreshLoopLogsNow();
    });
    dimension?.addEventListener("change", () => {
        if (dimension.value !== "all" && stage) {
            stage.value = "evaluate";
        }
        refreshLoopLogsNow();
    });
}

function startLoopArtifactPolling(loopId, intervalMs) {
    let intervalId = null;
    async function tick() {
        if (document.hidden) return;
        const status = document.getElementById("loop-status");
        if (status && isTerminalStatus(status.textContent.trim())) {
            clearInterval(intervalId);
            return;
        }
        const active = document.querySelector(
            ".loop-page [data-detail-panel]:not([hidden])",
        );
        if (active?.dataset.detailPanel === "journal") await refreshLoopJournal(loopId);
        if (active?.dataset.detailPanel === "logs") await refreshLoopLogs(loopId);
    }
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) tick();
    });
    intervalId = setInterval(tick, intervalMs);
}
