// CivicResolve Admin Dashboard — vanilla JS (no build step, no framework)
// Faithful port of the React version: useApi (auth/token/interceptor), useReports
// (polling + filters + confirm/reclassify), Login, StatsCards, ReportsTable
// (+ inline review actions), MapView + MapLegend all live here as plain functions.

const API_BASE = 'http://localhost:8000';
const TOKEN_KEY = 'civicresolve_admin_token';
const POLL_INTERVAL_MS = 30000;

// ---------------------------------------------------------------------------
// Token storage (was getStoredToken/setStoredToken/clearStoredToken in useApi.js)
// ---------------------------------------------------------------------------
function getStoredToken() { return localStorage.getItem(TOKEN_KEY); }
function setStoredToken(t) { localStorage.setItem(TOKEN_KEY, t); }
function clearStoredToken() { localStorage.removeItem(TOKEN_KEY); }

// ---------------------------------------------------------------------------
// Fetch wrapper with Authorization header + 401 handling
// (was the axios instance + interceptors in useApi.js)
// ---------------------------------------------------------------------------
async function apiFetch(path, options = {}) {
  const token = getStoredToken();
  const headers = { ...(options.headers || {}) };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });

  if (res.status === 401) {
    clearStoredToken();
    // Same behavior as the React version: no hard navigation, just clear
    // the token — the render loop below checks isAuthenticated and falls
    // back to the login screen on the next check.
    state.isAuthenticated = false;
    showLogin();
  }

  if (!res.ok) {
    let detail = `Request failed with status ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_) {}
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }

  if (res.status === 204) return null;
  return res.json();
}

async function login(username, password) {
  const body = new URLSearchParams();
  body.append('username', username);
  body.append('password', password);

  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  if (!res.ok) {
    let detail = 'Login failed. Please try again.';
    try { const b = await res.json(); detail = b.detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  const data = await res.json();
  setStoredToken(data.access_token);
  return data.access_token;
}

function logout() { clearStoredToken(); }

async function getReports(params = {}) {
  const qs = new URLSearchParams(params).toString();
  return apiFetch(`/api/reports${qs ? '?' + qs : ''}`);
}
async function getReportsGeoJSON(params = {}) {
  const qs = new URLSearchParams(params).toString();
  return apiFetch(`/api/reports/geojson${qs ? '?' + qs : ''}`);
}
async function getStats() {
  return apiFetch('/api/reports/stats');
}
async function updateReportStatus(reportId, status) {
  // The backend binds `status` via FastAPI's Form(...), which reads from
  // the request BODY (form-encoded), not the URL query string. Sending it
  // as a query param (an earlier version of this function did
  // `?status=...` with no body) gets a 422 "Field required" - verified
  // against a live instance before fixing.
  const body = new URLSearchParams();
  body.append('status', status);
  return apiFetch(`/api/reports/${reportId}/status`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
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

// ---------------------------------------------------------------------------
// App state (was the useState/useReports hook state in App.jsx)
// ---------------------------------------------------------------------------
const state = {
  isAuthenticated: false,
  authChecked: false,
  reports: [],
  geojson: { type: 'FeatureCollection', features: [] },
  stats: null,
  loading: true,
  error: null,
  categoryFilter: 'all',
  statusFilter: 'all',
  priorityFilter: 'all',
  selectedReportId: null,
  pollHandle: null,
};

// ---------------------------------------------------------------------------
// Data fetching (was fetchData/startPolling/stopPolling in useReports.js)
// ---------------------------------------------------------------------------
async function fetchData() {
  try {
    state.error = null;
    const params = {};
    if (state.categoryFilter !== 'all') params.category = state.categoryFilter;
    if (state.statusFilter !== 'all') params.status = state.statusFilter;

    const [reportsRes, geojsonRes, statsRes] = await Promise.all([
      getReports(params),
      getReportsGeoJSON({ ...params, limit: 500 }),
      getStats(),
    ]);
    state.reports = reportsRes.reports;
    state.geojson = geojsonRes;
    state.stats = statsRes;
  } catch (err) {
    state.error = err.message || 'Failed to fetch data';
  } finally {
    state.loading = false;
    renderAll();
  }
}

function startPolling() {
  if (state.pollHandle) return;
  state.pollHandle = setInterval(fetchData, POLL_INTERVAL_MS);
}
function stopPolling() {
  if (state.pollHandle) { clearInterval(state.pollHandle); state.pollHandle = null; }
}

async function changeStatus(reportId, newStatus) {
  try {
    await updateReportStatus(reportId, newStatus);
    const r = state.reports.find(r => r.id === reportId);
    if (r) r.status = newStatus;
    state.stats = await getStats();
    renderAll();
  } catch (err) {
    state.error = err.message || 'Failed to update status';
    await fetchData();
  }
}

async function confirmPending(reportId) {
  try {
    await confirmReport(reportId);
    await fetchData();
  } catch (err) {
    state.error = err.message || 'Failed to confirm report';
    renderAll();
  }
}

async function reclassifyReport(reportId, category, severity) {
  try {
    await reviewReport(reportId, category, severity);
    await fetchData();
  } catch (err) {
    state.error = err.message || 'Failed to reclassify report';
    renderAll();
  }
}

// ---------------------------------------------------------------------------
// Priority helpers (shared by table + map, was in ReportsTable.jsx / MapView.jsx)
// ---------------------------------------------------------------------------
const PRIORITY_CLASSES = {
  high: { min: 8, max: 10, label: 'High', color: '#dc2626' },
  medium: { min: 4, max: 7, label: 'Medium', color: '#f59e0b' },
  low: { min: 1, max: 3, label: 'Low', color: '#16a34a' },
};
function getPriorityClass(score) {
  if (score >= 8) return 'high';
  if (score >= 4) return 'medium';
  return 'low';
}
function formatRelativeTime(dateString) {
  try {
    const diffMs = Date.now() - new Date(dateString).getTime();
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return 'now';
    if (mins < 60) return `${mins}m`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h`;
    return `${Math.floor(hours / 24)}d`;
  } catch { return dateString; }
}

