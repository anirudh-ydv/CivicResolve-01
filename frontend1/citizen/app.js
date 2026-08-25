// CivicResolve Citizen Portal — vanilla JS (no build step, no framework)
// Faithful port of the React version: useGeolocation, ImageUpload, useApi (submitReport),
// and ReportForm's submit/result-rendering logic all live here as plain functions.

const API_BASE = 'http://localhost:8000';

// ---------------------------------------------------------------------------
// Device ID (was getDeviceId() in useApi.js)
// ---------------------------------------------------------------------------
function getDeviceId() {
  let deviceId = localStorage.getItem('civicresolve_device_id');
  if (!deviceId) {
    deviceId = 'device_' + Math.random().toString(36).substr(2, 16) + Date.now().toString(36);
    localStorage.setItem('civicresolve_device_id', deviceId);
  }
  return deviceId;
}

// ---------------------------------------------------------------------------
// submitReport (was submitReport() in useApi.js)
// ---------------------------------------------------------------------------
async function submitReport(formData) {
  formData.append('user_id', getDeviceId());
  const res = await fetch(`${API_BASE}/api/report/submit`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    let detail = `Request failed with status ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_) { /* non-JSON error body, keep default */ }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Geolocation (was useGeolocation.js)
// ---------------------------------------------------------------------------
const geo = {
  position: null,
  error: null,
  status: 'idle', // idle | loading | success | error
};

function requestLocation() {
  const statusEl = document.getElementById('gps-status');
  const textEl = document.getElementById('gps-status-text');
  const spinnerEl = document.getElementById('gps-spinner');
  const retryBtn = document.getElementById('gps-retry-btn');

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
        timestamp: pos.timestamp,
      };
      geo.status = 'success';
      renderGpsStatus();
      updateSubmitEnabled();
    },
    (err) => {
      let message = 'Unable to retrieve location';
      switch (err.code) {
        case err.PERMISSION_DENIED:
          message = 'Location permission denied. Please enable in browser settings.';
          break;
        case err.POSITION_UNAVAILABLE:
          message = 'Location information unavailable.';
          break;
        case err.TIMEOUT:
          message = 'Location request timed out.';
          break;
      }
      geo.error = message;
      geo.status = 'error';
      renderGpsStatus();
      updateSubmitEnabled();
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 300000 }
  );
}

function renderGpsStatus() {
  const statusEl = document.getElementById('gps-status');
  const textEl = document.getElementById('gps-status-text');
  const spinnerEl = document.getElementById('gps-spinner');
  const retryBtn = document.getElementById('gps-retry-btn');

  statusEl.className = `gps-status ${geo.status === 'loading' ? 'loading' : geo.status === 'success' ? 'success' : 'error'}`;
  spinnerEl.style.display = geo.status === 'loading' ? 'inline-block' : 'none';
  retryBtn.style.display = geo.status === 'error' ? 'inline-block' : 'none';

  if (geo.status === 'loading') textEl.textContent = 'Getting your location...';
  else if (geo.status === 'success') textEl.textContent = `Location acquired (±${Math.round(geo.position?.accuracy || 0)}m)`;
  else if (geo.status === 'error') textEl.textContent = geo.error;
}

// ---------------------------------------------------------------------------
// Image upload (was ImageUpload.jsx)
// ---------------------------------------------------------------------------
const upload = {
  file: null,
  maxSizeMB: 10,
};

function validateFile(file) {
  const allowedTypes = ['image/jpeg', 'image/png', 'image/webp'];
  if (!allowedTypes.includes(file.type)) {
    return 'Please select a valid image file (JPG, PNG, or WebP)';
  }
  if (file.size > upload.maxSizeMB * 1024 * 1024) {
    return `File size must be less than ${upload.maxSizeMB}MB`;
  }
  return null;
}

function handleFileSelect(file) {
  const error = validateFile(file);
  if (error) {
    alert(error);
    return;
  }
  const reader = new FileReader();
  reader.onload = (e) => {
    upload.file = file;
    document.getElementById('upload-preview-img').src = e.target.result;
    document.getElementById('upload-placeholder').style.display = 'none';
    document.getElementById('upload-preview-container').style.display = 'block';
    updateSubmitEnabled();
  };
  reader.readAsDataURL(file);
}

function removeImage() {
  upload.file = null;
  document.getElementById('file-input').value = '';
  document.getElementById('upload-placeholder').style.display = 'block';
  document.getElementById('upload-preview-container').style.display = 'none';
  updateSubmitEnabled();
}

function setupImageUpload() {
  const area = document.getElementById('upload-area');
  const input = document.getElementById('file-input');

  area.addEventListener('click', () => input.click());
  area.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); }
  });
  area.addEventListener('dragover', (e) => { e.preventDefault(); area.classList.add('drag-active'); });
  area.addEventListener('dragleave', (e) => { e.preventDefault(); area.classList.remove('drag-active'); });
  area.addEventListener('drop', (e) => {
    e.preventDefault();
    area.classList.remove('drag-active');
    const file = e.dataTransfer.files[0];
    if (file) handleFileSelect(file);
  });
  input.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) handleFileSelect(file);
  });
  document.getElementById('remove-image-btn').addEventListener('click', (e) => {
    e.stopPropagation();
    removeImage();
  });
}

// ---------------------------------------------------------------------------
// Submit enable/disable (was `disabled={submitting || !image || !position}`)
// ---------------------------------------------------------------------------
let submitting = false;

function updateSubmitEnabled() {
  const btn = document.getElementById('submit-btn');
  btn.disabled = submitting || !upload.file || !geo.position;
}

// ---------------------------------------------------------------------------
// Priority helpers (was getPriorityClass / getPriorityLabel in ReportForm.jsx)
// ---------------------------------------------------------------------------
function getPriorityClass(score) {
  if (score >= 8) return 'severity-high';
  if (score >= 4) return 'severity-medium';
  return 'severity-low';
}
function getPriorityLabel(score) {
  if (score >= 8) return 'High';
  if (score >= 4) return 'Medium';
  return 'Low';
}

// ---------------------------------------------------------------------------
// Result rendering (was the JSX result-card block in ReportForm.jsx)
// ---------------------------------------------------------------------------
function renderResult(result) {
  const grid = document.getElementById('result-grid');
  const visualSeverity = result.visual_severity_score ?? result.severity_score;
  const finalPriority = result.final_priority_score ?? result.severity_score;

  grid.innerHTML = `
    <div class="result-item">
      <div class="result-label">Issue Type</div>
      <div class="result-value" style="text-transform:capitalize;">${(result.category || '').replace('_', ' ')}</div>
    </div>
    <div class="result-item">
      <div class="result-label">Visual Severity</div>
      <div class="result-value">
        <span class="severity-badge ${getPriorityClass(visualSeverity)}">${visualSeverity}/10</span>
      </div>
    </div>
    <div class="result-item">
      <div class="result-label">Road Type</div>
      <div class="result-value" style="text-transform:capitalize;">${(result.road_type || '').replace('_', ' ') || 'Unknown'}</div>
    </div>
    <div class="result-item">
      <div class="result-label">Final Priority Score</div>
      <div class="result-value">
        <span class="severity-badge ${getPriorityClass(finalPriority)}">${Number(finalPriority).toFixed(1)}/10 • ${getPriorityLabel(finalPriority)}</span>
      </div>
    </div>
    <div class="result-item">
      <div class="result-label">Critical Proximity</div>
      <div class="result-value">
        ${result.critical_proximity_flag
          ? '<span style="color:var(--danger);">⚠ Near hospital/school</span>'
          : '<span style="color:var(--success);">✓ Clear</span>'}
      </div>
    </div>
    <div class="result-item">
      <div class="result-label">Report ID</div>
      <div class="result-value" style="font-family:monospace;font-size:12px;">${(result.report_id || '').slice(0, 8)}...</div>
    </div>
    <div class="result-item">
      <div class="result-label">Status</div>
      <div class="result-value" style="text-transform:capitalize;">${result.status || ''}</div>
    </div>
  `;

  const manualReviewNote = document.getElementById('manual-review-note');
  if (result.requires_manual_review) {
    // If this was rejected by the CLIP OOD guard (ai/ood_guard.py), show
    // the specific reason it gave instead of the generic message - e.g.
    // "this looks like a cat" is far more useful to a citizen than a
    // vague "AI wasn't confident enough." Falls back to the generic
    // message if ood_reason isn't present (e.g. a normal low-confidence
    // case that wasn't OOD-rejected, or if the OOD guard was disabled on
    // the backend for that request).
    manualReviewNote.textContent = result.ood_reason
      ? `${result.ood_reason} We've still saved your report so a team member can review it by hand — if you uploaded the wrong photo by mistake, feel free to submit again with the correct one.`
      : "Our AI wasn't confident enough to auto-classify this photo, so it's been flagged for a team member to review by hand — your report is still saved and will be triaged correctly.";
    manualReviewNote.style.display = 'block';
  } else {
    manualReviewNote.style.display = 'none';
  }

  const dupNote = document.getElementById('duplicate-note');
  if (result.possible_duplicates && result.possible_duplicates.length > 0) {
    const count = result.possible_duplicates.length;
    const closest = result.possible_duplicates[0];
    const dateText = closest?.created_at
      ? `, the closest submitted ${new Date(closest.created_at).toLocaleDateString()}`
      : '';
    dupNote.textContent = `Heads up — ${count === 1 ? 'a similar report already exists' : `${count} similar reports already exist`} nearby${dateText}. Your report has still been saved and will be reviewed — this just helps our team avoid dispatching the same crew twice.`;
    dupNote.style.display = 'block';
  } else {
    dupNote.style.display = 'none';
  }

  document.getElementById('report-form').querySelectorAll('.form-group, .gps-status, #submit-btn').forEach(el => el.style.display = 'none');
  document.getElementById('result-card').style.display = 'block';

  if (navigator.vibrate) navigator.vibrate([50, 50, 50]);
}

