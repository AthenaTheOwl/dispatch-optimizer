/* Metrics display: comparison table, KPI cards */


function renderComparisonMetrics(data) {
    const section = document.getElementById('metrics-section');
    const tbody = document.getElementById('metrics-body');
    section.style.display = 'block';
    tbody.innerHTML = '';

    const gm = data.greedy.metrics;
    const hm = data.hungarian.metrics;

    const rows = [
        { label: 'Total Distance', key: 'total_distance_km', unit: ' km', decimals: 1 },
        { label: 'Total Time', key: 'total_time_minutes', unit: ' min', decimals: 0 },
        { label: 'Avg Distance/Order', key: 'avg_distance_per_order_km', unit: ' km', decimals: 2 },
        { label: 'Avg Pickup Wait', key: 'avg_pickup_wait_min', unit: ' min', decimals: 1 },
        { label: 'Max Pickup Wait', key: 'max_pickup_wait_min', unit: ' min', decimals: 1 },
        { label: 'Deadline Compliance', key: 'deadline_compliance_rate', unit: '%', decimals: 0 },
        { label: 'Drivers Used', key: 'drivers_used', unit: '', decimals: 0 },
        { label: 'Overqualified Assigns', key: 'overqualified_assignments', unit: '', decimals: 0 },
        { label: 'Cost per Package', key: 'cost_per_package_km', unit: ' km', decimals: 2 },
    ];

    rows.forEach(row => {
        const gVal = gm[row.key];
        const hVal = hm[row.key];
        const delta = data.deltas[row.key];

        const tr = document.createElement('tr');

        // Determine if lower or higher is better
        const lowerIsBetter = !['deadline_compliance_rate', 'orders_assigned'].includes(row.key);
        const isImprovement = lowerIsBetter ? (delta < 0) : (delta > 0);

        tr.innerHTML = `
            <td>${row.label}</td>
            <td class="col-greedy">${formatNum(gVal, row.decimals)}${row.unit}</td>
            <td class="col-optimal">${formatNum(hVal, row.decimals)}${row.unit}</td>
            <td class="col-delta" style="color: ${isImprovement ? 'var(--accent-green)' : delta === 0 ? 'var(--text-secondary)' : 'var(--accent-red)'};">
                ${delta > 0 ? '+' : ''}${formatNum(delta, 1)}%
            </td>
        `;

        tbody.appendChild(tr);
    });

    // Add urgency-specific wait times if available
    ['stat', 'urgent', 'routine'].forEach(urgency => {
        const key = `avg_pickup_wait_${urgency}_min`;
        if (gm[key] !== undefined || hm[key] !== undefined) {
            const gVal = gm[key] || 0;
            const hVal = hm[key] || 0;
            const delta = gVal !== 0 ? ((hVal - gVal) / gVal * 100) : 0;

            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td style="padding-left: 20px; font-size: 11px;">↳ ${urgency.toUpperCase()} wait</td>
                <td class="col-greedy">${formatNum(gVal, 1)} min</td>
                <td class="col-optimal">${formatNum(hVal, 1)} min</td>
                <td class="col-delta" style="color: ${delta < 0 ? 'var(--accent-green)' : 'var(--text-secondary)'};">
                    ${delta > 0 ? '+' : ''}${formatNum(delta, 1)}%
                </td>
            `;
            tbody.appendChild(tr);
        }
    });
}


function renderKPIs(data) {
    const section = document.getElementById('kpi-section');
    const grid = document.getElementById('kpi-grid');
    section.style.display = 'block';
    grid.innerHTML = '';

    const gm = data.greedy.metrics;
    const hm = data.hungarian.metrics;

    const kpis = [
        {
            label: 'Distance Saved',
            value: `${formatNum(gm.total_distance_km - hm.total_distance_km, 1)} km`,
            delta: data.deltas.total_distance_km,
            lowerBetter: true,
        },
        {
            label: 'Wait Time Saved',
            value: `${formatNum(gm.avg_pickup_wait_min - hm.avg_pickup_wait_min, 1)} min`,
            delta: data.deltas.avg_pickup_wait_min,
            lowerBetter: true,
        },
        {
            label: 'Deadline Rate',
            value: `${formatNum(hm.deadline_compliance_rate, 0)}%`,
            delta: data.deltas.deadline_compliance_rate,
            lowerBetter: false,
        },
        {
            label: 'Fewer Drivers',
            value: `${gm.drivers_used - hm.drivers_used}`,
            delta: data.deltas.drivers_used,
            lowerBetter: true,
        },
        {
            label: 'Cost/Package',
            value: `${formatNum(hm.cost_per_package_km, 2)} km`,
            delta: data.deltas.cost_per_package_km,
            lowerBetter: true,
        },
        {
            label: 'Packages Delivered',
            value: `${hm.total_packages_delivered}`,
            delta: 0,
            lowerBetter: false,
        },
    ];

    // Add pooling stats if available
    if (data.pooling) {
        kpis.push({
            label: 'Poolable Orders',
            value: `${data.pooling.orders_poolable}`,
            delta: null,
            lowerBetter: false,
        });
        kpis.push({
            label: 'Pool Groups',
            value: `${data.pooling.multi_order_pools}`,
            delta: null,
            lowerBetter: false,
        });
    }

    kpis.forEach(kpi => {
        const card = document.createElement('div');
        card.className = 'metric-card';

        let deltaHtml = '';
        if (kpi.delta !== null && kpi.delta !== undefined && kpi.delta !== 0) {
            const isGood = kpi.lowerBetter ? (kpi.delta < 0) : (kpi.delta > 0);
            deltaHtml = `<div class="delta ${isGood ? 'negative' : 'positive'}">
                ${kpi.delta > 0 ? '+' : ''}${formatNum(kpi.delta, 1)}% vs greedy
            </div>`;
        }

        card.innerHTML = `
            <div class="label">${kpi.label}</div>
            <div class="value">${kpi.value}</div>
            ${deltaHtml}
        `;

        grid.appendChild(card);
    });
}


function formatNum(val, decimals) {
    if (val === null || val === undefined) return '-';
    if (typeof val !== 'number') return val;
    return val.toFixed(decimals);
}
