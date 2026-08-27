'use strict';

// ============================================================
// CivicResolve — unified single-site app
// One login gate, then routes to either the citizen report form or the
// admin dashboard based on the role returned by POST /api/auth/login.
// ============================================================

// --- 1. Config ---
const API_BASE_URL = 'http://localhost:8000';
const TOKEN_KEY = 'civicresolve_token';
const ROLE_KEY = 'civicresolve_role';

// Helper to get user info from JWT
function getUserDetails() {
  const token = getStoredToken();
  if (!token) return null;
  try {
    const payload = JSON.parse(atob(token.split('.')[1])); 
    return { 
      identifier: payload.email || payload.sub,
      role: payload.role 
    };
  } catch(e) { return null; }
}

// ============================================================
// 2. Auth
// ============================================================
function getStoredToken() { return localStorage.getItem(TOKEN_KEY); }
function getStoredRole() { return localStorage.getItem(ROLE_KEY); }
function setStoredAuth(token, role) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(ROLE_KEY, role);
}
function clearStoredAuth() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(ROLE_KEY);
}

/**
 * Shared fetch wrapper used by BOTH citizen and admin logic below.
 */
async function apiFetch(path, options = {}) {
  const token = getStoredToken();
  const headers = { ...(options.headers || {}) };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });

  if (response.status === 401) {
    clearStoredAuth();
  }

  let data = null;
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    try { data = await response.json(); } catch { data = null; }
  }

  if (!response.ok) {
    const message = data?.detail || `Request failed with status ${response.status}`;
    const err = new Error(message);
    err.status = response.status;
    throw err;
  }
  return data;
}

async function login(usernameOrEmail, password) {
  const formData = new URLSearchParams();
  formData.append('username', usernameOrEmail);
  formData.append('password', password);

  const data = await apiFetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: formData.toString(),
  });

  setStoredAuth(data.access_token, data.role);
  return data.role;
}

async function signup(email, password) {
  return apiFetch('/api/auth/signup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
}

async function verifyEmailToken(token) {
  return apiFetch(`/api/auth/verify-email?token=${encodeURIComponent(token)}`);
}

function logout() {
  clearStoredAuth();
  stopPolling(); 
  showView('login-view');
}

// ============================================================
// 3. Citizen view logic (report submission)
// ============================================================
function getDeviceId() {
  let deviceId = localStorage.getItem('civicresolve_device_id');
  if (!deviceId) {
    deviceId = 'device_' + Math.random().toString(36).substr(2, 16) + Date.now().toString(36);
    localStorage.setItem('civicresolve_device_id', deviceId);
  }
  return deviceId;
}

async function submitReport(formData) {
  formData.append('user_id', getDeviceId());
  const token = getStoredToken();
  const response = await fetch(`${API_BASE_URL}/api/report/submit`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  });
  let data;
  try { data = await response.json(); } catch { data = null; }
  if (!response.ok) {
    throw new Error(data?.detail || `Request failed with status ${response.status}`);
  }
  return data;
}

const geo = { position: null, error: null, status: 'idle' };

function requestLocation() {
  const statusText = document.getElementById('gps-status-text');
  const spinner = document.getElementById('gps-spinner');
  const retryBtn = document.getElementById('gps-retry-btn');
  const statusEl = document.getElementById('gps-status');

  if (!navigator.geolocation) {
    geo.error = 'Geolocation is not supported by your browser';
    geo.status = 'error';
    renderGpsStatus();
    return;
  }
  geo.status = 'loading';
  geo.error = null;
  renderGpsStatus();

  navigator.geolocation.getCurrentPosition(
    (pos) => {
      geo.position = {
        latitude: pos.coords.latitude,
        longitude: pos.coords.longitude,
        accuracy: pos.coords.accuracy,
      };
      geo.status = 'success';
      renderGpsStatus();
      updateSubmitEnabled();
    },
    (err) => {
      let message = 'Unable to retrieve location';
      if (err.code === err.PERMISSION_DENIED) message = 'Location permission denied. Please enable in browser settings.';
      else if (err.code === err.POSITION_UNAVAILABLE) message = 'Location information unavailable.';
      else if (err.code === err.TIMEOUT) message = 'Location request timed out.';
      geo.error = message;
      geo.status = 'error';
      renderGpsStatus();
      updateSubmitEnabled();
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 300000 }
  );

  function renderGpsStatus() {
    statusEl.className = `gps-status ${geo.status === 'loading' ? 'loading' : geo.status === 'success' ? 'success' : 'error'}`;
    spinner.style.display = geo.status === 'loading' ? 'inline-block' : 'none';
    retryBtn.style.display = geo.status === 'error' ? 'inline-block' : 'none';
    if (geo.status === 'loading') statusText.textContent = 'Getting your location...';
    else if (geo.status === 'success') statusText.textContent = `Location acquired (±${Math.round(geo.position?.accuracy || 0)}m)`;
    else if (geo.status === 'error') statusText.textContent = geo.error;
  }
}

