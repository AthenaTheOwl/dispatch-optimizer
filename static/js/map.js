/* Map rendering with Leaflet — facilities, drivers, orders, routes */

let map;
let facilityMarkers = [];
let driverMarkers = [];
let orderMarkers = [];
let greedyRouteLines = [];
let optimalRouteLines = [];
let currentRouteDisplay = 'both';

// Marker icon factories
function facilityIcon(type) {
    const colors = {
        hub: '#ff5630',
        branch: '#36b37e',
        destination: '#4c9aff',
        satellite: '#6554c0',
    };
    const symbols = {
        hub: 'H',
        branch: 'B',
        destination: 'D',
        satellite: 'S',
    };
    const color = colors[type] || '#6b778c';
    const symbol = symbols[type] || '?';

    return L.divIcon({
        className: '',
        html: `<div style="
            width: 24px; height: 24px; border-radius: 50%;
            background: ${color}; color: white;
            display: flex; align-items: center; justify-content: center;
            font-size: 12px; font-weight: 700;
            border: 2px solid rgba(255,255,255,0.8);
            box-shadow: 0 2px 6px rgba(0,0,0,0.3);
        ">${symbol}</div>`,
        iconSize: [24, 24],
        iconAnchor: [12, 12],
    });
}

function driverIcon(status, coldStorage) {
    const statusColors = {
        available: '#36b37e',
        en_route: '#ffab00',
        at_pickup: '#4c9aff',
        at_delivery: '#6554c0',
        offline: '#6b778c',
    };
    const coldBadge = {
        none: '',
        cooler: '*',
        active_fridge: '**',
        cryo: '***',
    };
    const color = statusColors[status] || '#6b778c';
    const badge = coldBadge[coldStorage] || '';

    return L.divIcon({
        className: '',
        html: `<div style="
            width: 28px; height: 28px; border-radius: 6px;
            background: ${color}; color: white;
            display: flex; align-items: center; justify-content: center;
            font-size: 14px; font-weight: 700;
            border: 2px solid rgba(255,255,255,0.9);
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
            position: relative;
        ">D${badge ? `<span style="position:absolute;top:-6px;right:-4px;font-size:8px;color:${color};">${badge}</span>` : ''}</div>`,
        iconSize: [28, 28],
        iconAnchor: [14, 14],
    });
}

function orderIcon(urgency) {
    const colors = {
        stat: '#ff5630',
        urgent: '#ffab00',
        routine: '#4c9aff',
        standard: '#6b778c',
    };
    const color = colors[urgency] || '#6b778c';

    return L.divIcon({
        className: '',
        html: `<div style="
            width: 14px; height: 14px; border-radius: 50%;
            background: ${color};
            border: 2px solid rgba(255,255,255,0.8);
            box-shadow: 0 1px 4px rgba(0,0,0,0.3);
            ${urgency === 'stat' ? 'animation: pulse 1s infinite;' : ''}
        "></div>
        <style>
            @keyframes pulse {
                0%, 100% { transform: scale(1); opacity: 1; }
                50% { transform: scale(1.3); opacity: 0.7; }
            }
        </style>`,
        iconSize: [14, 14],
        iconAnchor: [7, 7],
    });
}


function initMap() {
    map = L.map('map', {
        zoomControl: true,
        attributionControl: true,
    }).setView([40.7580, -73.9555], 12);

    // Dark tile layer
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
        subdomains: 'abcd',
        maxZoom: 19,
    }).addTo(map);
}


function clearMap() {
    facilityMarkers.forEach(m => map.removeLayer(m));
    driverMarkers.forEach(m => map.removeLayer(m));
    orderMarkers.forEach(m => map.removeLayer(m));
    clearRoutes();
    facilityMarkers = [];
    driverMarkers = [];
    orderMarkers = [];
}


function clearRoutes() {
    greedyRouteLines.forEach(l => map.removeLayer(l));
    optimalRouteLines.forEach(l => map.removeLayer(l));
    greedyRouteLines = [];
    optimalRouteLines = [];
}


function plotFacilities(facilities) {
    facilities.forEach(f => {
        const marker = L.marker([f.lat, f.lng], {
            icon: facilityIcon(f.type),
        }).addTo(map);

        marker.bindPopup(`
            <strong>${f.name}</strong><br>
            Type: ${f.type}
        `);

        facilityMarkers.push(marker);
    });
}


function plotDrivers(drivers) {
    drivers.forEach(d => {
        const marker = L.marker([d.lat, d.lng], {
            icon: driverIcon(d.status, d.cold_storage),
        }).addTo(map);

        marker.bindPopup(`
            <strong>${d.name}</strong> (${d.id})<br>
            Vehicle: ${d.vehicle_type} | Cold: ${d.cold_storage}<br>
            Certs: ${d.certifications.join(', ')}<br>
            Status: ${d.status} | Load: ${d.current_load}/${d.capacity}
        `);

        driverMarkers.push(marker);
    });
}


