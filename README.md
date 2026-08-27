# 🏙️ CivicResolve
### *Automated Public Infrastructure Reporting System with Composite Risk Scoring*

<div align="center">

[![Live Demo](https://img.shields.io/badge/Live_Demo-Vercel-black?style=for-the-badge&logo=vercel)](https://civicresolve-wine.vercel.app)
[![API Docs](https://img.shields.io/badge/API_Docs-FastAPI-009688?style=for-the-badge&logo=fastapi)](https://civicresolve-01.onrender.com/docs)
[![Repository](https://img.shields.io/badge/Repository-GitHub-181717?style=for-the-badge&logo=github)](https://github.com/anirudh-ydv/CivicResolve-01)

</div>

---

## 📖 About

**CivicResolve** is a full-stack, AI-powered public infrastructure reporting platform designed to help citizens and municipalities **detect, classify, prioritize, and manage civic issues efficiently**.

Using **computer vision, geospatial intelligence, and a composite risk engine**, CivicResolve analyzes citizen-submitted images, identifies infrastructure problems such as potholes, broken streetlights, graffiti, illegal dumping, and damaged sidewalks, estimates visual severity, and generates a **priority score** to help authorities address the most critical issues first.

---

## ✨ Key Features

| Feature | Description |
|---|---|
| 🤖 **AI-Powered Image Classification** | Automatically categorizes infrastructure issues from user-uploaded images. |
| 🎯 **Composite Risk Engine** | Calculates a prioritized risk score using Visual Severity, Road Hierarchy Weights, and Critical Proximity. |
| 👥 **Role-Based Dashboards** | Separate experiences for Citizens and Administrators — see table below. |
| 🗺️ **Geospatial Mapping** | View all active reports on an interactive map using dynamically generated GeoJSON data. |
| 🔐 **Secure Authentication** | Unified JWT-based login routing for both Citizens and Admins using `PyJWT` and `bcrypt`. |

### 👥 Dashboard Roles

| | 👤 Citizen | 👨‍💼 Administrator |
|---|---|---|
| Access | Secure login | Secure login |
| Core action | Submit reports | Manage & prioritize reports |
| Media | Upload images | Override AI predictions (feedback loop) |
| Tracking | Track issue status | View aggregate/system-wide statistics |

---

## 🛠️ Tech Stack

<table>
<tr>
<td valign="top" width="33%">

### 🎨 Frontend
- HTML5
- CSS3
- JavaScript
- Hosted on **Vercel**

</td>
<td valign="top" width="33%">

### ⚙️ Backend
- FastAPI (Python)
- PostgreSQL (SQLAlchemy ORM)
- Hosted on **Render**

</td>
<td valign="top" width="33%">

### 🤖 AI / Machine Learning
- PyTorch
- Computer Vision
- Custom AI Inference Pipeline
- Composite Risk Scoring Algorithms

</td>
</tr>
</table>

---

## 📐 Technical Architecture

```mermaid
graph TD
    A["Frontend: HTML5, CSS3, JS"] -->|REST API| B["Backend: FastAPI, Python"]
    B -->|Database| C["PostgreSQL Database"]
    B -->|AI Pipeline| D["PyTorch Inference"]
    B -->|Risk Engine| E["Composite Risk Score"]
    B -->|Security| F["JWT & bcrypt Auth"]
    B -->|Hosting| G["Render Cloud Hosting"]
```

---
## ☁️ Deployment Architecture

CivicResolve uses a cloud-based three-tier deployment architecture:

| Component | Technology | Platform |
|---|---|---|
| 🎨 Frontend | HTML5, CSS3, Vanilla JavaScript | **Vercel** |
| ⚙️ Backend API | FastAPI + Python | **Render** |
| 🗄️ Database | PostgreSQL | **Neon** |
| 🤖 AI Inference | PyTorch | **Render Backend** |

---

## 🚀 Live Demo

### 🌐 Frontend Application
[https://civicresolve-wine.vercel.app](https://civicresolve-wine.vercel.app)

### ⚡ Backend API Documentation
[https://civicresolve-01.onrender.com/docs](https://civicresolve-01.onrender.com/docs)

> **Note:** The backend is hosted on Render's free tier and may take approximately 50 seconds to wake up after inactivity.

---

## 🔑 Demo Accounts

### 👨‍💼 Administrator
- **Username:** `admin`
- **Password:** `changeme123`

### 👤 Citizen
- **Email:** `test21@gmail.com`
- **Password:** `zxcv1234`

> ⚠️ **Demo credentials only.** Replace these credentials with secure production credentials before using the application in a real environment.

---

## ⚙️ Local Development Setup

### 1. Clone & Setup Backend

```bash
git clone https://github.com/anirudh-ydv/CivicResolve-01.git
cd CivicResolve-01/backend
python -m venv venv
# Windows: venv\Scripts\activate  |  Mac/Linux: source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment (`backend/.env`)

Create a `.env` file inside the `backend/` folder with your credentials:

```env
DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/civicresolve
CIVICRESOLVE_SECRET_KEY=your_super_secret_jwt_key
ADMIN_USERNAME=admin
ADMIN_PASSWORD=changeme123
```

### 3. Run the App

**Start Backend:**

```bash
uvicorn app.main:app --reload --port 8000
```

**Start Frontend:** Open `frontend1/app/index.html` using VS Code Live Server, or run:

```bash
cd frontend1/app && python -m http.server 3000
```

---

## 🧠 AI Pipeline

CivicResolve uses a computer vision pipeline to analyze uploaded infrastructure images.

```mermaid
graph TD
    A["Uploaded Image"] --> B["Image Preprocessing"]
    B --> C["PyTorch Model"]
    C --> D["Issue Classification"]
    C --> E["Severity Estimation"]
    D --> F["Confidence Check"]
    E --> F
    F --> G["Composite Risk Engine"]
    G --> H["Final Priority Score"]
    H --> I["Citizen / Admin Dashboard"]
```

### Supported Issue Categories
- `pothole`
- `broken_streetlight`
- `graffiti`
- `illegal_dumping`
- `cracked_sidewalk`
- `damaged_sign`
- `other`

---

## 🎯 Composite Risk Scoring

CivicResolve does not rely only on the AI classification. The final priority score considers multiple factors:

```text
                 Final Priority Score
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
   Visual Severity   Road Hierarchy   Critical Proximity
```

This enables authorities to prioritize reports based on potential public impact, rather than simply processing reports in the order they were submitted.

---

## 🗺️ Geospatial Intelligence

CivicResolve uses PostgreSQL + PostGIS to store and process geographical information.

The system can use:
- Latitude and longitude
- Infrastructure proximity
- Road hierarchy
- Critical locations
- GeoJSON-based report visualization

This allows the platform to identify issues that may require higher priority because they are located near important infrastructure.

---

## 📊 AI Dataset & Training

The AI training pipeline uses real image datasets organized into infrastructure issue categories.

The workflow includes:
1. Collecting real-world images.
2. Organizing images by issue category.
3. Generating a unified `manifest.csv`.
4. Creating training, validation, and test splits.
5. Training the PyTorch model.
6. Saving the trained model checkpoint.
7. Evaluating the model on a held-out test set.
8. Running inference on real images.

**Model checkpoint:** `backend/models/civicresolve_model.pth`

> **Note:** Severity labels currently use a bootstrap heuristic because the source datasets do not provide human-annotated severity scores. Severity predictions should therefore be treated as an experimental component until sufficient verified human feedback is collected.

---

## 🗂️ Project Structure

```text
CivicResolve-01/
│
├── backend/
│   ├── ai/
│   │   ├── data/
│   │   ├── model_pipeline.py
│   │   ├── evaluate.py
│   │   └── risk_engine.py
│   │
│   ├── app/
│   │   ├── main.py
│   │   ├── auth.py
│   │   └── auth_routes.py
│   │
│   ├── models/
│   │   ├── database.py
│   │   ├── report.py
│   │   └── user.py
│   │
│   ├── tests/
│   ├── uploads/
│   ├── requirements.txt
│   └── .env
│
├── frontend1/
│   └── app/
│       ├── index.html
│       ├── app.js
│       └── style.css
│
└── README.md
```

---

## 🔒 Security

Beyond the authentication covered in [Key Features](#-key-features), CivicResolve enforces:

| Measure | Purpose |
|---|---|
| 👥 Role-based authorization | Restricts actions/data by Citizen vs Administrator role |
| 🛡️ Environment-based secret management | Keeps credentials out of source control |
| 📁 File upload validation | Rejects malformed or unexpected uploads |
| 🖼️ Allowed image type restrictions | Only permits safe, expected image formats |
| 📦 Maximum upload size enforcement | Prevents oversized/abusive uploads |
| 👨‍💼 Manual review for uncertain AI predictions | Human check on low-confidence classifications |

---

## 📈 Future Improvements

- [ ] Expand real-world civic issue datasets
- [ ] Collect human-annotated severity labels
- [ ] Improve severity regression performance
- [ ] Improve out-of-distribution detection
- [ ] Add automated municipal department routing
- [ ] Add notification and escalation workflows
- [ ] Implement continuous learning from verified admin feedback
- [ ] Add multilingual citizen reporting
- [ ] Optimize AI inference for production
- [ ] Add advanced geospatial analytics
- [ ] Improve model generalization across different environments

---

## 🏆 Why CivicResolve?

Traditional civic reporting systems mainly answer:

> "What problem was reported?"

CivicResolve goes one step further:

> "What is the problem, how severe is it, and how urgently should it be addressed?"

By combining:

**AI Detection + Risk Scoring + Geospatial Intelligence + Citizen Reporting + Administrative Workflows**

CivicResolve transforms scattered citizen reports into actionable infrastructure intelligence.

---

## ⭐ Support the Project

If you find CivicResolve useful, consider giving the repository a ⭐ on GitHub.

**🔗 Repository:** [https://github.com/anirudh-ydv/CivicResolve-01](https://github.com/anirudh-ydv/CivicResolve-01)

**🌐 Live Application:** [https://civicresolve-wine.vercel.app](https://civicresolve-wine.vercel.app)

**⚡ API Documentation:** [https://civicresolve-01.onrender.com/docs](https://civicresolve-01.onrender.com/docs)