const upload = { file: null, maxSizeMB: 10 };

function validateFile(file) {
  const allowedTypes = ['image/jpeg', 'image/png', 'image/webp'];
  if (!allowedTypes.includes(file.type)) return 'Please select a valid image file (JPG, PNG, or WebP)';
  if (file.size > upload.maxSizeMB * 1024 * 1024) return `File size must be less than ${upload.maxSizeMB}MB`;
  return null;
}

function handleFileSelect(file) {
  const error = validateFile(file);
  if (error) { alert(error); return; }
  const reader = new FileReader();
  reader.onload = (e) => {
    upload.file = file;
    document.getElementById('upload-preview-img').src = e.target.result;
    document.getElementById('upload-placeholder').style.display = 'none';
    document.getElementById('upload-preview-container').style.display = 'block';
    document.getElementById('upload-area').classList.add('has-image');
    updateSubmitEnabled();
  };
  reader.readAsDataURL(file);
}

function removeImage() {
  upload.file = null;
  document.getElementById('file-input').value = '';
  document.getElementById('upload-placeholder').style.display = 'block';
  document.getElementById('upload-preview-container').style.display = 'none';
  document.getElementById('upload-area').classList.remove('has-image');
  updateSubmitEnabled();
}

function setupImageUpload() {
  const area = document.getElementById('upload-area');
  const input = document.getElementById('file-input');
  area.addEventListener('click', () => input.click());
  area.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
  area.addEventListener('dragover', (e) => { e.preventDefault(); area.classList.add('drag-active'); });
  area.addEventListener('dragleave', (e) => { e.preventDefault(); area.classList.remove('drag-active'); });
  area.addEventListener('drop', (e) => {
    e.preventDefault();
    area.classList.remove('drag-active');
    const file = e.dataTransfer.files[0];
    if (file) handleFileSelect(file);
  });
  input.addEventListener('change', (e) => { const file = e.target.files[0]; if (file) handleFileSelect(file); });
  document.getElementById('remove-image-btn').addEventListener('click', (e) => { e.stopPropagation(); removeImage(); });
}

let citizenSubmitting = false;
function updateSubmitEnabled() {
  const btn = document.getElementById('submit-btn');
  if (!btn) return;
  btn.disabled = citizenSubmitting || !upload.file || !geo.position;
}

function getPriorityClass(score) { return score >= 8 ? 'severity-high' : score >= 4 ? 'severity-medium' : 'severity-low'; }
function getPriorityLabel(score) { return score >= 8 ? 'High' : score >= 4 ? 'Medium' : 'Low'; }

function renderCitizenResult(result) {
  const grid = document.getElementById('result-grid');
  const visualSeverity = result.visual_severity_score ?? result.severity_score;
  const finalPriority = result.final_priority_score ?? result.severity_score;

  grid.innerHTML = `
    <div class="result-item"><div class="result-label">Issue Type</div><div class="result-value" style="text-transform:capitalize;">${(result.category || '').replace('_', ' ')}</div></div>
    <div class="result-item"><div class="result-label">Visual Severity</div><div class="result-value"><span class="severity-badge ${getPriorityClass(visualSeverity)}">${visualSeverity}/10</span></div></div>
    <div class="result-item"><div class="result-label">Road Type</div><div class="result-value" style="text-transform:capitalize;">${(result.road_type || '').replace('_', ' ') || 'Unknown'}</div></div>
    <div class="result-item"><div class="result-label">Final Priority Score</div><div class="result-value"><span class="severity-badge ${getPriorityClass(finalPriority)}">${Number(finalPriority).toFixed(1)}/10 &bull; ${getPriorityLabel(finalPriority)}</span></div></div>
    <div class="result-item"><div class="result-label">Critical Proximity</div><div class="result-value">${result.critical_proximity_flag ? '<span style="color:var(--danger);">&#9888; Near hospital/school</span>' : '<span style="color:var(--success);">&check; Clear</span>'}</div></div>
    <div class="result-item"><div class="result-label">Report ID</div><div class="result-value" style="font-family:monospace;font-size:12px;">${(result.report_id || '').slice(0, 8)}...</div></div>
    <div class="result-item"><div class="result-label">Status</div><div class="result-value" style="text-transform:capitalize;">${result.status || ''}</div></div>
  `;

  document.getElementById('manual-review-note').style.display = result.requires_manual_review ? 'block' : 'none';

  const dupNote = document.getElementById('duplicate-note');
  if (result.possible_duplicates && result.possible_duplicates.length > 0) {
    const count = result.possible_duplicates.length;
    const closest = result.possible_duplicates[0];
    const dateText = closest?.created_at ? `, the closest submitted ${new Date(closest.created_at).toLocaleDateString()}` : '';
    dupNote.textContent = `Heads up — ${count === 1 ? 'a similar report already exists' : `${count} similar reports already exist`} nearby${dateText}. Your report has still been saved and will be reviewed.`;
    dupNote.style.display = 'block';
  } else {
    dupNote.style.display = 'none';
  }

  document.getElementById('report-form').querySelectorAll('.form-group, .gps-status, #submit-btn').forEach(el => el.style.display = 'none');
  document.getElementById('result-card').style.display = 'block';
  if (navigator.vibrate) navigator.vibrate([50, 50, 50]);
}