function plotOrders(orders) {
    orders.forEach(o => {
        // Pickup marker
        const marker = L.marker([o.pickup_lat, o.pickup_lng], {
            icon: orderIcon(o.urgency),
        }).addTo(map);

        const pkgList = o.packages.map(p =>
            `${p.cargo_type} → ${p.destination_name} (${p.temp_regime})`
        ).join('<br>');

        marker.bindPopup(`
            <strong>${o.id}</strong> — ${o.pickup_name}<br>
            <span class="urgency-badge urgency-${o.urgency}" style="
                padding: 2px 6px; border-radius: 3px; font-size: 10px;
                font-weight: 600; text-transform: uppercase;
                background: ${o.urgency === 'stat' ? '#ff5630' :
                            o.urgency === 'urgent' ? '#ffab00' :
                            o.urgency === 'routine' ? '#4c9aff' : '#6b778c'};
                color: ${o.urgency === 'urgent' ? '#1a1d27' : 'white'};
            ">${o.urgency}</span>
            ${o.chain_of_custody ? '| Chain of Custody' : ''}<br>
            <strong>${o.num_packages} package(s):</strong><br>
            <span style="font-size: 11px;">${pkgList}</span><br>
            Deadline: ${new Date(o.tightest_deadline).toLocaleTimeString()}
        `);

        orderMarkers.push(marker);
    });
}


function plotRoutes(assignments, type) {
    const isGreedy = type === 'greedy';
    const color = isGreedy ? '#ff5630' : '#36b37e';
    const dashArray = isGreedy ? '8, 8' : null;
    const weight = isGreedy ? 2 : 3;
    const opacity = 0.7;

    assignments.forEach(a => {
        // Find the driver's starting position
        const driver = window._currentScenario?.drivers.find(d => d.id === a.driver_id);
        if (!driver) return;

        const points = [[driver.lat, driver.lng]];

        a.route.stops.forEach(stop => {
            points.push([stop.lat, stop.lng]);
        });

        const line = L.polyline(points, {
            color: color,
            weight: weight,
            opacity: opacity,
            dashArray: dashArray,
        }).addTo(map);

        // Add numbered stop markers
        a.route.stops.forEach((stop, idx) => {
            const stopMarker = L.circleMarker([stop.lat, stop.lng], {
                radius: 6,
                fillColor: stop.type === 'pickup' ? '#4c9aff' : '#36b37e',
                color: 'white',
                weight: 1,
                fillOpacity: 0.9,
            }).addTo(map);

            stopMarker.bindTooltip(`${idx + 1}: ${stop.type} — ${stop.name}`, {
                permanent: false,
                direction: 'top',
            });

            if (isGreedy) {
                greedyRouteLines.push(stopMarker);
            } else {
                optimalRouteLines.push(stopMarker);
            }
        });

        // Click to show route detail
        line.on('click', () => showRouteDetail(a, driver, type));

        if (isGreedy) {
            greedyRouteLines.push(line);
        } else {
            optimalRouteLines.push(line);
        }
    });
}


function setRouteDisplay(mode) {
    currentRouteDisplay = mode;

    // Update toggle UI
    document.querySelectorAll('.toggle-option').forEach(el => {
        el.classList.toggle('active', el.dataset.mode === mode);
    });

    // Show/hide route layers
    greedyRouteLines.forEach(l => {
        if (mode === 'greedy' || mode === 'both') {
            map.addLayer(l);
        } else {
            map.removeLayer(l);
        }
    });

    optimalRouteLines.forEach(l => {
        if (mode === 'optimal' || mode === 'both') {
            map.addLayer(l);
        } else {
            map.removeLayer(l);
        }
    });
}


function showRouteDetail(assignment, driver, type) {
    const detail = document.getElementById('route-detail');
    const title = document.getElementById('detail-title');
    const content = document.getElementById('detail-content');

    const typeName = type === 'greedy' ? 'Greedy' : 'Optimal';
    title.textContent = `${typeName}: ${driver.name} → ${assignment.order_id}`;

    let html = `
        <div style="font-size: 12px; margin-bottom: 8px; color: var(--text-secondary);">
            Distance: ${assignment.distance_km} km | Time: ${assignment.total_time_min} min |
            Score: ${assignment.cost_score}
        </div>
        <ul class="stop-list">
    `;

    assignment.route.stops.forEach((stop, idx) => {
        html += `
            <li class="${stop.type}">
                <strong>${idx + 1}.</strong> ${stop.type.toUpperCase()} — ${stop.name}
                ${stop.package_ids.length > 0 ? `<br><span style="color: var(--text-secondary); font-size: 11px;">${stop.package_ids.join(', ')}</span>` : ''}
            </li>
        `;
    });

    html += '</ul>';

    // Cost breakdown
    if (assignment.cost_breakdown && Object.keys(assignment.cost_breakdown).length > 0) {
        html += '<div style="margin-top: 10px; font-size: 11px;"><strong>Cost Breakdown:</strong>';
        for (const [key, val] of Object.entries(assignment.cost_breakdown)) {
            if (key !== 'total' && key !== 'violations') {
                html += `<br>${key}: ${val}`;
            }
        }
        html += '</div>';
    }

    content.innerHTML = html;
    detail.classList.add('visible');
}


// Click outside to close detail
document.addEventListener('click', (e) => {
    const detail = document.getElementById('route-detail');
    if (!detail.contains(e.target) && detail.classList.contains('visible')) {
        detail.classList.remove('visible');
    }
});