const CATEGORY_CHOICES = ['pothole', 'broken_streetlight', 'graffiti', 'illegal_dumping', 'cracked_sidewalk', 'damaged_sign', 'other'];

// ---------------------------------------------------------------------------
// StatsCards (was StatsCards.jsx)
// ---------------------------------------------------------------------------
const STAT_CONFIG = [
  { key: 'total_reports', label: 'Total Reports', icon: '📊', color: '#2563eb' },
  { key: 'open_reports', label: 'Open', icon: '🔵', color: '#2563eb' },
  { key: 'in_progress_reports', label: 'In Progress', icon: '🟡', color: '#f59e0b' },
  { key: 'resolved_reports', label: 'Resolved', icon: '🟢', color: '#16a34a' },
];

function renderStatsCards() {
  const el = document.getElementById('stats-cards');
  if (!state.stats) { el.innerHTML = ''; return; }

  const cardHtml = ({ key, label, icon, color }) => `
    <div class="glass-panel" style="padding:16px;display:flex;flex-direction:column;align-items:center;gap:8px;">
      <span style="font-size:24px;">${icon}</span>
      <div style="font-size:28px;font-weight:700;color:${color};line-height:1;">${state.stats[key] ?? 0}</div>
      <div style="font-size:12px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;font-weight:500;">${label}</div>
    </div>`;

  el.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fit, minmax(140px, 1fr));gap:12px;padding:16px 20px;border-bottom:1px solid var(--glass-border);background:transparent;';
  el.innerHTML = STAT_CONFIG.map(cardHtml).join('') + `
    <div class="glass-panel" style="padding:16px;display:flex;flex-direction:column;align-items:center;gap:8px;">
      <span style="font-size:24px;">📈</span>
      <div style="font-size:28px;font-weight:700;color:#7c3aed;line-height:1;">${state.stats.avg_priority_score ?? 0}</div>
      <div style="font-size:12px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;font-weight:500;">Avg Priority</div>
    </div>`;
}