function resetCitizenForm() {
  upload.file = null;
  document.getElementById('file-input').value = '';
  document.getElementById('upload-placeholder').style.display = 'block';
  document.getElementById('upload-preview-container').style.display = 'none';
  document.getElementById('upload-area').classList.remove('has-image');
  document.getElementById('description').value = '';
  document.getElementById('result-card').style.display = 'none';
  document.getElementById('error-toast').style.display = 'none';
  document.getElementById('report-form').querySelectorAll('.form-group, .gps-status, #submit-btn').forEach(el => el.style.display = '');
  updateSubmitEnabled();
}

async function handleCitizenSubmit(e) {
  e.preventDefault();
  const errorToast = document.getElementById('error-toast');
  const errorMsg = document.getElementById('error-toast-message');

  if (!upload.file) { errorMsg.textContent = 'Please add a photo of the issue'; errorToast.style.display = 'flex'; return; }
  if (!geo.position) { errorMsg.textContent = 'Location not available. Please enable location services.'; errorToast.style.display = 'flex'; return; }

  citizenSubmitting = true;
  updateSubmitEnabled();
  errorToast.style.display = 'none';
  document.getElementById('result-card').style.display = 'none';

  const btn = document.getElementById('submit-btn');
  const originalText = btn.textContent;
  btn.innerHTML = '<span class="btn-loading"><span class="spinner" aria-hidden="true"></span>Analyzing &amp; Submitting...</span>';

  try {
    const formData = new FormData();
    formData.append('image', upload.file);
    formData.append('latitude', geo.position.latitude.toString());
    formData.append('longitude', geo.position.longitude.toString());
    const description = document.getElementById('description').value.trim();
    if (description) formData.append('description', description);

    const result = await submitReport(formData);
    renderCitizenResult(result);
  } catch (err) {
    errorMsg.textContent = err.message || 'Failed to submit report. Please try again.';
    errorToast.style.display = 'flex';
  } finally {
    citizenSubmitting = false;
    btn.textContent = originalText;
    updateSubmitEnabled();
  }
}

function initCitizenView() {
  setupImageUpload();
  document.getElementById('report-form').addEventListener('submit', handleCitizenSubmit);
  document.getElementById('gps-retry-btn').addEventListener('click', requestLocation);
  document.getElementById('new-report-btn').addEventListener('click', resetCitizenForm);
  requestLocation();
  updateSubmitEnabled();

  // Populate User Badge
  const user = getUserDetails();
  if (user && user.identifier) {
    const initial = user.identifier.charAt(0).toUpperCase();
    document.getElementById('citizen-user-info').innerHTML = `
      <div class="user-avatar">${initial}</div>
      <span>${user.identifier}</span>
    `;
  }
}

// ============================================================
// 4. Admin view logic (dashboard, map, table)
// ============================================================
const reportsState = {
  reports: [], geojson: { type: 'FeatureCollection', features: [] }, stats: null,
  loading: true, error: null, params: {}, pollingHandle: null, onUpdate: null,
};
function notifyUpdate() { if (typeof reportsState.onUpdate === 'function') reportsState.onUpdate(); }

async function getReports(params = {}) { return apiFetch(`/api/reports${buildQuery(params)}`); }
async function getReportsGeoJSON(params = {}) { return apiFetch(`/api/reports/geojson${buildQuery(params)}`); }
async function getStats() { return apiFetch('/api/reports/stats'); }
function buildQuery(params = {}) {
  const usp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') usp.append(k, v); });
  const qs = usp.toString();
  return qs ? `?${qs}` : '';
}
async function updateReportStatus(reportId, status) {
  const body = new URLSearchParams();
  body.append('status', status);
  return apiFetch(`/api/reports/${reportId}/status`, { method: 'PATCH', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: body.toString() });
}
async function confirmReport(reportId) { return apiFetch(`/api/reports/${reportId}/confirm`, { method: 'PATCH' }); }
async function reviewReport(reportId, category, severity) {
  return apiFetch(`/api/reports/${reportId}/review`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ category, severity }) });
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
function startPolling(interval = 30000) { if (!reportsState.pollingHandle) reportsState.pollingHandle = setInterval(fetchReportsData, interval); }
function stopPolling() { if (reportsState.pollingHandle) { clearInterval(reportsState.pollingHandle); reportsState.pollingHandle = null; } }
async function changeReportStatus(reportId, newStatus) {
  try {
    await updateReportStatus(reportId, newStatus);
    reportsState.reports = reportsState.reports.map(r => r.id === reportId ? { ...r, status: newStatus } : r);
    reportsState.stats = await getStats();
    notifyUpdate();
  } catch (err) {
    reportsState.error = err.message || 'Failed to update status';
    notifyUpdate();
    fetchReportsData();
  }
}
async function confirmPendingReport(reportId) {
  try { await confirmReport(reportId); await fetchReportsData(); }
  catch (err) { reportsState.error = err.message || 'Failed to confirm report'; notifyUpdate(); }
}
async function reclassifyPendingReport(reportId, category, severity) {
  try { await reviewReport(reportId, category, severity); await fetchReportsData(); }
  catch (err) { reportsState.error = err.message || 'Failed to reclassify report'; notifyUpdate(); }
}

