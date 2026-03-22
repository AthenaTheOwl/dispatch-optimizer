/* Analysis tab: detailed logs of why greedy is worse than optimal */

async function runAnalysis() {
    if (!window._currentScenario) {
        alert('Generate a scenario first (on the Map tab).');
        return;
    }

    const btn = document.getElementById('btn-analysis');
    btn.textContent = 'Running...';
    btn.disabled = true;

    try {
        const data = await apiCall('/api/dispatch/analysis', 'POST');
        if (data.error) { alert(data.error); return; }

        renderAnalysisSummary(data.summary);
        renderRootCauses(data.summary.root_causes);
        renderAssignmentLogs(data.assignments);
        renderDriverLogs(data.drivers);
    } finally {
        btn.textContent = 'Run Analysis';
        btn.disabled = false;
    }
}


function renderAnalysisSummary(summary) {
    const el = document.getElementById('analysis-summary');
    el.style.display = 'block';

    el.innerHTML = `
        <div class="analysis-card">
            <h3>Summary</h3>
            <div class="summary-grid">
                <div class="summary-card">
                    <div class="label">Distance Wasted</div>
                    <div class="value red">${summary.distance_wasted_km} km</div>
                </div>
                <div class="summary-card">
                    <div class="label">Distance Improvement</div>
                    <div class="value green">${summary.distance_pct_improvement}%</div>
                </div>
                <div class="summary-card">
                    <div class="label">Wait Time Saved</div>
                    <div class="value green">${summary.wait_time_saved_min} min/order</div>
                </div>
                <div class="summary-card">
                    <div class="label">Deadline Rate</div>
                    <div class="value">
                        <span class="red">${summary.greedy_deadline_pct}%</span>
                        <span style="color: var(--text-secondary); font-size: 14px;"> &rarr; </span>
                        <span class="green">${summary.optimal_deadline_pct}%</span>
                    </div>
                </div>
                <div class="summary-card">
                    <div class="label">Overqualified Assignments</div>
                    <div class="value">
                        <span class="red">${summary.greedy_overqualified}</span>
                        <span style="color: var(--text-secondary); font-size: 14px;"> &rarr; </span>
                        <span class="green">${summary.optimal_overqualified}</span>
                    </div>
                </div>
                <div class="summary-card">
                    <div class="label">Orders With Problems</div>
                    <div class="value red">${summary.problems_found}</div>
                </div>
                <div class="summary-card">
                    <div class="label">Greedy Unassigned</div>
                    <div class="value red">${summary.greedy_unassigned}</div>
                </div>
                <div class="summary-card">
                    <div class="label">Cost per Package</div>
                    <div class="value">
                        <span class="red">${summary.greedy_cost_per_pkg} km</span>
                        <span style="color: var(--text-secondary); font-size: 14px;"> &rarr; </span>
                        <span class="green">${summary.optimal_cost_per_pkg} km</span>
                    </div>
                </div>
            </div>
        </div>
    `;
}


function renderRootCauses(causes) {
    const el = document.getElementById('analysis-root-causes');
    el.style.display = 'block';

    el.innerHTML = `
        <div class="analysis-card">
            <h3>Root Causes: Why Greedy Fails</h3>
            <ul class="root-cause-list">
                ${causes.map(c => `<li>${c}</li>`).join('')}
            </ul>
        </div>
    `;
}