// ---------------------------------------------------------------------------
// MapLegend (was MapLegend in MapView.jsx)
// ---------------------------------------------------------------------------
function renderMapLegendHtml() {
  const levels = [
    { key: 'high', label: 'High (8-10)', color: '#dc2626' },
    { key: 'medium', label: 'Medium (4-7)', color: '#f59e0b' },
    { key: 'low', label: 'Low (1-3)', color: '#16a34a' },
  ];
  const rows = levels.map(({ key, label, color }) => {
    const isActive = state.priorityFilter === key;
    const dimmed = state.priorityFilter !== 'all' && state.priorityFilter !== key;
    return `
      <button class="legend-btn" data-priority="${key}" style="display:flex;align-items:center;gap:8px;padding:6px 0;border:none;background:none;cursor:pointer;font-size:12px;color:${isActive || state.priorityFilter === 'all' ? 'var(--text)' : 'var(--text-muted)'};font-weight:${isActive ? 600 : 400};width:100%;text-align:left;">
        <span style="width:16px;height:16px;border-radius:4px;background:${color};display:inline-block;opacity:${dimmed ? 0.3 : 1};flex-shrink:0;"></span>
        ${label}
      </button>`;
  }).join('');

  return `
    <div class="map-legend glass-panel" style="position:absolute;bottom:20px;right:20px;z-index:400;padding:12px 16px;font-size:12px;min-width:160px;">
      <div style="font-weight:600;margin-bottom:8px;color:var(--text);">Priority Score Legend</div>
      ${rows}
    </div>`;
}

// ---------------------------------------------------------------------------
// ReportsTable + inline review actions (was ReportsTable.jsx)
// ---------------------------------------------------------------------------
function getFilteredReports() {
  if (state.priorityFilter === 'all') return state.reports;
  const { min, max } = PRIORITY_CLASSES[state.priorityFilter];
  return state.reports.filter(r => r.final_priority_score >= min && r.final_priority_score <= max);
}

function reviewActionsHtml(report) {
  if (report.status !== 'pending_review') return '';
  return `
    <div class="review-actions" data-report-id="${report.id}" style="display:flex;flex-direction:column;gap:4px;">
      <button class="btn btn-secondary confirm-btn" data-id="${report.id}" style="padding:3px 6px;font-size:11px;width:100%;" ${report.category === 'unclassified' ? 'disabled title="Reclassify first - AI could not guess a category"' : 'title="Confirm the AI got it right"'}>✓ Confirm</button>
      <button class="btn btn-secondary fix-btn" data-id="${report.id}" style="padding:3px 6px;font-size:11px;width:100%;">✎ Fix</button>
    </div>`;
}

function reclassifyFormHtml(report) {
  const initialCategory = report.category === 'unclassified' ? 'other' : report.category;
  const initialSeverity = Math.round(report.visual_severity_score) || 5;
  const options = CATEGORY_CHOICES.map(c => `<option value="${c}" ${c === initialCategory ? 'selected' : ''}>${c.replace('_', ' ')}</option>`).join('');
  return `
    <div class="reclassify-form" data-report-id="${report.id}" style="display:flex;flex-direction:column;gap:4px;max-width:100px;">
      <select class="action-btn reclassify-category" style="width:100%;font-size:10px;padding:2px 4px;">${options}</select>
      <input type="number" min="1" max="10" value="${initialSeverity}" class="action-btn reclassify-severity" style="width:100%;font-size:10px;padding:2px 4px;" aria-label="Severity (1-10)" />
      <div style="display:flex;gap:3px;">
        <button class="btn btn-primary reclassify-save" data-id="${report.id}" style="padding:2px 4px;font-size:10px;width:50%;">Save</button>
        <button class="btn btn-secondary reclassify-cancel" data-id="${report.id}" style="padding:2px 4px;font-size:10px;width:50%;">✕</button>
      </div>
    </div>`;
}