// --- Map (Leaflet) ---
let mapInstance = null;
const markersById = new Map();
let boundsFitted = false;
let onFeatureClickCallback = null;
const DEFAULT_CENTER = [37.7749, -122.4194];
const DEFAULT_ZOOM = 12;

function initMap() {
  if (mapInstance) return;
  mapInstance = L.map('map', { center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM, zoomControl: true, attributionControl: true });
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '&copy; OpenStreetMap contributors',
  }).addTo(mapInstance);
}

function escapeHtmlTable(str) { const div = document.createElement('div'); div.textContent = str ?? ''; return div.innerHTML; }

// --- MAP POPUP ENHANCEMENT ---
function updateMapMarkers(geojson, priorityFilter, selectedId, onFeatureClick) {
  if (!mapInstance || !geojson?.features) return;
  onFeatureClickCallback = onFeatureClick;
  const features = geojson.features;
  const currentIds = new Set(features.map(f => f.properties.id));
  
  for (const id of Array.from(markersById.keys())) {
    if (!currentIds.has(id)) { mapInstance.removeLayer(markersById.get(id)); markersById.delete(id); }
  }
  
  const bounds = L.latLngBounds([]);
  
  features.forEach((feature) => {
    const { id, category, final_priority_score, visual_severity_score, road_type, critical_proximity_flag, status, description, created_at } = feature.properties;
    const [lng, lat] = feature.geometry.coordinates;
    const priorityClass = final_priority_score >= 8 ? 'high' : final_priority_score >= 4 ? 'medium' : 'low';

    if (priorityFilter && priorityFilter !== 'all' && priorityClass !== priorityFilter) {
      if (markersById.has(id)) { mapInstance.removeLayer(markersById.get(id)); markersById.delete(id); }
      return;
    }
    bounds.extend([lat, lng]);

    // Cross-reference with reports array to get the image URL and Submitter ID
    const fullReport = reportsState.reports.find(r => r.id === id);
    const imgUrl = fullReport && fullReport.image_path ? `${API_BASE_URL}${fullReport.image_path}` : '';
    const submitter = fullReport && fullReport.user_id ? fullReport.user_id : 'Unknown User';

    // Build the Image Banner HTML
    const imgHtml = imgUrl 
      ? `<img src="${imgUrl}" style="width:100%;height:140px;object-fit:cover;border-radius:6px;margin-bottom:8px;border:1px solid #e2e8f0;display:block;" />` 
      : `<div style="width:100%;height:40px;background:#f8fafc;border-radius:6px;margin-bottom:8px;display:flex;align-items:center;justify-content:center;font-size:11px;color:#94a3b8;border:1px solid #e2e8f0;">No image</div>`;

    let marker = markersById.get(id);
    const icon = L.divIcon({
      className: 'severity-marker',
      html: `<div class="severity-marker-icon ${priorityClass}" style="transform: rotate(-45deg);"></div>`,
      iconSize: [28, 28], iconAnchor: [14, 28], popupAnchor: [0, -28],
    });

    // Build the expanded Popup Layout
    const popupContent = `
      <div style="min-width:220px; max-width:250px; font-family:inherit;">
        ${imgHtml}
        <div style="font-weight:600;margin-bottom:6px;text-transform:capitalize;font-size:14px;color:var(--text);">${String(category || '').replace('_', ' ')}</div>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid #e2e8f0;">
          <span class="severity-badge ${priorityClass}" style="width:24px;height:24px;font-size:11px;font-weight:700;color:white;display:flex;align-items:center;justify-content:center;border-radius:6px;">${final_priority_score.toFixed(1)}</span>
          <span style="font-size:12px;color:#475569;font-weight:600;">Priority Score</span>
        </div>
        <div style="font-size:11px;color:#475569;margin-bottom:4px;">Visual: <strong>${visual_severity_score}/10</strong> &bull; Road: <strong style="text-transform:capitalize;">${String(road_type || '').replace('_', ' ') || 'unknown'}</strong></div>
        ${critical_proximity_flag ? '<div style="font-size:11px;color:#dc2626;margin-bottom:4px;font-weight:600;">&#9888; Near Critical Infrastructure</div>' : ''}
        <div style="font-size:11px;color:#475569;margin-bottom:4px;">Status: <span style="text-transform:capitalize;font-weight:600;color:var(--text);">${String(status || '').replace('_', ' ')}</span></div>
        <div style="font-size:11px;color:#475569;margin-bottom:4px;display:flex;flex-direction:column;gap:2px;">
          <span style="font-weight:600;">Submitted By:</span>
          <span style="font-family:monospace;background:#f1f5f9;padding:4px;border-radius:4px;border:1px solid #e2e8f0;word-break:break-all;">${submitter}</span>
        </div>
        ${description ? `<div style="font-size:11px;margin-top:8px;padding:6px;background:#f8fafc;border-radius:6px;border:1px solid #e2e8f0;color:#334155;">${escapeHtmlTable(description)}</div>` : ''}
        <div style="font-size:10px;color:#94a3b8;margin-top:8px;text-align:right;">${new Date(created_at).toLocaleString()}</div>
      </div>`;

    if (marker) { marker.setLatLng([lat, lng]); marker.setIcon(icon); marker.setPopupContent(popupContent); }
    else { marker = L.marker([lat, lng], { icon }).bindPopup(popupContent).addTo(mapInstance); markersById.set(id, marker); }
    if (id === selectedId) { marker.openPopup(); mapInstance.setView([lat, lng], 16, { animate: true }); }
    marker.off('click');
    marker.on('click', () => onFeatureClickCallback?.(id));
  });
  
  if (features.length > 0 && !boundsFitted) { mapInstance.fitBounds(bounds, { padding: [50, 50], maxZoom: 16 }); boundsFitted = true; }
}