function renderAssignmentLogs(assignments) {
    const el = document.getElementById('analysis-assignments');
    el.style.display = 'block';

    // Sort: problems first, then by order ID
    const sorted = [...assignments].sort((a, b) => {
        if (a.problems.length !== b.problems.length) return b.problems.length - a.problems.length;
        return a.order_id.localeCompare(b.order_id);
    });

    let html = '<div class="analysis-card"><h3>Order-by-Order Comparison</h3>';

    sorted.forEach(a => {
        const urgencyClass = `urgency-${a.urgency}`;
        const pkgStr = a.packages.map(p =>
            `${p.cargo} (${p.temp}) &rarr; ${p.destination}`
        ).join('<br>');

        html += `<div class="analysis-order">`;
        html += `<div class="order-header">
            <span class="urgency-badge ${urgencyClass}">${a.urgency}</span>
            <span>${a.order_id}</span>
            <span style="color: var(--text-secondary); font-weight: 400;">${a.pickup}</span>
            <span style="color: var(--text-secondary); font-weight: 400; font-size: 11px;">
                Deadline: ${a.deadline_min.toFixed(0)} min
            </span>
        </div>`;

        html += `<div class="packages">${pkgStr}</div>`;

        // Comparison rows
        html += `<div class="comparison-row">
            <div class="label-col"></div>
            <div style="color: var(--accent-red); font-weight: 600; font-size: 11px;">GREEDY</div>
            <div style="color: var(--accent-green); font-weight: 600; font-size: 11px;">OPTIMAL</div>
        </div>`;

        if (a.greedy && a.optimal) {
            html += `<div class="comparison-row">
                <div class="label-col">Driver</div>
                <div>${a.greedy.driver_name} (${a.greedy.cold_storage})</div>
                <div>${a.optimal.driver_name} (${a.optimal.cold_storage})</div>
            </div>`;
            html += `<div class="comparison-row">
                <div class="label-col">To Pickup</div>
                <div>${a.greedy.dist_to_pickup_km} km</div>
                <div>${a.optimal.dist_to_pickup_km} km</div>
            </div>`;
            html += `<div class="comparison-row">
                <div class="label-col">Total Dist</div>
                <div>${a.greedy.total_distance_km} km</div>
                <div>${a.optimal.total_distance_km} km</div>
            </div>`;
            html += `<div class="comparison-row">
                <div class="label-col">Total Time</div>
                <div>${a.greedy.total_time_min} min</div>
                <div>${a.optimal.total_time_min} min</div>
            </div>`;
        } else if (a.greedy && !a.optimal) {
            html += `<div class="comparison-row">
                <div class="label-col">Driver</div>
                <div>${a.greedy.driver_name} (${a.greedy.cold_storage})</div>
                <div style="color: var(--text-secondary);">Not assigned</div>
            </div>`;
        } else if (!a.greedy && a.optimal) {
            html += `<div class="comparison-row">
                <div class="label-col">Driver</div>
                <div style="color: var(--accent-red);">UNASSIGNED</div>
                <div>${a.optimal.driver_name} (${a.optimal.cold_storage})</div>
            </div>`;
        } else {
            html += `<div style="color: var(--text-secondary); font-size: 12px; padding: 4px 0;">
                Neither algorithm assigned this order (no eligible driver).
            </div>`;
        }

        // Problems
        a.problems.forEach(p => {
            html += `<div class="problem ${p.severity}">${p.message}</div>`;
        });

        html += `</div>`;
    });

    html += '</div>';
    el.innerHTML = html;
}


function renderDriverLogs(drivers) {
    const el = document.getElementById('analysis-drivers');
    el.style.display = 'block';

    let html = `<div class="analysis-card">
        <h3>Driver Utilization</h3>
        <table class="driver-table">
            <thead>
                <tr>
                    <th>Driver</th>
                    <th>Cold Storage</th>
                    <th>Greedy Orders</th>
                    <th>Greedy Distance</th>
                    <th>Optimal Orders</th>
                    <th>Optimal Distance</th>
                    <th>Notes</th>
                </tr>
            </thead>
            <tbody>`;

    drivers.forEach(d => {
        const hasNotes = d.notes.length > 0;
        const noteClass = d.notes.some(n => n.includes('wasted')) ? 'bad' :
                          d.notes.some(n => n.includes('found useful')) ? 'good' : '';

        html += `<tr>
            <td><strong>${d.name}</strong> <span style="color: var(--text-secondary);">(${d.vehicle})</span></td>
            <td>${d.cold_storage}</td>
            <td>${d.greedy_orders}</td>
            <td>${d.greedy_distance_km} km</td>
            <td>${d.optimal_orders}</td>
            <td>${d.optimal_distance_km} km</td>
            <td class="note ${noteClass}">${d.notes.join('; ') || '-'}</td>
        </tr>`;
    });

    html += '</tbody></table></div>';
    el.innerHTML = html;
}
