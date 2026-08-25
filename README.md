# 🏙️ CivicResolve

CivicResolve is a full-stack, AI-powered platform designed to automatically detect, classify, and manage civic infrastructure issues. By utilizing computer vision and a dedicated risk engine, it identifies problems such as potholes, illegal dumping, graffiti, and broken streetlights, streamlining the resolution process for modern smart cities.

## ✨ Key Features
*   **AI-Powered Detection:** Custom machine learning pipelines to classify civic issues (cracks, graffiti, illegal dumping, potholes, etc.).
*   **Intelligent Risk Engine:** Evaluates the severity and priority of reported issues using `risk_engine.py`.
*   **Out-of-Distribution Guard:** Ensures model reliability by filtering anomalous data via `ood_guard.py`.
*   **Full-Stack Architecture:** Cleanly separated React frontend and Python backend.
*   **Containerized Environment:** Fully dockerized with `docker-compose` for quick, unified deployment.

## 🛠️ Tech Stack
*   **Frontend:** React.js
*   **Backend:** Python (FastAPI/Flask/Django)
*   **AI/ML:** Python (TensorFlow/PyTorch, Scikit-Learn)
*   **Infrastructure:** Docker, Docker Compose

## 📂 Project Structure
The repository is strictly organized into decoupled frontend and backend environments:

```text
CivicResolve/
├── backend/                   # Python Backend & AI Pipelines
│   ├── ai/                    # AI scripts and training models
│   │   ├── data/raw/          # Datasets (potholes, graffiti, etc.)
│   │   ├── build_manifest.py  # Data preparation 
│   │   ├── evaluate.py        # Model evaluation metrics
│   │   ├── model_pipeline.py  # Core ML training & inference pipeline
│   │   ├── ood_guard.py       # Out-of-distribution detection
│   │   └── risk_engine.py     # Priority scoring algorithm
│   ├── app/                   # Backend application logic
│   ├── requirements.txt       # Python dependencies
│   └── Dockerfile             # Backend container configuration
├── frontend1/                 # React Frontend
│   └── (UI source code)
├── docker-compose.yml         # Multi-container orchestration
└── README.md                  # Project documentation