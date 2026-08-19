function statusClass(status) {
    return "status status-" + status;
}

function startJobListPolling(intervalMs) {
    async function refresh() {
        const resp = await fetch("/jobs");
        if (!resp.ok) return;
        const jobs = await resp.json();
        const tbody = document.getElementById("job-rows");
        if (!tbody) return;
        if (jobs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="empty">No jobs yet</td></tr>';
            return;
        }
        tbody.innerHTML = jobs.map(job => {
            const done = (job.tasks || []).filter(t => t.status === "completed").length;
            const total = (job.tasks || []).length;
            return `<tr>
                <td><a href="/jobs/${job.id}/view">${job.id}</a></td>
                <td>${job.name}</td>
                <td><span class="${statusClass(job.status)}">${job.status}</span></td>
                <td>${done}/${total}</td>
                <td>${job.created_at || ""}</td>
            </tr>`;
        }).join("");
    }
    refresh();
    setInterval(refresh, intervalMs);
}

function startJobDetailPolling(jobId, intervalMs) {
    async function refresh() {
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
            tbody.innerHTML = (job.tasks || []).map(t => `<tr>
                <td>${t.id}</td><td>${t.name}</td>
                <td><span class="${statusClass(t.status)}">${t.status}</span></td>
                <td>${t.retries}</td>
            </tr>`).join("") || '<tr><td colspan="4" class="empty">No tasks</td></tr>';
        }
    }
    refresh();
    setInterval(refresh, intervalMs);
}

function togglePlanView() {
    const structured = document.getElementById("plan-structured");
    const raw = document.getElementById("plan-raw");
    const isRaw = raw.style.display !== "none";
    raw.style.display = isRaw ? "none" : "block";
    structured.style.display = isRaw ? "block" : "none";
}

async function loadPlan(jobId) {
    const resp = await fetch(`/jobs/${jobId}/plan`);
    if (!resp.ok) return;
    const data = await resp.json();
    document.getElementById("plan-raw").textContent = data.raw_yaml;
    const list = document.getElementById("plan-tasks");
    list.innerHTML = data.plan.tasks.map(t => {
        const worker = t.assignee ? `persona: ${t.assignee}` : (t.harness ? `${t.harness}/${t.model || "default"}` : "default");
        return `<li><strong>${t.id}</strong> — ${t.name} (${worker})</li>`;
    }).join("");
}