function resetForm() {
  upload.file = null;
  document.getElementById('file-input').value = '';
  document.getElementById('upload-placeholder').style.display = 'block';
  document.getElementById('upload-preview-container').style.display = 'none';
  document.getElementById('description').value = '';
  document.getElementById('result-card').style.display = 'none';
  document.getElementById('error-toast').style.display = 'none';
  document.getElementById('report-form').querySelectorAll('.form-group, .gps-status, #submit-btn').forEach(el => el.style.display = '');
  updateSubmitEnabled();
}

// ---------------------------------------------------------------------------
// Form submit (was handleSubmit in ReportForm.jsx)
// ---------------------------------------------------------------------------
async function handleSubmit(e) {
  e.preventDefault();

  const errorToast = document.getElementById('error-toast');
  const errorMsg = document.getElementById('error-toast-message');

  if (!upload.file) {
    errorMsg.textContent = 'Please add a photo of the issue';
    errorToast.style.display = 'flex';
    return;
  }
  if (!geo.position) {
    errorMsg.textContent = 'Location not available. Please enable location services.';
    errorToast.style.display = 'flex';
    return;
  }

  submitting = true;
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
    renderResult(result);
  } catch (err) {
    errorMsg.textContent = err.message || 'Failed to submit report. Please try again.';
    errorToast.style.display = 'flex';
  } finally {
    submitting = false;
    btn.textContent = originalText;
    updateSubmitEnabled();
  }
}

// ---------------------------------------------------------------------------
// Wire everything up on load (was the App.jsx mount + hook auto-run)
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  setupImageUpload();
  document.getElementById('report-form').addEventListener('submit', handleSubmit);
  document.getElementById('gps-retry-btn').addEventListener('click', requestLocation);
  document.getElementById('new-report-btn').addEventListener('click', resetForm);

  requestLocation(); // auto-request on mount, same as useGeolocation's useEffect
  updateSubmitEnabled();
});