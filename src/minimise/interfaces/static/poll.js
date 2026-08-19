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