function needsReviewBadgeHtml(report) {
  if (!report.requires_manual_review && report.category !== 'unclassified') return '';
  const conf = report.ai_confidence != null ? report.ai_confidence.toFixed(1) + '%' : 'unknown';
  return `<span class="needs-review-badge" title="AI confidence: ${conf}" style="display:inline-block;margin-left:6px;padding:2px 6px;font-size:10px;font-weight:700;border-radius:4px;background:#fef3c7;color:#92400e;border:1px solid #fbbf24;">⚠ NEEDS REVIEW</span>`;
}

function renderReportsTable() {
  const container = document.getElementById('reports-table-container');
  const filtered = getFilteredReports();

  if (filtered.length === 0) {
    container.innerHTML = `
      <div class="empty-state glass-panel" style="padding:40px;">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true" style="width:64px;height:64px;margin-bottom:16px;opacity:0.5;">
          <path d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
        </svg>
        <p>${state.reports.length === 0 ? 'No reports yet' : 'No reports match the current filter'}</p>
        <button id="clear-filters-btn" class="btn btn-secondary" style="margin-top:12px;width:auto;">Clear Filters</button>
      </div>`;
    document.getElementById('clear-filters-btn')?.addEventListener('click', () => {
      state.priorityFilter = 'all';
      renderReportsTable();
      renderMap();
    });
    return;
  }

  const rows = filtered.map(report => {
    const priorityClass = getPriorityClass(report.final_priority_score);
    const isSelected = report.id === state.selectedReportId;
    const actionCell = report.status === 'pending_review'
      ? (report._reclassifying ? reclassifyFormHtml(report) : reviewActionsHtml(report))
      : `
        <select class="action-btn status-select" data-id="${report.id}" aria-label="Change status for ${report.category}">
          ${['open', 'in_progress', 'resolved', 'rejected'].map(s => `<option value="${s}" ${s === report.status ? 'selected' : ''}>${s.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase())}</option>`).join('')}
        </select>`;

    return `
      <tr data-id="${report.id}" class="report-row" style="cursor:pointer;background:${isSelected ? 'var(--primary-light)' : 'transparent'};">
        <td><div class="severity-cell"><span class="severity-badge ${priorityClass}" style="width:32px;height:28px;font-size:11px;">${report.final_priority_score.toFixed(1)}</span></div></td>
        <td><span class="category-badge">${report.category.replace('_', ' ')}</span>${needsReviewBadgeHtml(report)}</td>
        <td><span class="status-badge ${report.status}">${report.status.replace('_', ' ')}</span></td>
        <td class="coords">${formatRelativeTime(report.created_at)}</td>
        <td class="action-cell">${actionCell}</td>
      </tr>`;
  }).join('');

  container.innerHTML = `
    <div class="reports-table">
      <div class="sidebar-header">
        <div class="sidebar-title">Reports (${filtered.length} of ${state.reports.length})</div>
        ${renderMapLegendHtml()}
      </div>
      <div class="table-wrapper">
        <table>
          <thead><tr><th style="width:40px;">Pri</th><th>Category</th><th style="width:80px;">Status</th><th style="width:55px;">Time</th><th style="width:100px;">Action</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>`;

  wireTableEvents();
}

function wireTableEvents() {
  // Row click -> select + sync map
  document.querySelectorAll('.report-row').forEach(row => {
    row.addEventListener('click', () => {
      const id = row.dataset.id;
      state.selectedReportId = state.selectedReportId === id ? null : id;
      renderReportsTable();
      renderMap();
    });
  });

  // Status dropdown (non-pending reports)
  document.querySelectorAll('.status-select').forEach(sel => {
    sel.addEventListener('click', (e) => e.stopPropagation());
    sel.addEventListener('change', (e) => changeStatus(sel.dataset.id, e.target.value));
  });

  // Confirm button
  document.querySelectorAll('.confirm-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      btn.disabled = true;
      await confirmPending(btn.dataset.id);
    });
  });

  // Fix (enter reclassify mode)
  document.querySelectorAll('.fix-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const r = state.reports.find(r => r.id === btn.dataset.id);
      if (r) r._reclassifying = true;
      renderReportsTable();
    });
  });

  // Reclassify save/cancel
  document.querySelectorAll('.reclassify-save').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const form = btn.closest('.reclassify-form');
      const category = form.querySelector('.reclassify-category').value;
      const severity = Number(form.querySelector('.reclassify-severity').value);
      btn.disabled = true;
      await reclassifyReport(btn.dataset.id, category, severity);
    });
  });
  document.querySelectorAll('.reclassify-cancel').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const r = state.reports.find(r => r.id === btn.dataset.id);
      if (r) r._reclassifying = false;
      renderReportsTable();
    });
  });

  // Legend filter buttons
  document.querySelectorAll('.legend-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const key = btn.dataset.priority;
      state.priorityFilter = state.priorityFilter === key ? 'all' : key;
      renderReportsTable();
      renderMap();
    });
  });
}