const PRIORITY_LEVELS = [
  { key: 'high', label: 'High (8-10)', color: '#dc2626' },
  { key: 'medium', label: 'Medium (4-7)', color: '#f59e0b' },
  { key: 'low', label: 'Low (1-3)', color: '#16a34a' },
];

function renderMapLegend(priorityFilter) {
  const items = PRIORITY_LEVELS.map(({ key, label, color }) => {
    const isDimmed = priorityFilter !== 'all' && priorityFilter !== key;
    const isActive = priorityFilter === key;
    return `<button class="legend-btn" data-priority="${key}" style="display:flex;align-items:center;gap:6px;padding:4px 8px;border:1px solid ${isActive ? 'var(--primary)' : 'var(--glass-border)'};background:${isActive ? 'var(--primary-light)' : 'var(--glass-bg)'};border-radius:20px;cursor:pointer;font-size:11px;color:${isActive ? 'var(--primary)' : 'var(--text-muted)'};font-weight:${isActive ? 600 : 400};">
      <span style="width:10px;height:10px;border-radius:50%;background:${color};display:inline-block;opacity:${isDimmed ? 0.3 : 1};"></span>${label}</button>`;
  }).join('');
  return `<div class="map-legend" style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;">${items}</div>`;
}

// --- Table ---
const PRIORITY_CLASSES = { high: { min: 8, max: 10 }, medium: { min: 4, max: 7 }, low: { min: 1, max: 3 } };
const CATEGORY_CHOICES = ['pothole', 'broken_streetlight', 'graffiti', 'illegal_dumping', 'cracked_sidewalk', 'damaged_sign', 'other'];
const reclassifyingState = {};

