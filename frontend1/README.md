# CivicResolve Frontend — Vanilla HTML/CSS/JS

This is a plain HTML/CSS/JS port of the original React (`Frontend/citizen`,
`Frontend/admin`) apps. No build step, no npm install, no framework —
each app is just `index.html` + `style.css` + `app.js`, served as static
files. Every feature from the React version is preserved 1:1; nothing was
simplified or dropped. See "What was ported" below for the exact mapping.

## Running it

The backend's CORS policy only allows `http://localhost:5173` (citizen)
and `http://localhost:5174` (admin) as origins — the same ports the
original Vite dev servers used — so serve these on the same ports with any
static file server. Two easy options:

**Python (already installed if you've been running the backend):**
```bash
# terminal 1
cd Frontend-vanilla/citizen
python3 -m http.server 5173

# terminal 2
cd Frontend-vanilla/admin
python3 -m http.server 5174
```

**Node (if you have `serve` or similar installed):**
```bash
npx serve -l 5173 Frontend-vanilla/citizen
npx serve -l 5174 Frontend-vanilla/admin
```

Then open:
- Citizen portal: http://localhost:5173
- Admin dashboard: http://localhost:5174

The backend must be running on `http://localhost:8000` as usual
(`uvicorn app.main:app --host 0.0.0.0 --port 8000` from `backend/`).

If you deploy this somewhere other than localhost, update `API_BASE` at
the top of each `app.js` file to point at your real backend URL, and add
that origin to `allow_origins` in `backend/app/main.py`'s CORS middleware.

## What was ported (and where to find it)

| React version | Vanilla version | What it does |
|---|---|---|
| `citizen/src/hooks/useGeolocation.js` | `citizen/app.js` — `requestLocation()`, `renderGpsStatus()` | Auto-requests GPS on load, shows status/accuracy, retry on error |
| `citizen/src/components/ImageUpload.jsx` | `citizen/app.js` — `setupImageUpload()`, `handleFileSelect()` | Drag/drop + click upload, type/size validation, preview, remove |
| `citizen/src/hooks/useApi.js` | `citizen/app.js` — `submitReport()`, `getDeviceId()` | POSTs the report, manages the anonymous device ID |
| `citizen/src/components/ReportForm.jsx` | `citizen/app.js` — `handleSubmit()`, `renderResult()` | Form validation, submit flow, result card (severity, priority, duplicates, manual-review note) |
| `admin/src/hooks/useApi.js` | `admin/app.js` — `apiFetch()`, `login()`, `logout()` | Bearer token storage, auth header injection, 401 handling, all API calls |
| `admin/src/hooks/useReports.js` | `admin/app.js` — `fetchData()`, `startPolling()`, `changeStatus()`, `confirmPending()`, `reclassifyReport()` | 30s polling, filters, status changes, human-review confirm/reclassify |
| `admin/src/components/Login.jsx` | `admin/index.html` (`#login-container`) + `admin/app.js` login handler | Sign-in form, error display |
| `admin/src/components/StatsCards.jsx` | `admin/app.js` — `renderStatsCards()` | Total/open/in-progress/resolved counts + avg priority |
| `admin/src/components/ReportsTable.jsx` | `admin/app.js` — `renderReportsTable()`, `reviewActionsHtml()`, `reclassifyFormHtml()`, `needsReviewBadgeHtml()` | Priority-sorted table, inline Confirm/Fix actions, needs-review badges, status dropdown |
| `admin/src/components/MapView.jsx` (+`MapLegend`) | `admin/app.js` — `initMap()`, `renderMap()`, `renderMapLegendHtml()` | Leaflet map, priority-colored markers, popups, click-to-select synced with the table, clickable legend filter |
| `App.jsx` (both apps) — hook ordering, auth gating, loading states | `admin/app.js` — `renderAll()`, `showLogin()`/`showLoading()`/`showDashboard()` | Same screen-state logic as the React version (this is also where the earlier hooks-order bug lived — not a concern here since there's no hooks system, but the *screen flow* — login → loading → dashboard — is preserved exactly) |

The CSS (`style.css` in each app) is copied over completely unchanged —
it was already plain CSS, not React-specific, so the glassmorphism theme,
light sky-blue background, and all severity color coding look identical.

## Why this doesn't need a build step

The React version used Vite only for JSX compilation and dev-server
proxying — there's no other framework dependency doing real work (no
routing library, no state management library). Once JSX is removed and
the API calls point at an absolute `http://localhost:8000` URL instead of
a relative `/api` path (which relied on Vite's dev proxy), there's nothing
left that requires a build step. `fetch`, `FormData`, and the Geolocation
API are all native browser APIs.

## Known trade-off vs. the React version

The React version re-rendered only the parts of the DOM that changed
(React's diffing). This vanilla version re-renders larger chunks of HTML
via `innerHTML` on each state change (e.g. the whole reports table
re-renders on every poll). At the data volumes this project handles
(dozens to low hundreds of reports on screen at once), this has no
noticeable performance impact — but it's worth knowing if the report
volume ever grows into the thousands, at which point a smarter diffing
approach would be worth revisiting.