// ---------------------------------------------------------------------------
// MapView (was MapView.jsx) — Leaflet, markers colored by priority
// ---------------------------------------------------------------------------
let leafletMap = null;
const markerLayer = new Map(); // report id -> L.Marker
let boundsFitted = false;

function initMap() {
  leafletMap = L.map('map', { center: [37.7749, -122.4194], zoom: 12, zoomControl: true, attributionControl: true });
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(leafletMap);
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str == null ? '' : String(str);
  return div.innerHTML;
}

function renderMap() {
  if (!leafletMap) return;
  const features = state.geojson?.features || [];
  const currentIds = new Set(features.map(f => f.properties.id));

  // Remove stale markers
  for (const id of Array.from(markerLayer.keys())) {
    if (!currentIds.has(id)) {
      leafletMap.removeLayer(markerLayer.get(id));
      markerLayer.delete(id);
    }
  }

  const bounds = L.latLngBounds([]);

  features.forEach(feature => {
    const { id, category, final_priority_score, visual_severity_score, road_type, critical_proximity_flag, status, description, created_at } = feature.properties;
    const [lng, lat] = feature.geometry.coordinates;

    if (state.priorityFilter !== 'all') {
      const cls = final_priority_score >= 8 ? 'high' : final_priority_score >= 4 ? 'medium' : 'low';
      if (cls !== state.priorityFilter) {
        if (markerLayer.has(id)) { leafletMap.removeLayer(markerLayer.get(id)); markerLayer.delete(id); }
        return;
      }
    }

    bounds.extend([lat, lng]);
    const priorityClass = final_priority_score >= 8 ? 'high' : final_priority_score >= 4 ? 'medium' : 'low';
    const icon = L.divIcon({
      className: 'severity-marker',
      html: `<div class="severity-marker-icon ${priorityClass}" style="transform: rotate(-45deg);"></div>`,
      iconSize: [28, 28], iconAnchor: [14, 28], popupAnchor: [0, -28],
    });

    const popupContent = `
      <div style="min-width:220px;">
        <div style="font-weight:600;margin-bottom:8px;text-transform:capitalize;">${category.replace('_', ' ')}</div>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
          <span class="severity-badge ${priorityClass}" style="width:28px;height:28px;font-size:12px;font-weight:700;">${final_priority_score.toFixed(1)}</span>
          <span style="font-size:13px;color:#64748b;">Priority Score</span>
        </div>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;"><span style="font-size:12px;color:#64748b;">Visual: </span><span style="font-size:12px;font-weight:600;color:#1e293b;">${visual_severity_score}/10</span></div>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;"><span style="font-size:12px;color:#64748b;">Road: </span><span style="font-size:12px;font-weight:600;color:#1e293b;text-transform:capitalize;">${road_type?.replace('_', ' ') || 'unknown'}</span></div>
        ${critical_proximity_flag ? '<div style="font-size:12px;color:#dc2626;margin-bottom:8px;">⚠ Near Critical Infrastructure</div>' : ''}
        <div style="font-size:12px;color:#64748b;margin-bottom:8px;">Status: <span style="text-transform:capitalize;color:#1e293b;">${status.replace('_', ' ')}</span></div>
        ${description ? `<div style="font-size:12px;margin-bottom:8px;">${escapeHtml(description)}</div>` : ''}
        <div style="font-size:11px;color:#94a3b8;">${new Date(created_at).toLocaleString()}</div>
      </div>`;

    let marker = markerLayer.get(id);
    if (marker) {
      marker.setLatLng([lat, lng]);
      marker.setIcon(icon);
      marker.setPopupContent(popupContent);
    } else {
      marker = L.marker([lat, lng], { icon }).bindPopup(popupContent).addTo(leafletMap);
      marker.on('click', () => {
        state.selectedReportId = state.selectedReportId === id ? null : id;
        renderReportsTable();
        renderMap();
      });
      markerLayer.set(id, marker);
    }

    if (id === state.selectedReportId) {
      marker.openPopup();
      leafletMap.setView([lat, lng], 16, { animate: true });
    }
  });

  if (features.length > 0 && !boundsFitted) {
    leafletMap.fitBounds(bounds, { padding: [50, 50], maxZoom: 16 });
    boundsFitted = true;
  }

  // Re-render the legend that lives inside the sidebar's table header
  // (legend markup is regenerated by renderReportsTable, this just keeps
  // the map's own error banner in sync)
  const errEl = document.getElementById('map-error');
  if (state.error) { errEl.textContent = state.error; errEl.style.display = 'block'; }
  else { errEl.style.display = 'none'; }
}