function getPriorityClassForScore(score) { return score >= 8 ? 'high' : score >= 4 ? 'medium' : 'low'; }
function formatRelativeDate(dateString) {
  try {
    const mins = Math.floor((Date.now() - new Date(dateString).getTime()) / 60000);
    if (mins < 1) return 'now';
    if (mins < 60) return `${mins}m`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h`;
    return `${Math.floor(hours / 24)}d`;
  } catch { return dateString; }
}

function needsReviewBadgeHtml(report) {
  if (!report.requires_manual_review && report.category !== 'unclassified') return '';
  const confidenceText = report.ai_confidence != null ? `${report.ai_confidence.toFixed(1)}%` : 'unknown';
  return `<span class="needs-review-badge" title="AI confidence: ${confidenceText}" style="display:inline-block;margin-left:6px;padding:2px 6px;font-size:10px;font-weight:700;border-radius:4px;background:#fef3c7;color:#92400e;border:1px solid #fbbf24;">&#9888; NEEDS REVIEW</span>`;
}

function reviewActionsHtml(report) {
  if (report.status !== 'pending_review') return '';
  if (!reclassifyingState[report.id]) {
    const disableConfirm = report.category === 'unclassified';
    return `<div class="review-actions" style="display:flex;flex-direction:column;gap:4px;">
      <button class="btn btn-secondary review-confirm-btn" data-report-id="${report.id}" style="padding:3px 6px;font-size:11px;width:100%;" ${disableConfirm ? 'disabled' : ''}>&check; Confirm</button>
      <button class="btn btn-secondary review-fix-btn" data-report-id="${report.id}" style="padding:3px 6px;font-size:11px;width:100%;">&#9998; Fix</button>
    </div>`;
  }
  const currentCategory = report.category === 'unclassified' ? 'other' : report.category;
  const currentSeverity = Math.round(report.visual_severity_score) || 5;
  const options = CATEGORY_CHOICES.map(c => `<option value="${c}" ${c === currentCategory ? 'selected' : ''}>${c.replace('_', ' ')}</option>`).join('');
  return `<div class="reclassify-form" style="display:flex;flex-direction:column;gap:4px;max-width:100px;">
    <select class="action-btn reclassify-category" data-report-id="${report.id}" style="width:100%;font-size:10px;padding:2px 4px;">${options}</select>
    <input type="number" min="1" max="10" value="${currentSeverity}" class="action-btn reclassify-severity" data-report-id="${report.id}" style="width:100%;font-size:10px;padding:2px 4px;" />
    <div style="display:flex;gap:3px;">
      <button class="btn btn-primary reclassify-save-btn" data-report-id="${report.id}" style="padding:2px 4px;font-size:10px;width:50%;">Save</button>
      <button class="btn btn-secondary reclassify-cancel-btn" data-report-id="${report.id}" style="padding:2px 4px;font-size:10px;width:50%;">&times;</button>
    </div></div>`;
}

function statusSelectHtml(report) {
  return `<select class="action-btn status-select" data-report-id="${report.id}">
    <option value="open" ${report.status === 'open' ? 'selected' : ''}>Open</option>
    <option value="in_progress" ${report.status === 'in_progress' ? 'selected' : ''}>In Progress</option>
    <option value="resolved" ${report.status === 'resolved' ? 'selected' : ''}>Resolved</option>
    <option value="rejected" ${report.status === 'rejected' ? 'selected' : ''}>Rejected</option>
  </select>`;
}

function renderReportsTable(reports, priorityFilter, selectedId, callbacks) {
  const container = document.getElementById('reports-table-container');
  const filteredReports = (!priorityFilter || priorityFilter === 'all') ? reports : reports.filter(r => {
    const { min, max } = PRIORITY_CLASSES[priorityFilter];
    return r.final_priority_score >= min && r.final_priority_score <= max;
  });
  const legendHtml = renderMapLegend(priorityFilter);

  if (filteredReports.length === 0) {
    container.innerHTML = `<div class="empty-state glass-panel" style="padding:40px;">
      <p>${reports.length === 0 ? 'No reports yet' : 'No reports match the current filter'}</p>
      <button class="btn btn-secondary" id="clear-filters-btn" style="margin-top:12px;width:auto;">Clear Filters</button></div>`;
    document.getElementById('clear-filters-btn')?.addEventListener('click', () => callbacks.onPriorityFilterChange('all'));
    return;
  }

  const rows = filteredReports.map((report) => {
    const priorityClass = getPriorityClassForScore(report.final_priority_score);
    const isSelected = report.id === selectedId;
    
    const imgUrl = report.image_path ? `${API_BASE_URL}${report.image_path}` : '';
    const imgHtml = imgUrl 
        ? `<img src="${imgUrl}" style="width:40px;height:40px;object-fit:cover;border-radius:4px;border:1px solid #ccc;" />` 
        : `<div style="width:40px;height:40px;background:#eee;border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:10px;color:#aaa;">No Img</div>`;

    return `<tr class="report-row" data-report-id="${report.id}" style="cursor:pointer;${isSelected ? 'background:var(--primary-light);' : ''}">
      <td>${imgHtml}</td>
      <td><span class="severity-badge ${priorityClass}" style="width:32px;height:28px;font-size:11px;">${report.final_priority_score.toFixed(1)}</span></td>
      <td>
        <div style="margin-bottom:4px;"><span class="category-badge">${escapeHtmlTable((report.category || '').replace('_', ' '))}</span>${needsReviewBadgeHtml(report)}</div>
        <div style="font-size:10px;color:var(--text-muted);max-width:140px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtmlTable(report.description || 'No description provided')}</div>
      </td>
      <td><span class="status-badge ${report.status}">${escapeHtmlTable((report.status || '').replace('_', ' '))}</span></td>
      <td class="coords">${formatRelativeDate(report.created_at)}</td>
      <td class="row-action-cell">${report.status === 'pending_review' ? reviewActionsHtml(report) : statusSelectHtml(report)}</td>
    </tr>`;
  }).join('');

  container.innerHTML = `<div class="reports-table">
    <div class="sidebar-header">
      <div class="sidebar-title">Reports (${filteredReports.length} of ${reports.length})</div>
      ${legendHtml}
    </div>
    <div class="table-wrapper"><table><thead><tr>
      <th style="width:48px;">Img</th><th style="width:40px;">Pri</th><th>Category & Details</th><th style="width:80px;">Status</th><th style="width:50px;">Time</th><th style="width:100px;">Action</th>
    </tr></thead><tbody>${rows}</tbody></table></div></div>`;

  wireTableEvents(container, callbacks, priorityFilter);
}

function wireTableEvents(container, callbacks, priorityFilter) {
  container.querySelectorAll('.report-row').forEach(row => {
    row.addEventListener('click', (e) => { if (e.target.closest('.row-action-cell')) return; callbacks.onRowClick?.(row.dataset.reportId); });
  });
  container.querySelectorAll('.legend-btn').forEach(btn => {
    btn.addEventListener('click', () => { const key = btn.dataset.priority; callbacks.onPriorityFilterChange(priorityFilter === key ? 'all' : key); });
  });
  container.querySelectorAll('.status-select').forEach(select => {
    select.addEventListener('click', e => e.stopPropagation());
    select.addEventListener('change', e => callbacks.onStatusChange?.(select.dataset.reportId, e.target.value));
  });
  container.querySelectorAll('.review-confirm-btn').forEach(btn => {
    btn.addEventListener('click', async e => { e.stopPropagation(); btn.disabled = true; await callbacks.onConfirm?.(btn.dataset.reportId); });
  });
  container.querySelectorAll('.review-fix-btn').forEach(btn => {
    btn.addEventListener('click', e => { e.stopPropagation(); reclassifyingState[btn.dataset.reportId] = true; callbacks.onRerender?.(); });
  });
  container.querySelectorAll('.reclassify-save-btn').forEach(btn => {
    btn.addEventListener('click', async e => {
      e.stopPropagation();
      const reportId = btn.dataset.reportId;
      const categorySelect = container.querySelector(`.reclassify-category[data-report-id="${reportId}"]`);
      const severityInput = container.querySelector(`.reclassify-severity[data-report-id="${reportId}"]`);
      btn.disabled = true;
      await callbacks.onReclassify?.(reportId, categorySelect.value, Number(severityInput.value));
      delete reclassifyingState[reportId];
    });
  });
  container.querySelectorAll('.reclassify-cancel-btn').forEach(btn => {
    btn.addEventListener('click', e => { e.stopPropagation(); delete reclassifyingState[btn.dataset.reportId]; callbacks.onRerender?.(); });
  });
  container.querySelectorAll('.reclassify-category, .reclassify-severity').forEach(el => el.addEventListener('click', e => e.stopPropagation()));
}

// --- Admin dashboard orchestration ---
const adminUiState = { priorityFilter: 'all', selectedReportId: null };
const STAT_CONFIG = [
  { key: 'total_reports', label: 'Total Reports', icon: '\u{1F4CA}', color: '#2563eb' },
  { key: 'open_reports', label: 'Open', icon: '\u{1F535}', color: '#2563eb' },
  { key: 'in_progress_reports', label: 'In Progress', icon: '\u{1F7E1}', color: '#f59e0b' },
  { key: 'resolved_reports', label: 'Resolved', icon: '\u{1F7E2}', color: '#16a34a' },
];

function renderStatsCards() {
  const el = document.getElementById('stats-cards');
  const stats = reportsState.stats;
  if (!stats) { el.innerHTML = ''; return; }
  const cardStyle = 'padding:16px;display:flex;flex-direction:column;align-items:center;gap:8px;margin-bottom:12px;';
  const cards = STAT_CONFIG.map(({ key, label, icon, color }) => `
    <div class="glass-panel" style="${cardStyle}"><span style="font-size:24px;">${icon}</span>
      <div style="font-size:28px;font-weight:700;color:${color};line-height:1;">${stats[key] ?? 0}</div>
      <div style="font-size:12px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;font-weight:500;">${label}</div></div>`).join('');
  const avgCard = `<div class="glass-panel" style="${cardStyle}"><span style="font-size:24px;">\u{1F4C8}</span>
    <div style="font-size:28px;font-weight:700;color:#7c3aed;line-height:1;">${stats.avg_priority_score ?? 0}</div>
    <div style="font-size:12px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;font-weight:500;">Avg Priority</div></div>`;
  
  el.style.cssText = 'display:flex;flex-direction:column;gap:12px;';
  el.innerHTML = cards + avgCard;
}

function handleRowOrMarkerClick(reportId) {
  adminUiState.selectedReportId = adminUiState.selectedReportId === reportId ? null : reportId;
  renderAdminDashboard();
}

function renderAdminDashboard() {
  renderStatsCards();
  updateMapMarkers(reportsState.geojson, adminUiState.priorityFilter, adminUiState.selectedReportId, handleRowOrMarkerClick);
  renderReportsTable(reportsState.reports, adminUiState.priorityFilter, adminUiState.selectedReportId, {
    onPriorityFilterChange: (value) => { adminUiState.priorityFilter = value; renderAdminDashboard(); },
    onRowClick: handleRowOrMarkerClick,
    onStatusChange: async (id, status) => { await changeReportStatus(id, status); },
    onConfirm: async (id) => { await confirmPendingReport(id); },
    onReclassify: async (id, category, severity) => { await reclassifyPendingReport(id, category, severity); },
    onRerender: renderAdminDashboard,
  });
  const errorBanner = document.getElementById('map-error-banner');
  if (reportsState.error) { errorBanner.textContent = reportsState.error; errorBanner.style.display = ''; }
  else { errorBanner.style.display = 'none'; }
  const refreshBtn = document.getElementById('refresh-btn');
  refreshBtn.disabled = reportsState.loading;
  refreshBtn.textContent = reportsState.loading ? 'Refreshing...' : 'Refresh';
}

async function initAdminView() {
  initMap();
  reportsState.onUpdate = () => {
    if (document.getElementById('dashboard-loading').style.display !== 'none') {
      showView('admin-view');
      requestAnimationFrame(() => mapInstance?.invalidateSize());
    }
    renderAdminDashboard();
  };
  document.getElementById('refresh-btn').addEventListener('click', fetchReportsData);
  document.getElementById('category-filter').addEventListener('change', e => updateFilters({ category: e.target.value !== 'all' ? e.target.value : undefined }));
  document.getElementById('status-filter').addEventListener('change', e => updateFilters({ status: e.target.value !== 'all' ? e.target.value : undefined }));
  function updateFilters(newParams) { reportsState.params = { ...reportsState.params, ...newParams, page: 1 }; fetchReportsData(); }

  showView('dashboard-loading');
  await fetchReportsData();
  startPolling();

  // Populate Admin Badge
  const user = getUserDetails();
  if (user && user.identifier) {
    document.getElementById('admin-user-info').innerHTML = `
      <div class="user-avatar" style="background:#f59e0b;color:white;">A</div>
      <span>Admin (${user.identifier})</span>
    `;
  }
}

// ============================================================
// 5. Boot / view routing
// ============================================================
function showView(viewId) {
  const layoutViews = { 'admin-view': 'grid' };
  ['boot-loading', 'login-view', 'signup-view', 'verify-notice-view', 'dashboard-loading', 'citizen-view', 'admin-view'].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.style.display = id === viewId ? (layoutViews[id] || 'flex') : 'none';
  });
}

