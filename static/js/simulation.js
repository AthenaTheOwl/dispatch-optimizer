/* Scenario generation, dispatch execution, and state management */

// Global state
window._currentScenario = null;
window._greedyResult = null;
window._hungarianResult = null;
window._comparisonData = null;


async function apiCall(endpoint, method = 'POST', body = null) {
    const loading = document.getElementById('loading');
    loading.classList.add('visible');

    try {
        const options = { method };
        if (body) {
            options.headers = { 'Content-Type': 'application/json' };
            options.body = JSON.stringify(body);
        }

        const resp = await fetch(endpoint, options);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return await resp.json();
    } finally {
        loading.classList.remove('visible');
    }
}


async function generateScenario() {
    const config = {
        num_drivers: parseInt(document.getElementById('num-drivers').value),
        num_orders: parseInt(document.getElementById('num-orders').value),
        seed: parseInt(document.getElementById('seed').value),
        include_field_collection: document.getElementById('include-field-col').checked,
        num_home_visits: 5,
    };

    const data = await apiCall('/api/scenario/generate', 'POST', config);

    if (data.error) {
        alert(data.error);
        return;
    }

    window._currentScenario = data;
    window._greedyResult = null;
    window._hungarianResult = null;
    window._comparisonData = null;

    // Clear and redraw map
    clearMap();
    plotFacilities(data.facilities);
    plotDrivers(data.drivers);
    plotOrders(data.orders);

    // Enable dispatch buttons
    document.getElementById('btn-greedy').disabled = false;
    document.getElementById('btn-hungarian').disabled = false;
    document.getElementById('btn-compare').disabled = false;

    // Update status
    const totalPkgs = data.orders.reduce((sum, o) => sum + o.num_packages, 0);
    document.getElementById('status-text').textContent =
        `${data.drivers.length} drivers | ${data.orders.length} orders | ${totalPkgs} packages`;

    // Show order list
    renderOrderList(data.orders);

    // Hide metrics until comparison runs
    document.getElementById('metrics-section').style.display = 'none';
    document.getElementById('kpi-section').style.display = 'none';
}


async function runGreedy() {
    const data = await apiCall('/api/dispatch/greedy', 'POST');
    if (data.error) { alert(data.error); return; }

    window._greedyResult = data;

    clearRoutes();
    plotRoutes(data.assignments, 'greedy');
    setRouteDisplay('greedy');

    document.getElementById('status-text').textContent =
        `Greedy: ${data.assignments.length} assigned | ${data.metrics.total_distance_km} km total`;
}


async function runHungarian() {
    const data = await apiCall('/api/dispatch/hungarian', 'POST');
    if (data.error) { alert(data.error); return; }

    window._hungarianResult = data;

    clearRoutes();
    plotRoutes(data.assignments, 'optimal');
    setRouteDisplay('optimal');

    document.getElementById('status-text').textContent =
        `Optimal: ${data.assignments.length} assigned | ${data.metrics.total_distance_km} km total`;
}


async function runComparison() {
    const data = await apiCall('/api/dispatch/compare', 'POST');
    if (data.error) { alert(data.error); return; }

    window._greedyResult = data.greedy;
    window._hungarianResult = data.hungarian;
    window._comparisonData = data;

    // Plot both route sets
    clearRoutes();
    plotRoutes(data.greedy.assignments, 'greedy');
    plotRoutes(data.hungarian.assignments, 'optimal');
    setRouteDisplay('both');

    // Show metrics
    renderComparisonMetrics(data);
    renderKPIs(data);

    const distDelta = data.deltas.total_distance_km;
    document.getElementById('status-text').textContent =
        `Comparison complete | Distance: ${distDelta > 0 ? '+' : ''}${distDelta}% | ` +
        `Greedy: ${data.greedy.metrics.total_distance_km} km | Optimal: ${data.hungarian.metrics.total_distance_km} km`;
}


async function loadPresets() {
    const data = await apiCall('/api/scenario/presets', 'GET');
    const container = document.getElementById('preset-list');
    container.innerHTML = '';

    data.presets.forEach(preset => {
        const btn = document.createElement('button');
        btn.className = 'preset-btn';
        btn.innerHTML = `${preset.name}<small>${preset.description}</small>`;
        btn.onclick = () => {
            document.getElementById('num-drivers').value = preset.config.num_drivers;
            document.getElementById('num-orders').value = preset.config.num_orders;
            document.getElementById('seed').value = preset.config.seed || 42;
            document.getElementById('include-field-col').checked = preset.config.include_field_collection || false;
            generateScenario();
        };
        container.appendChild(btn);
    });
}


function renderOrderList(orders) {
    const container = document.getElementById('order-list');
    const countEl = document.getElementById('order-count');
    const section = document.getElementById('orders-section');

    section.style.display = 'block';
    countEl.textContent = orders.length;
    container.innerHTML = '';

    // Sort by urgency
    const urgencyOrder = { stat: 0, urgent: 1, routine: 2, standard: 3 };
    const sorted = [...orders].sort((a, b) =>
        (urgencyOrder[a.urgency] || 99) - (urgencyOrder[b.urgency] || 99)
    );

    sorted.forEach(o => {
        const item = document.createElement('div');
        item.className = 'order-item';
        item.innerHTML = `
            <span class="urgency-badge urgency-${o.urgency}">${o.urgency}</span>
            <span style="flex: 1; font-size: 11px;">
                ${o.id} — ${o.packages[0]?.cargo_type || '?'}
                ${o.num_packages > 1 ? `+${o.num_packages - 1} more` : ''}
            </span>
            <span style="font-size: 11px; color: var(--text-secondary);">
                → ${o.packages.length} dest${o.packages.length > 1 ? 's' : ''}
            </span>
        `;

        // Click to zoom to order on map
        item.onclick = () => {
            map.setView([o.pickup_lat, o.pickup_lng], 15);
        };

        container.appendChild(item);
    });
}