// ---------------------------------------------------------------------------
// Screen switching (was the authChecked/isAuthenticated/loading branches in App.jsx)
// ---------------------------------------------------------------------------
function showLogin() {
  document.getElementById('login-container').style.display = 'flex';
  document.getElementById('loading-container').style.display = 'none';
  document.getElementById('dashboard').style.display = 'none';
  stopPolling();
}
function showLoading(text) {
  document.getElementById('login-container').style.display = 'none';
  document.getElementById('loading-container').style.display = 'flex';
  document.getElementById('loading-text').textContent = text;
  document.getElementById('dashboard').style.display = 'none';
}
function showDashboard() {
  document.getElementById('login-container').style.display = 'none';
  document.getElementById('loading-container').style.display = 'none';
  document.getElementById('dashboard').style.display = 'grid';
  if (!leafletMap) initMap();
}

function renderAll() {
  if (!state.authChecked) { showLoading(''); return; }
  if (!state.isAuthenticated) { showLogin(); return; }
  if (state.loading && state.reports.length === 0) { showDashboard(); showLoading('Loading dashboard...'); return; }

  showDashboard();
  renderStatsCards();
  renderReportsTable();
  renderMap();
  document.getElementById('refresh-btn').disabled = state.loading;
  document.getElementById('refresh-btn').textContent = state.loading ? 'Refreshing...' : 'Refresh';
}

// ---------------------------------------------------------------------------
// Wire up on load
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  // Login form
  document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    const errEl = document.getElementById('login-error');
    const errText = document.getElementById('login-error-text');
    const btn = document.getElementById('login-submit-btn');

    errEl.style.display = 'none';
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner" aria-hidden="true"></span> Signing in...';

    try {
      await login(username, password);
      state.isAuthenticated = true;
      renderAll();
      startPolling();
      await fetchData();
    } catch (err) {
      errText.textContent = err.message || 'Login failed. Please try again.';
      errEl.style.display = 'flex';
    } finally {
      btn.disabled = false;
      btn.textContent = 'Sign In';
    }
  });

  // Logout
  document.getElementById('logout-btn').addEventListener('click', () => {
    logout();
    state.isAuthenticated = false;
    stopPolling();
    renderAll();
  });

  // Refresh
  document.getElementById('refresh-btn').addEventListener('click', () => fetchData());

  // Filters
  document.getElementById('category-filter').addEventListener('change', (e) => {
    state.categoryFilter = e.target.value;
    fetchData();
  });
  document.getElementById('status-filter').addEventListener('change', (e) => {
    state.statusFilter = e.target.value;
    fetchData();
  });

  // Check for existing token on mount (was the useEffect in App.jsx)
  const token = getStoredToken();
  state.isAuthenticated = !!token;
  state.authChecked = true;
  renderAll();

  if (state.isAuthenticated) {
    startPolling();
    fetchData();
  }
});
