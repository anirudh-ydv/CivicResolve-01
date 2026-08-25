'use strict';

// ============================================================
// CivicResolve Admin Dashboard — vanilla JS (no build step, no framework)
// Consolidated single-file version, matching the style of the citizen
// portal's app.js. Sections, in order:
//   1. Config              (API_BASE_URL)
//   2. Auth / token mgmt    (login, logout, getStoredToken, apiFetch)
//   3. Reports data/polling (fetchReportsData, changeReportStatus, etc.)
//   4. Map (Leaflet)        (initMap, updateMapMarkers, renderMapLegend)
//   5. Reports table        (renderReportsTable + inline review actions)
//   6. App orchestration    (login flow, filters, wiring it all together)
// ============================================================

// --- 1. Config ---
// No Vite dev-proxy anymore, so this calls the backend directly. Keep
// serving this folder on port 5174 (e.g. `python -m http.server 5174`,
// or the nginx service in docker-compose.yml) since that's the origin
// already allowlisted in backend/app/main.py's CORS config.
const API_BASE_URL = 'http://localhost:8000';


/* ============================================================
   Token management (equivalent to hooks/useApi.js)
   ============================================================ */
const TOKEN_KEY = 'civicresolve_admin_token';

function getStoredToken() {
  return localStorage.getItem(TOKEN_KEY);
}

function setStoredToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}

function clearStoredToken() {
  localStorage.removeItem(TOKEN_KEY);
}

/* ============================================================
   Core fetch wrapper - equivalent to axios's request/response
   interceptors: attaches the Bearer token to every call, and
   clears the token on any 401 the same way the original
   interceptor did (see the comment there about why no hard
   navigation happens here either - App.jsx's auth-check logic,
   reproduced in app.js, already reacts to the token being gone).
   ============================================================ */
async function apiFetch(path, options = {}) {
  const token = getStoredToken();
  const headers = { ...(options.headers || {}) };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });

  if (response.status === 401) {
    clearStoredToken();
  }

  let data = null;
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    try {
      data = await response.json();
    } catch {
      data = null;
    }
  }

  if (!response.ok) {
    const message = data?.detail || `Request failed with status ${response.status}`;
    const err = new Error(message);
    err.status = response.status;
    err.data = data;
    throw err;
  }

  return data;
}

/* ============================================================
   Auth
   ============================================================ */
async function login(username, password) {
  const formData = new URLSearchParams();
  formData.append('username', username);
  formData.append('password', password);

  const data = await apiFetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: formData.toString(),
  });

  setStoredToken(data.access_token);
  return data.access_token;
}

function logout() {
  clearStoredToken();
}

/* ============================================================
   Reports
   ============================================================ */
function buildQuery(params = {}) {
  const usp = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      usp.append(key, value);
    }
  });
  const qs = usp.toString();
  return qs ? `?${qs}` : '';
}

async function getReports(params = {}) {
  return apiFetch(`/api/reports${buildQuery(params)}`);
}

async function getReportsGeoJSON(params = {}) {
  return apiFetch(`/api/reports/geojson${buildQuery(params)}`);
}

async function getStats() {
  return apiFetch('/api/reports/stats');
}

async function updateReportStatus(reportId, status) {
  // FOUND BUG (pre-existing in the original React code, not introduced by
  // this rewrite): the backend's PATCH /reports/{id}/status endpoint
  // declares `status: ReportStatus = Form(...)`, which requires the value
  // in the request body as form-encoded data. The original useApi.js sent
  // it as a URL query parameter instead (`params: { status }` with a null
  // axios body), which FastAPI's Form(...) would reject with a 422 -
  // meaning the status-change dropdown likely never actually worked.
  // Fixed here to send it correctly as application/x-www-form-urlencoded
  // body data.
  const body = new URLSearchParams();
  body.append('status', status);
  return apiFetch(`/api/reports/${reportId}/status`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  });
}

async function confirmReport(reportId) {
  return apiFetch(`/api/reports/${reportId}/confirm`, { method: 'PATCH' });
}

async function reviewReport(reportId, category, severity) {
  return apiFetch(`/api/reports/${reportId}/review`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ category, severity }),
  });
}

/* ============================================================
   Reports state (equivalent to hooks/useReports.js's useState/
   useRef values, as a plain module-level object since there's
   no React state system here)
   ============================================================ */