async function routeToRoleView() {
  const role = getStoredRole();
  if (role === 'admin') {
    await initAdminView();
  } else if (role === 'citizen') {
    showView('citizen-view');
    initCitizenView();
  } else {
    clearStoredAuth();
    showView('login-view');
  }
}

async function handleLoginSubmit(e) {
  e.preventDefault();
  const usernameOrEmail = document.getElementById('login-username').value;
  const password = document.getElementById('login-password').value;
  const errorBox = document.getElementById('login-error');
  const errorText = document.getElementById('login-error-text');
  const submitBtn = document.getElementById('login-submit-btn');

  errorBox.style.display = 'none';
  submitBtn.disabled = true;
  submitBtn.textContent = 'Signing in...';

  try {
    const role = await login(usernameOrEmail, password);
    await routeToRoleView();
  } catch (err) {
    errorText.textContent = err.message || 'Login failed. Please try again.';
    errorBox.style.display = '';
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Sign In';
  }
}

async function handleSignupSubmit(e) {
  e.preventDefault();
  const email = document.getElementById('signup-email').value;
  const password = document.getElementById('signup-password').value;
  const confirmPassword = document.getElementById('signup-confirm-password').value;
  const errorBox = document.getElementById('signup-error');
  const errorText = document.getElementById('signup-error-text');
  const submitBtn = document.getElementById('signup-submit-btn');

  errorBox.style.display = 'none';

  if (password !== confirmPassword) {
    errorText.textContent = 'Passwords do not match';
    errorBox.style.display = '';
    return;
  }
  if (password.length < 8) {
    errorText.textContent = 'Password must be at least 8 characters';
    errorBox.style.display = '';
    return;
  }

  submitBtn.disabled = true;
  submitBtn.textContent = 'Creating account...';

  try {
    const result = await signup(email, password);
    document.getElementById('verify-notice-text').textContent = result.message;
    showView('verify-notice-view');
  } catch (err) {
    errorText.textContent = err.message || 'Signup failed. Please try again.';
    errorBox.style.display = '';
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Create Account';
  }
}

