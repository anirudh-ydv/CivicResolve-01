# CivicResolve 🏙️

**Automated Public Infrastructure Reporting System**  
* Productivity & Social Good*

## Overview

CivicResolve streamlines how citizens report and city managers prioritize public infrastructure issues using AI-powered computer vision and composite risk scoring.

### The Problem
- Citizens struggle with clunky reporting apps
- Cities lack automated triage for incoming reports
- Critical issues (deep potholes, broken streetlights) get buried

### The Solution
1. **Citizen Portal** - Mobile-first, one-tap reporting with auto-GPS
2. **AI Vision Pipeline** - Classifies issue type + scores visual severity (1-10) from photos
3. **Composite Risk Engine** - Calculates Final Priority Score using: (Visual Severity × 0.5) + (Road Hierarchy × 0.3) + (Critical Proximity × 0.2)
4. **Admin Dashboard** - Priority-sorted map & table for data-driven crew dispatch

> **Before trusting any AI output from this system, read [Current Limitations](#current-limitations-honest-as-of-the-part-0-6-migration) below** - the model's real, measured accuracy (58.3%) and specific known failure modes are documented there with evidence, not glossed over.

## Quick Start

```bash
# Clone and enter
cd civicresolve

# Launch all services (Docker)
docker-compose up --build

# Access:
# - Citizen Portal: http://localhost:5173
# - Admin Dashboard: http://localhost:5174
# - API Docs: http://localhost:8000/docs
```

## Authentication

The **Admin Dashboard** requires authentication. The **Citizen Portal** is fully public.

### Default Dev Credentials
```
Username: admin
Password: changeme123
```
These are seeded automatically on first backend startup via `ADMIN_USERNAME`/`ADMIN_PASSWORD` environment variables.

### Login Flow
1. Open http://localhost:5174 (Admin Dashboard)
2. Enter credentials on the glassmorphism login screen
3. JWT token is stored in `localStorage` and attached to all API requests
4. Session persists across refreshes; click "Sign Out" to clear

⚠️ **Production Warning**: Before any real deployment, **must** change:
- `CIVICRESOLVE_SECRET_KEY` — use a strong random string (32+ chars)
- `ADMIN_PASSWORD` — use a secure password
- Set `DATABASE_URL` to PostgreSQL with PostGIS

See `.env.example` for all configurable variables.

## Composite Risk Scoring Engine

The core innovation is the **Composite Risk Engine** that calculates a Final Priority Score (1-10) using:

```
Final Risk Score = (Visual Severity × 0.5) + (Road Hierarchy Weight × 0.3) + (Critical Proximity × 0.2)
```

### Components:
- **Visual Severity (1-10)**: Derived from computer vision model analyzing uploaded photos
- **Road Hierarchy Weight**: Arterial Highway=10, Secondary=7, Residential=4 (determined from GPS)
- **Critical Proximity**: Within 200m of hospital/school = 10, else 0

This ensures that a moderate pothole on a major artery near a hospital ranks higher than a severe pothole on a quiet residential street.

## Architecture

```
┌─────────────┐     HTTPS/REST      ┌──────────────────┐
│   Citizen   │ ──────────────────► │    FastAPI       │
│   Portal    │  Image + GPS + Text │    Backend       │
│  (React)    │                     │                  │
└─────────────┘                     │  ┌────────────┐  │
                                    │  │  AI Model  │  │
                                    │  │ (ResNet50) │  │
                                    │  └────────────┘  │
                                    │        │         │
                                    │        ▼         │
                                    │  ┌────────────┐  │
                                    │  │  Risk      │  │
                                    │  │  Engine    │  │
                                    │  └────────────┘  │
                                    │        │         │
                                    │        ▼         │
                                    │  ┌────────────┐  │
                                    │  │  SQLite/   │  │
                                    │  │  PostgreSQL│  │
                                    │  └────────────┘  │
                                    └────────┬─────────┘
                                             │
                                     ┌────────▼─────────┐
                                     │   Admin Dashboard│
                                     │   (React+Leaflet)│
                                     └──────────────────┘
```

## Tech Stack

| Layer | Technology |
|-------|------------|
| **Frontend (Citizen)** | React 18 + Vite + TypeScript-ready |
| **Frontend (Admin)** | React 18 + Leaflet.js (free maps) |
| **Backend** | FastAPI + SQLAlchemy 2.0 |
| **AI/ML** | PyTorch + ResNet50 (dual-head) |
| **Risk Engine** | Python (haversine distance, road classification) |
| **Database** | SQLite (dev) → PostgreSQL + PostGIS (prod) |
| **Deployment** | Docker Compose |

## Key Features

### 🎯 Citizen Portal (`/`)
- Camera/gallery image capture
- Automatic GPS geolocation
- One-field description + submit
- Instant AI feedback (category + visual severity + final priority score)
- PWA-ready (installable on mobile)

### 🧠 AI Vision Pipeline
- **Classification**: 7 infrastructure categories (Pothole, Broken Streetlight, Graffiti, Illegal Dumping, Cracked Sidewalk, Damaged Sign *(limited AI support — zero training data, see Current Limitations)*, Other)
- **Visual Severity Scoring**: Visual damage assessment → 1-10 priority score
- **Transfer Learning**: ResNet50 backbone + dual heads
- **Real Training Data**: Trained on 1,448 real images across 6 of 7 categories (no synthetic/mock data) — see [Current Limitations](#current-limitations-honest-as-of-the-part-0-6-migration) and `backend/ai/data/TRAINING_REPORT.md` for honest, measured accuracy and known failure modes

### 🧮 Composite Risk Engine
- **Visual Severity**: From CV model (50% weight)
- **Road Hierarchy**: GPS-based road classification (30% weight)
- **Critical Proximity**: Haversine distance to hospitals/schools (20% weight)
- **Final Priority Score**: Composite 1-10 score for dispatch prioritization

### 🗺️ Admin Dashboard (`/admin`)
- Leaflet.js map with color-coded pins by Final Priority Score (Red 8-10, Yellow 4-7, Green 1-3)
- Real-time GeoJSON feed from API
- Sortable table (Final Priority Score DESC by default)
- Multi-filter: category, status, priority tier
- Detailed popups showing all risk components
- Inline status updates (Open → In Progress → Resolved)

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Liveness check |
| `POST` | `/api/auth/login` | Admin login (JWT) |
| `POST` | `/api/report/submit` | Submit new report (multipart); always created as `pending_review` |
| `GET` | `/api/reports` | Paginated list with filters |
| `GET` | `/api/reports/{id}` | Single report lookup |
| `GET` | `/api/reports/geojson` | Map-ready GeoJSON |
| `GET` | `/api/reports/stats` | Dashboard statistics |
| `PATCH` | `/api/reports/{id}/status` | Update report status (dispatch workflow: open → in_progress → resolved) |
| `PATCH` | `/api/reports/{id}/confirm` | Admin confirms the AI's category/severity were correct |
| `PATCH` | `/api/reports/{id}/review` | Admin corrects category/severity; logged to `training_feedback` for retraining (Part 4) |

## Development

### Backend
```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m ai.model_pipeline  # Train on real data - see backend/ai/data/README.md for dataset setup
uvicorn app.main:app --reload
```

### Frontends
```bash
# Citizen Portal
cd Frontend/citizen && npm install && npm run dev

# Admin Dashboard
cd Frontend/admin && npm install && npm run dev
```

## Project Structure

```
civicresolve/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI routes
│   │   └── auth.py          # JWT authentication
│   ├── models/
│   │   ├── database.py      # SQLAlchemy setup
│   │   ├── report.py        # Report model with risk fields
│   │   └── user.py          # Admin user model
│   ├── ai/
│   │   ├── model_pipeline.py # Dual-head ResNet50
│   │   ├── risk_engine.py    # Composite risk scoring
│   │   └── data/              # manifest.csv, dataset README, TRAINING_REPORT.md
│   └── tests/
│       ├── test_integration.py   # live-stack HTTP integration tests
│       └── test_report_model.py  # DB-level unit test (status default, etc.)
├── Frontend/
│   ├── citizen/             # Citizen reporting portal
│   │   ├── src/
│   │   │   ├── components/  # ReportForm, ImageUpload
│   │   │   └── hooks/       # useGeolocation, useApi
│   │   └── public/          # PWA manifest, icons
│   └── admin/               # Admin dashboard
│       ├── src/
│       │   ├── components/  # MapView, ReportsTable, StatsCards, Login
│       │   └── hooks/       # useApi, useReports
│       └── public/
├── docker-compose.yml
├── .env.example
├── DEPLOYMENT.md
└── README.md
```

## Current Limitations (honest, as of the Part 0-6 migration)

This section exists because a prior pass claimed things worked without verifying them. Everything below is backed by actual command output, metrics, or screenshots produced while building it - see `backend/ai/data/TRAINING_REPORT.md` for the full detail behind the AI numbers.

### AI model
- **Trained on 1,448 real photos** across 6 of 7 categories (not synthetic images) - see `backend/ai/data/README.md` for exact sources. `cracked_sidewalk` (77 images) and `illegal_dumping` (137 images, a domain-mismatched proxy dataset) are below the target sample size.
- **`damaged_sign` has zero training images.** No reachable real dataset was found from this environment (Kaggle/HuggingFace/Zenodo are outside the deployment sandbox's typical network allowlist during development) - the model will never confidently predict this class. This is a real gap, not a rounding error.
- **Test-set accuracy: 58.3%** (126/216), measured on a held-out split never seen during training. Per-category breakdown, confusion matrix, and precision/recall/F1 are in `TRAINING_REPORT.md`. `cracked_sidewalk` has **0% recall** - every test example was misclassified.
- **No ImageNet-pretrained weights were used** - training used a frozen, randomly-initialized backbone (only `layer4` + heads fine-tuned) rather than real transfer learning, which caps achievable accuracy well below what a properly pretrained model would reach.
- **The severity regression head collapsed** during training and currently outputs close to the minimum for most inputs - severity scores should not be trusted as-is until a retrain fixes this (higher severity loss weight, more epochs, real pretrained features).
- **Confidence gating (40% threshold) does not catch confidently-wrong predictions** - only genuinely uncertain ones. A real test photo of a cat was classified as `broken_streetlight` at 75.1% confidence, well above the gating threshold. Below-threshold predictions are returned as `category: "unclassified"` with `requires_manual_review: true` rather than presented as an authoritative guess.

### Human review loop
- New reports default to `pending_review`, not an actionable dispatch status, until an admin explicitly confirms or corrects them (`PATCH /api/reports/{id}/confirm` or `/review`). Every correction is logged to `training_feedback` for future retraining.
- The retraining pipeline (`backend/models/export_feedback_for_retraining.py`) is real and tested, but intentionally manual - merging exported feedback into a new training run is a human decision, not automated. Recommended cadence: wait for 50+ corrected rows before retraining (see `MIN_ROWS_FOR_RETRAIN` in the export script) - a handful of corrections would have outsized, noisy influence relative to the ~1,448-image bootstrap set.

### Database / spatial queries
- Postgres + PostGIS is now the only supported database - SQLite was fully removed, not left as a fallback. `reports.location`, `critical_infrastructure.location`, and `road_segments.geom` are real `geography(Point/LineString, 4326)` columns, and `ST_Distance`/`ST_DWithin` queries run at the database level (see `backend/ai/risk_engine.py`).
- Critical infrastructure (hospitals/schools) and road segments are seeded with **real, named San Francisco locations** (`backend/models/seed_geo_data.py`), but road segment coverage is a small hand-picked sample (8 segments), not a full road network import - no OSM/GIS data source was reachable during development. Most coordinates outside the seeded segments' immediate vicinity correctly return `road_type: "unknown"` rather than a guessed default.
- Duplicate-report detection (`ST_DWithin`, 20m radius) is new functionality, not a migration of prior code - a search of the codebase found no pre-existing duplicate-clustering logic to migrate from. The backend has returned real `possible_duplicates` data in the submit response since this was built, but the citizen portal never displayed it until this pass - it's now surfaced as an informational note on the submit result screen (`Frontend/citizen/src/components/ReportForm.jsx`).

### Testing
- `backend/tests/test_integration.py` covers the full submit→classify→score→store flow, the auth flow (login, protected routes, token expiry), and the duplicate-detection boundary at exactly 19m/20m/21m, run against a real live backend and Postgres/PostGIS instance - 13 passed, 1 skipped (a model-uncertainty guard, not a failure). Run with `pytest tests/test_integration.py -v` against a running backend.
- These are integration tests against a live stack, not unit tests with mocks - there is currently no unit test coverage for individual functions in isolation.

## Production Roadmap

- [x] PostgreSQL + PostGIS for spatial queries
- [x] Real (non-synthetic) training data
- [x] Human-in-the-loop review before dispatch
- [x] Feedback loop for retraining
- [ ] ImageNet-pretrained backbone (blocked in dev by network access to `download.pytorch.org` - should be trivial in a real deployment environment)
- [ ] Fix the collapsed severity regression head
- [ ] Full road network import (currently 8 hand-seeded segments)
- [ ] `damaged_sign` training data
- [ ] Celery + Redis for async AI inference
- [ ] S3/MinIO for image storage
- [ ] OAuth2/OIDC municipal SSO
- [ ] PWA offline submission queue
- [ ] WebSocket real-time updates