const reportsState = {
  reports: [],
  geojson: { type: 'FeatureCollection', features: [] },
  stats: null,
  loading: true,
  error: null,
  params: {},
  pollingHandle: null,
  onUpdate: null, // callback set by app.js, called after every state change
};

function notifyUpdate() {
  if (typeof reportsState.onUpdate === 'function') {
    reportsState.onUpdate();
  }
}

async function fetchReportsData() {
  try {
    reportsState.error = null;
    const [reportsRes, geojsonRes, statsRes] = await Promise.all([
      getReports(reportsState.params),
      getReportsGeoJSON({ ...reportsState.params, limit: 500 }),
      getStats(),
    ]);
    reportsState.reports = reportsRes.reports;
    reportsState.geojson = geojsonRes;
    reportsState.stats = statsRes;
  } catch (err) {
    reportsState.error = err.message || 'Failed to fetch data';
  } finally {
    reportsState.loading = false;
    notifyUpdate();
  }
}

function startPolling(interval = 30000) {
  if (reportsState.pollingHandle) return;
  reportsState.pollingHandle = setInterval(fetchReportsData, interval);
}

function stopPolling() {
  if (reportsState.pollingHandle) {
    clearInterval(reportsState.pollingHandle);
    reportsState.pollingHandle = null;
  }
}

function updateFilters(newParams) {
  reportsState.params = { ...reportsState.params, ...newParams, page: 1 };
  fetchReportsData();
}

async function changeReportStatus(reportId, newStatus) {
  try {
    await updateReportStatus(reportId, newStatus);
    // Optimistic update, same as the original React version
    reportsState.reports = reportsState.reports.map((r) =>
      r.id === reportId ? { ...r, status: newStatus } : r
    );
    reportsState.stats = await getStats();
    notifyUpdate();
  } catch (err) {
    reportsState.error = err.message || 'Failed to update status';
    notifyUpdate();
    // Revert on error by refetching, same as the original
    fetchReportsData();
  }
}

async function confirmPendingReport(reportId) {
  try {
    await confirmReport(reportId);
    await fetchReportsData();
  } catch (err) {
    reportsState.error = err.message || 'Failed to confirm report';
    notifyUpdate();
  }
}

async function reclassifyPendingReport(reportId, category, severity) {
  try {
    await reviewReport(reportId, category, severity);
    await fetchReportsData();
  } catch (err) {
    reportsState.error = err.message || 'Failed to reclassify report';
    notifyUpdate();
  }
}

/* ============================================================
   Map (equivalent to components/MapView.jsx - this component
   was already fairly imperative in React, using refs for the
   Leaflet instance rather than React state, so this port is
   close to line-for-line)
   ============================================================ */
let mapInstance = null;
const markersById = new Map();
let boundsFitted = false;
let currentPriorityFilter = 'all';
let onFeatureClickCallback = null;

const DEFAULT_CENTER = [37.7749, -122.4194]; // San Francisco
const DEFAULT_ZOOM = 12;

function initMap() {
  if (mapInstance) return;

  mapInstance = L.map('map', {
    center: DEFAULT_CENTER,
    zoom: DEFAULT_ZOOM,
    zoomControl: true,
    attributionControl: true,
  });

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(mapInstance);
}

