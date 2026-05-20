# Data Extraction Workflow — Frontend

A browser-based HITL (Human-in-the-Loop) frontend for the DataExtractor API.

## Running

### 1. Start the backend

```bash
# From the repo root
mineru-api --host localhost --port 8001
uvicorn data.main:api --reload --port 8000
```

### 2. Open the frontend

Just double-click `frontend/index.html` or drag it into your browser.

## Workflow

```
[1. Setup]  →  [2. Content Review]  →  [3. Final Review]  →  [✓ Complete]
                       ↑                       |
                       └────── PPT loop ───────┘
```

| Step | What happens |
|------|-------------|
| **Setup** | Enter project name, source path, and (optional) languages. Calls `POST /start_extraction`. |
| **Content Review** | The extracted text is shown with a markdown preview. You can edit it, approve or reject, and optionally enable chunking. Calls `POST /continue_extraction`. |
| **Final Review** | Edit the keyword search texts, semantic search texts, and metadata JSON. Calls `POST /post_extraction`. |
| **Complete** | If `post_extraction` returns `null`, the job is done. If it returns a `ReviewRequest` (PPT next slide), loops back to **Content Review**. |

## No build step required

The frontend is a single `index.html` using React, Babel, Tailwind, and Marked via CDN.
