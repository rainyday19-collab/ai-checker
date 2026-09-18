# AI Checker

An AI-assisted assessment platform that evaluates student work against a mark scheme and provides structured feedback.

**Portfolio MVP:** real AI-assisted grading is available through the OpenAI API. The default `mock` mode returns clearly labeled demo data without credentials or paid API usage; `openai` mode requires backend credentials and incurs API usage.

## Overview

Teachers need to compare student answers with grading criteria and explain why marks were awarded or lost. AI Checker brings uploads, structured feedback, and assessment history into one interface. This Computer Science portfolio project implements that workflow through a small, separated frontend and backend. AI-generated marks should be reviewed by a teacher.

## Features

- Upload student work and mark schemes as PDF, PNG, or JPG/JPEG, or paste manual criteria.
- Validate file types, sizes, PDF readability, and image contents.
- Extract selectable PDF text and send supported images directly as multimodal inputs; no OCR yet.
- Display structured results with decimal marks, calculated percentages, feedback, and evidence.
- Save results in SQLite; browse, open, and delete assessment history.
- View dashboard statistics and recent performance bars in a responsive React interface.
- Grade against supplied mark schemes or criteria using OpenAI Structured Outputs, or use safe mock grading.
- Display model and input/output/total token metadata from the grading response.

Uploaded file contents and manual criteria text are not persisted. Only filenames, assessment metadata, and results are saved.

## Screenshots

These screenshots show the portfolio MVP in `GRADING_MODE=mock`. Assessment scores and feedback are fixed demo data, not AI evaluations of uploaded work.

### Dashboard

![Dashboard showing the empty assessment overview](docs/screenshots/dashboard.png)

### New Assessment

![New assessment workspace with uploads and manual criteria](docs/screenshots/new-assessment.png)

### Assessment Results

![Demo assessment result with marks, feedback, and example evidence](docs/screenshots/assessment-results.png)

### History

![History showing saved demo assessments](docs/screenshots/history.png)

## Tech Stack

| Layer | Technologies |
| --- | --- |
| Frontend | React, Vite, JavaScript, plain CSS |
| Backend | Python, FastAPI, Pydantic, built-in SQLite, pypdf, Pillow |
| AI service | Official OpenAI Python SDK, Structured Outputs, image inputs and extracted PDF text |

## Architecture

```text
Browser / React + Vite
          |
          v
    FastAPI REST API
          |
          v
   Document Processing
          |
          v
    Assessment Service
          |
          v
        SQLite
```

The assessment service prepares text or image inputs and selects a grader by `GRADING_MODE`. `mock` returns fixed demo results; `openai` makes one structured grading request with automatic retries disabled. Only successfully validated assessments are saved. History and dashboard endpoints read saved SQLite records directly.

## Project Structure

```text
ai-checker/
├── frontend/
│   ├── src/                   # Interface, uploads, results, dashboard, history
│   ├── .env.example
│   └── package.json
├── backend/
│   ├── main.py                # REST endpoints and startup
│   ├── document_processing.py # PDF extraction and image validation
│   ├── grading.py             # Result models and input preparation
│   ├── assessment_service.py  # Mock grader and mode selection
│   ├── ai_grading.py          # OpenAI grading, usage metadata, and safe errors
│   ├── database.py            # SQLite persistence
│   ├── test_*.py              # Backend tests
│   ├── .env.example
│   └── requirements.txt
├── docs/screenshots/          # Application screenshots
├── .gitignore
├── LICENSE
└── README.md
```

## Running Locally

Prerequisites: Python 3.10+ (3.12 recommended), Node.js 22.12+ with npm, and two PowerShell terminals. Start both terminals in the project root.

**Terminal 1 - backend:**

```powershell
cd backend
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000
```

If `py` is unavailable, use `python -m venv .venv` with an installed Python interpreter. No virtual environment activation or PowerShell execution-policy change is needed. Copy environment examples only on first setup; keep any existing local configuration.

**Terminal 2 - frontend:**

```powershell
cd frontend
npm install
Copy-Item .env.example .env
npm run dev -- --port 5173 --strictPort
```

Open [AI Checker](http://localhost:5173). Check [backend health](http://localhost:8000/health) or explore [API docs](http://localhost:8000/docs). The database is created automatically at `backend/data/ai_checker.db` and is excluded from Git. Development CORS allows localhost and 127.0.0.1 on port 5173.

## Environment Variables

Backend `.env`:

```dotenv
GRADING_MODE=mock
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.6-luna
```

Mock mode needs no OpenAI credentials. To enable paid grading, set `GRADING_MODE=openai`, configure `OPENAI_API_KEY` privately in the backend environment, and choose `OPENAI_MODEL`. Credentials remain backend-only. Environment variables override `.env` values; restart the backend after configuration changes.

Frontend `.env`:

```dotenv
VITE_API_URL=http://localhost:8000
```

Restart Vite after changing this value. `VITE_*` values are exposed to the browser, so never put an API key there. Real `.env` files are ignored; `.env.example` files are safe to commit.

## Testing

From the project root, run backend tests:

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -v
```

Tests use temporary databases and mocked OpenAI transports; no real API requests or credentials are needed.

In a separate terminal from the project root, build the frontend:

```powershell
cd frontend
npm run build
```

For a manual check in mock mode, submit sample work with criteria, view the demo result, find it in History, and verify Dashboard updates after saving or deleting it. In OpenAI mode, each submission uses the API; token metadata is available in the result's AI usage section.

## Current Status

This is a portfolio MVP with real AI grading available and mock mode as the safe default. Upload limits are 10 MB per file, 50 PDF pages, and 20 megapixels per image. Scanned or low-text PDFs are currently rejected in OpenAI mode: upload a clear PNG/JPG image or a PDF with selectable text instead. With no authentication, all assessments share one local history; the app is intended for local development, not production use.

## Future Improvements

- Expand grading quality checks and support for scanned PDFs.
- Add authentication and separate user histories.
- Deploy the frontend and backend.
- Add optional scalability improvements as usage grows.

Licensed under the [MIT License](LICENSE).