function updateMapMarkers(geojson, priorityFilter, selectedId, onFeatureClick) {
  if (!mapInstance || !geojson?.features) return;

  onFeatureClickCallback = onFeatureClick;
  currentPriorityFilter = priorityFilter;

  const features = geojson.features;
  const currentIds = new Set(features.map((f) => f.properties.id));
  const existingIds = new Set(markersById.keys());

  // Remove markers no longer in data
  for (const id of existingIds) {
    if (!currentIds.has(id)) {
      mapInstance.removeLayer(markersById.get(id));
      markersById.delete(id);
    }
  }

  const bounds = L.latLngBounds([]);

  features.forEach((feature) => {
    const {
      id,
      category,
      final_priority_score,
      visual_severity_score,
      road_type,
      critical_proximity_flag,
      status,
      description,
      created_at,
    } = feature.properties;
    const [lng, lat] = feature.geometry.coordinates;

    const priorityClass = final_priority_score >= 8 ? 'high' : final_priority_score >= 4 ? 'medium' : 'low';

    // Apply priority filter
    if (priorityFilter && priorityFilter !== 'all' && priorityClass !== priorityFilter) {
      // If this feature is currently shown but now filtered out, remove it
      if (markersById.has(id)) {
        mapInstance.removeLayer(markersById.get(id));
        markersById.delete(id);
      }
      return;
    }

    bounds.extend([lat, lng]);

    let marker = markersById.get(id);

    const markerHtml = `<div class="severity-marker-icon ${priorityClass}" style="transform: rotate(-45deg);"></div>`;
    const icon = L.divIcon({
      className: 'severity-marker',
      html: markerHtml,
      iconSize: [28, 28],
      iconAnchor: [14, 28],
      popupAnchor: [0, -28],
    });

    const escapedCategory = String(category || '').replace('_', ' ');
    const escapedRoadType = String(road_type || '').replace('_', ' ') || 'unknown';
    const escapedStatus = String(status || '').replace('_', ' ');

    const popupContent = `
      <div style="min-width: 220px;">
        <div style="font-weight: 600; margin-bottom: 8px; text-transform: capitalize;">${escapedCategory}</div>
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
          <span class="severity-badge ${priorityClass}" style="width: 28px; height: 28px; font-size: 12px; font-weight: 700;">${final_priority_score.toFixed(1)}</span>
          <span style="font-size: 13px; color: #64748b;">Priority Score</span>
        </div>
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
          <span style="font-size: 12px; color: #64748b;">Visual: </span>
          <span style="font-size: 12px; font-weight: 600; color: #1e293b;">${visual_severity_score}/10</span>
        </div>
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
          <span style="font-size: 12px; color: #64748b;">Road: </span>
          <span style="font-size: 12px; font-weight: 600; color: #1e293b; text-transform: capitalize;">${escapedRoadType}</span>
        </div>
        ${critical_proximity_flag ? '<div style="font-size: 12px; color: #dc2626; margin-bottom: 8px;">&#9888; Near Critical Infrastructure</div>' : ''}
        <div style="font-size: 12px; color: #64748b; margin-bottom: 8px;">Status: <span style="text-transform: capitalize; color: #1e293b;">${escapedStatus}</span></div>
        ${description ? `<div style="font-size: 12px; margin-bottom: 8px;">${description}</div>` : ''}
        <div style="font-size: 11px; color: #94a3b8;">${new Date(created_at).toLocaleString()}</div>
      </div>
    `;

    if (marker) {
      marker.setLatLng([lat, lng]);
      marker.setIcon(icon);
      marker.setPopupContent(popupContent);
    } else {
      marker = L.marker([lat, lng], { icon }).bindPopup(popupContent).addTo(mapInstance);
      markersById.set(id, marker);
    }

    if (id === selectedId) {
      marker.openPopup();
      mapInstance.setView([lat, lng], 16, { animate: true });
    }

    marker.off('click');
    marker.on('click', () => {
      onFeatureClickCallback?.(id);
    });
  });

  if (features.length > 0 && !boundsFitted) {
    mapInstance.fitBounds(bounds, { padding: [50, 50], maxZoom: 16 });
    boundsFitted = true;
  }
}

/* ============================================================
   Legend (equivalent to components/MapView.jsx's exported
   MapLegend component)
   ============================================================ */
const PRIORITY_LEVELS = [
  { key: 'high', label: 'High (8-10)', color: '#dc2626' },
  { key: 'medium', label: 'Medium (4-7)', color: '#f59e0b' },
  { key: 'low', label: 'Low (1-3)', color: '#16a34a' },
];

function renderMapLegend(priorityFilter, onFilterChange) {
  const items = PRIORITY_LEVELS.map(({ key, label, color }) => {
    const isDimmed = priorityFilter !== 'all' && priorityFilter !== key;
    const isActive = priorityFilter === key;
    return `
      <button
        class="legend-btn"
        data-priority="${key}"
        style="display:flex;align-items:center;gap:8px;padding:6px 0;border:none;background:none;cursor:pointer;font-size:12px;
               color:${isActive || priorityFilter === 'all' ? 'var(--text)' : 'var(--text-muted)'};
               font-weight:${isActive ? 600 : 400};width:100%;text-align:left;"
      >
        <span style="width:16px;height:16px;border-radius:4px;background:${color};display:inline-block;opacity:${isDimmed ? 0.3 : 1};flex-shrink:0;"></span>
        ${label}
      </button>
    `;
  }).join('');

  const html = `
    <div class="map-legend glass-panel" style="position:absolute;bottom:20px;right:20px;z-index:100;padding:12px 16px;font-size:12px;min-width:160px;">
      <div style="font-weight:600;margin-bottom:8px;color:var(--text);">Priority Score Legend</div>
      ${items}
    </div>
  `;

  return { html, onFilterChange };
}