function handleLogout() { logout(); }

document.addEventListener('DOMContentLoaded', async () => {
  // Email verification link handler
  const params = new URLSearchParams(window.location.search);
  const verifyToken = params.get('verify_token');
  if (verifyToken) {
    showView('boot-loading');
    try {
      await verifyEmailToken(verifyToken);
      alert('Email verified! You can now log in.');
    } catch (err) {
      alert(`Verification failed: ${err.message}`);
    }
    window.history.replaceState({}, '', window.location.pathname);
  }

  document.getElementById('login-form').addEventListener('submit', handleLoginSubmit);
  document.getElementById('signup-form').addEventListener('submit', handleSignupSubmit);
  document.getElementById('show-signup-link').addEventListener('click', (e) => { e.preventDefault(); showView('signup-view'); });
  document.getElementById('show-login-link').addEventListener('click', (e) => { e.preventDefault(); showView('login-view'); });
  document.getElementById('verify-notice-back-btn').addEventListener('click', () => showView('login-view'));
  document.getElementById('citizen-logout-btn').addEventListener('click', handleLogout);
  document.getElementById('admin-logout-btn').addEventListener('click', handleLogout);

  const token = getStoredToken();
  if (token && getStoredRole()) {
    await routeToRoleView();
  } else {
    showView('login-view');
  }
});