/* ============================================================
   Reports table (equivalent to components/ReportsTable.jsx)
   ============================================================ */
const PRIORITY_CLASSES = {
  high: { min: 8, max: 10 },
  medium: { min: 4, max: 7 },
  low: { min: 1, max: 3 },
};

const CATEGORY_CHOICES = [
  'pothole', 'broken_streetlight', 'graffiti', 'illegal_dumping',
  'cracked_sidewalk', 'damaged_sign', 'other',
];

// Per-row "currently editing a reclassify form" state, keyed by report id -
// equivalent to each ReviewActions instance's own useState in React.
const reclassifyingState = {};

function getPriorityClassForScore(score) {
  if (score >= 8) return 'high';
  if (score >= 4) return 'medium';
  return 'low';
}

function formatRelativeDate(dateString) {
  try {
    const diffMs = Date.now() - new Date(dateString).getTime();
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return 'now';
    if (mins < 60) return `${mins}m`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h`;
    return `${Math.floor(hours / 24)}d`;
  } catch {
    return dateString;
  }
}

function escapeHtmlTable(str) {
  const div = document.createElement('div');
  div.textContent = str ?? '';
  return div.innerHTML;
}

function needsReviewBadgeHtml(report) {
  if (!report.requires_manual_review && report.category !== 'unclassified') return '';
  const confidenceText = report.ai_confidence != null ? `${report.ai_confidence.toFixed(1)}%` : 'unknown';
  return `
    <span class="needs-review-badge" title="AI confidence: ${confidenceText}"
      style="display:inline-block;margin-left:6px;padding:2px 6px;font-size:10px;font-weight:700;
             border-radius:4px;background:#fef3c7;color:#92400e;border:1px solid #fbbf24;">
      &#9888; NEEDS REVIEW
    </span>
  `;
}

function reviewActionsHtml(report) {
  if (report.status !== 'pending_review') return '';

  if (!reclassifyingState[report.id]) {
    const disableConfirm = report.category === 'unclassified';
    return `
      <div class="review-actions" style="display:flex;flex-direction:column;gap:4px;">
        <button class="btn btn-secondary review-confirm-btn" data-report-id="${report.id}"
          style="padding:3px 6px;font-size:11px;width:100%;" ${disableConfirm ? 'disabled' : ''}
          title="${disableConfirm ? 'Reclassify first - AI could not guess a category' : 'Confirm the AI got it right'}">
          &check; Confirm
        </button>
        <button class="btn btn-secondary review-fix-btn" data-report-id="${report.id}"
          style="padding:3px 6px;font-size:11px;width:100%;">
          &#9998; Fix
        </button>
      </div>
    `;
  }

  const currentCategory = report.category === 'unclassified' ? 'other' : report.category;
  const currentSeverity = Math.round(report.visual_severity_score) || 5;
  const options = CATEGORY_CHOICES.map(
    (c) => `<option value="${c}" ${c === currentCategory ? 'selected' : ''}>${c.replace('_', ' ')}</option>`
  ).join('');

  return `
    <div class="reclassify-form" style="display:flex;flex-direction:column;gap:4px;max-width:100px;">
      <select class="action-btn reclassify-category" data-report-id="${report.id}" style="width:100%;font-size:10px;padding:2px 4px;">
        ${options}
      </select>
      <input type="number" min="1" max="10" value="${currentSeverity}" class="action-btn reclassify-severity"
        data-report-id="${report.id}" style="width:100%;font-size:10px;padding:2px 4px;" aria-label="Severity (1-10)" />
      <div style="display:flex;gap:3px;">
        <button class="btn btn-primary reclassify-save-btn" data-report-id="${report.id}" style="padding:2px 4px;font-size:10px;width:50%;">Save</button>
        <button class="btn btn-secondary reclassify-cancel-btn" data-report-id="${report.id}" style="padding:2px 4px;font-size:10px;width:50%;">&times;</button>
      </div>
    </div>
  `;
}

function statusSelectHtml(report) {
  return `
    <select class="action-btn status-select" data-report-id="${report.id}" aria-label="Change status for ${escapeHtmlTable(report.category)}">
      <option value="open" ${report.status === 'open' ? 'selected' : ''}>Open</option>
      <option value="in_progress" ${report.status === 'in_progress' ? 'selected' : ''}>In Progress</option>
      <option value="resolved" ${report.status === 'resolved' ? 'selected' : ''}>Resolved</option>
      <option value="rejected" ${report.status === 'rejected' ? 'selected' : ''}>Rejected</option>
    </select>
  `;
}

/**
 * Renders the reports table into #reports-table-container and wires up
 * every interactive control. Equivalent to ReportsTable.jsx's full render
 * output plus its event handlers (which in React were inline JSX
 * onClick/onChange props - here they're delegated listeners attached
 * fresh after each render, since the DOM is fully replaced each time).
 */
function renderReportsTable(reports, priorityFilter, selectedId, callbacks) {
  const container = document.getElementById('reports-table-container');

  const filteredReports = (!priorityFilter || priorityFilter === 'all')
    ? reports
    : reports.filter((r) => {
        const { min, max } = PRIORITY_CLASSES[priorityFilter];
        return r.final_priority_score >= min && r.final_priority_score <= max;
      });

  const legend = renderMapLegend(priorityFilter, callbacks.onPriorityFilterChange);

  if (filteredReports.length === 0) {
    container.innerHTML = `
      <div class="empty-state glass-panel" style="padding:40px;">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true" style="width:64px;height:64px;margin-bottom:16px;opacity:0.5;">
          <path d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
        </svg>
        <p>${reports.length === 0 ? 'No reports yet' : 'No reports match the current filter'}</p>
        <button class="btn btn-secondary" id="clear-filters-btn" style="margin-top:12px;width:auto;">Clear Filters</button>
      </div>
    `;
    document.getElementById('clear-filters-btn')?.addEventListener('click', () => callbacks.onPriorityFilterChange('all'));
    return;
  }

  const rows = filteredReports.map((report) => {
    const priorityClass = getPriorityClassForScore(report.final_priority_score);
    const isSelected = report.id === selectedId;
    return `
      <tr class="report-row" data-report-id="${report.id}" style="cursor:pointer;${isSelected ? 'background:var(--primary-light);' : ''}">
        <td>
          <div class="severity-cell">
            <span class="severity-badge ${priorityClass}" style="width:32px;height:28px;font-size:11px;">${report.final_priority_score.toFixed(1)}</span>
          </div>
        </td>
        <td>
          <span class="category-badge">${escapeHtmlTable((report.category || '').replace('_', ' '))}</span>
          ${needsReviewBadgeHtml(report)}
        </td>
        <td><span class="status-badge ${report.status}">${escapeHtmlTable((report.status || '').replace('_', ' '))}</span></td>
        <td class="coords">${formatRelativeDate(report.created_at)}</td>
        <td class="row-action-cell">
          ${report.status === 'pending_review' ? reviewActionsHtml(report) : statusSelectHtml(report)}
        </td>
      </tr>
    `;
  }).join('');

  container.innerHTML = `
    <div class="reports-table">
      <div class="sidebar-header">
        <div class="sidebar-title">Reports (${filteredReports.length} of ${reports.length})</div>
        ${legend.html}
      </div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr>
              <th style="width:40px;">Pri</th>
              <th>Category</th>
              <th style="width:80px;">Status</th>
              <th style="width:55px;">Time</th>
              <th style="width:100px;">Action</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>
  `;

  wireTableEvents(container, callbacks, priorityFilter);
}

function wireTableEvents(container, callbacks, priorityFilter) {
  // Row click -> select report (ignore clicks inside the action cell,
  // matching the original's e.stopPropagation() on the action controls)
  container.querySelectorAll('.report-row').forEach((row) => {
    row.addEventListener('click', (e) => {
      if (e.target.closest('.row-action-cell')) return;
      callbacks.onRowClick?.(row.dataset.reportId);
    });
  });

  // Legend priority filter buttons
  container.querySelectorAll('.legend-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const key = btn.dataset.priority;
      callbacks.onPriorityFilterChange(priorityFilter === key ? 'all' : key);
    });
  });

  // Status dropdown (for non-pending_review rows)
  container.querySelectorAll('.status-select').forEach((select) => {
    select.addEventListener('click', (e) => e.stopPropagation());
    select.addEventListener('change', (e) => {
      callbacks.onStatusChange?.(select.dataset.reportId, e.target.value);
    });
  });

  // Confirm button
  container.querySelectorAll('.review-confirm-btn').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      btn.disabled = true;
      await callbacks.onConfirm?.(btn.dataset.reportId);
    });
  });

  // Fix button -> switch this row into reclassify mode and re-render
  container.querySelectorAll('.review-fix-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      reclassifyingState[btn.dataset.reportId] = true;
      callbacks.onRerender?.();
    });
  });

  // Reclassify save
  container.querySelectorAll('.reclassify-save-btn').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const reportId = btn.dataset.reportId;
      const categorySelect = container.querySelector(`.reclassify-category[data-report-id="${reportId}"]`);
      const severityInput = container.querySelector(`.reclassify-severity[data-report-id="${reportId}"]`);
      btn.disabled = true;
      await callbacks.onReclassify?.(reportId, categorySelect.value, Number(severityInput.value));
      delete reclassifyingState[reportId];
    });
  });

  // Reclassify cancel
  container.querySelectorAll('.reclassify-cancel-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      delete reclassifyingState[btn.dataset.reportId];
      callbacks.onRerender?.();
    });
  });

  container.querySelectorAll('.reclassify-category, .reclassify-severity').forEach((el) => {
    el.addEventListener('click', (e) => e.stopPropagation());
  });
}

/* ============================================================
   Local UI state not owned by reportsState (equivalent to
   App.jsx's useState calls for filters/selection)
   ============================================================ */
const uiState = {
  priorityFilter: 'all',   // client-side only, filters the already-fetched list
  categoryFilter: 'all',   // sent to backend via updateFilters
  statusFilter: 'all',     // sent to backend via updateFilters
  selectedReportId: null,
};

const STAT_CONFIG = [
  { key: 'total_reports', label: 'Total Reports', icon: '\u{1F4CA}', color: '#2563eb' },
  { key: 'open_reports', label: 'Open', icon: '\u{1F535}', color: '#2563eb' },
  { key: 'in_progress_reports', label: 'In Progress', icon: '\u{1F7E1}', color: '#f59e0b' },
  { key: 'resolved_reports', label: 'Resolved', icon: '\u{1F7E2}', color: '#16a34a' },
];

/* ============================================================
   Rendering (equivalent to App.jsx's JSX re-rendering whenever
   state changes - here triggered explicitly by reportsState's
   onUpdate callback, since there's no React re-render cycle)
   ============================================================ */
function renderStatsCards() {
  const el = document.getElementById('stats-cards');
  const stats = reportsState.stats;
  if (!stats) {
    el.innerHTML = '';
    return;
  }

  const cardStyle = 'padding:16px;display:flex;flex-direction:column;align-items:center;gap:8px;';
  const cards = STAT_CONFIG.map(({ key, label, icon, color }) => `
    <div class="glass-panel" style="${cardStyle}">
      <span style="font-size:24px;">${icon}</span>
      <div style="font-size:28px;font-weight:700;color:${color};line-height:1;">${stats[key] ?? 0}</div>
      <div style="font-size:12px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;font-weight:500;">${label}</div>
    </div>
  `).join('');

  const avgCard = `
    <div class="glass-panel" style="${cardStyle}">
      <span style="font-size:24px;">\u{1F4C8}</span>
      <div style="font-size:28px;font-weight:700;color:#7c3aed;line-height:1;">${stats.avg_priority_score ?? 0}</div>
      <div style="font-size:12px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;font-weight:500;">Avg Priority</div>
    </div>
  `;

  el.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fit, minmax(140px, 1fr));gap:12px;';
  el.innerHTML = cards + avgCard;
}

function handleRowOrMarkerClick(reportId) {
  uiState.selectedReportId = uiState.selectedReportId === reportId ? null : reportId;
  renderDashboard();
}

function renderDashboard() {
  renderStatsCards();

  updateMapMarkers(reportsState.geojson, uiState.priorityFilter, uiState.selectedReportId, handleRowOrMarkerClick);

  renderReportsTable(reportsState.reports, uiState.priorityFilter, uiState.selectedReportId, {
    onPriorityFilterChange: (value) => {
      uiState.priorityFilter = value;
      renderDashboard();
    },
    onRowClick: handleRowOrMarkerClick,
    onStatusChange: async (reportId, newStatus) => {
      await changeReportStatus(reportId, newStatus);
    },
    onConfirm: async (reportId) => {
      await confirmPendingReport(reportId);
    },
    onReclassify: async (reportId, category, severity) => {
      await reclassifyPendingReport(reportId, category, severity);
    },
    onRerender: renderDashboard,
  });

  const errorBanner = document.getElementById('map-error-banner');
  if (reportsState.error) {
    errorBanner.textContent = reportsState.error;
    errorBanner.style.display = '';
  } else {
    errorBanner.style.display = 'none';
  }

  const refreshBtn = document.getElementById('refresh-btn');
  refreshBtn.disabled = reportsState.loading;
  refreshBtn.textContent = reportsState.loading ? 'Refreshing...' : 'Refresh';
}

/* ============================================================
   Auth flow (equivalent to App.jsx's authChecked/isAuthenticated
   gating and components/Login.jsx's submit handler)
   ============================================================ */
function showView(viewId) {
  ['boot-loading', 'login-view', 'dashboard-loading', 'app-view'].forEach((id) => {
    const el = document.getElementById(id);
    el.style.display = id === viewId ? (id === 'app-view' ? 'grid' : 'flex') : 'none';
  });
}

async function enterDashboard() {
  showView('dashboard-loading');

  initMap();

  reportsState.onUpdate = () => {
    // First successful load moves us from the loading screen to the real
    // dashboard, matching App.jsx's `loading && reports.length === 0` gate
    if (document.getElementById('dashboard-loading').style.display !== 'none') {
      showView('app-view');
      // Leaflet was initialized (initMap(), above) while #app-view was
      // still display:none, so it measured a zero-size container and will
      // render incorrectly (grey/misaligned tiles) unless told to
      // re-measure now that the container has real dimensions. React's
      // version never hit this because the map's containerRef only
      // existed once the component had already mounted into a visible
      // DOM tree - this plain-JS version pre-creates the map earlier, so
      // it needs this explicit fix that has no React equivalent.
      requestAnimationFrame(() => mapInstance?.invalidateSize());
    }
    renderDashboard();
  };

  await fetchReportsData();
  startPolling();
}

function exitDashboard() {
  stopPolling();
  reportsState.onUpdate = null;
  reportsState.reports = [];
  reportsState.geojson = { type: 'FeatureCollection', features: [] };
  reportsState.stats = null;
  reportsState.loading = true;
  uiState.selectedReportId = null;
  showView('login-view');
}

async function handleLoginSubmit(e) {
  e.preventDefault();
  const username = document.getElementById('username').value;
  const password = document.getElementById('password').value;
  const errorBox = document.getElementById('login-error');
  const errorText = document.getElementById('login-error-text');
  const submitBtn = document.getElementById('login-submit-btn');

  errorBox.style.display = 'none';
  submitBtn.disabled = true;
  const originalHtml = submitBtn.innerHTML;
  submitBtn.innerHTML = '<span class="spinner" aria-hidden="true"></span>Signing in...';

  try {
    await login(username, password);
    await enterDashboard();
  } catch (err) {
    errorText.textContent = err.message || 'Login failed. Please try again.';
    errorBox.style.display = '';
  } finally {
    submitBtn.disabled = !(document.getElementById('username').value && document.getElementById('password').value);
    submitBtn.innerHTML = originalHtml;
  }
}

function handleLogout() {
  logout();
  exitDashboard();
}

/* ============================================================
   Wire everything up on load (equivalent to App.jsx's mount
   effect that checks getStoredToken())
   ============================================================ */
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('login-form').addEventListener('submit', handleLoginSubmit);

  const loginSubmitBtn = document.getElementById('login-submit-btn');
  ['username', 'password'].forEach((id) => {
    document.getElementById(id).addEventListener('input', () => {
      loginSubmitBtn.disabled = !(document.getElementById('username').value && document.getElementById('password').value);
    });
  });

  document.getElementById('logout-btn').addEventListener('click', handleLogout);
  document.getElementById('refresh-btn').addEventListener('click', fetchReportsData);

  document.getElementById('category-filter').addEventListener('change', (e) => {
    uiState.categoryFilter = e.target.value;
    updateFilters({ category: e.target.value !== 'all' ? e.target.value : undefined });
  });
  document.getElementById('status-filter').addEventListener('change', (e) => {
    uiState.statusFilter = e.target.value;
    updateFilters({ status: e.target.value !== 'all' ? e.target.value : undefined });
  });

  const token = getStoredToken();
  if (token) {
    enterDashboard();
  } else {
    showView('login-view');
  }
});
