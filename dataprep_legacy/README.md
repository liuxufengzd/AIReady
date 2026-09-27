# Data Extraction Workflow — Frontend

A browser-based HITL (Human-in-the-Loop) frontend for the DataExtractor API.

## Running

### 1. Start the data backend

```bash
# From the repo root
mineru-kit api-server --host 127.0.0.1 --port 8000
python -m data.main
```

Office files are converted to PDF with LibreOffice before parsing. Install LibreOffice and make sure `soffice` is on `PATH`. Accepted Office suffixes are Word (`.doc .docx`), Excel (`.xls .xlsx`), and PowerPoint (`.ppt .pptx`).

### 2. Open the data frontend

Open `http://localhost:8001` (the host and port come from `HOST` and `PORT`).

The page calls the API at `API_BASE_URL` (default `http://localhost:8001`). Set that variable in `data/.env` when the API is not on the same origin as the page.

## Workflow

```
[1. Setup]  →  [2. Content Review]  →  [3. Final Review]  →  [✓ Complete]
```

An Office file is converted to one PDF, then parsed by MinerU and reviewed once.

| Step | What happens |
|------|-------------|
| **Setup** | Enter project name and source path. Calls `POST /start_extraction`. |
| **Content Review** | The MinerU text is shown with a markdown preview and, when available, a layout PDF of detected page regions. You can edit the text, approve or reject, and optionally enable chunking. Rejecting falls back to VLM extraction. Calls `POST /continue_extraction`. |
| **Final Review** | Edit the keyword search texts, semantic search texts, and metadata JSON. Calls `POST /post_extraction`. |
| **Complete** | `post_extraction` returns `null` and the job is done. |

## No build step required

The frontend is a single `index.html` using React, Babel, Tailwind, and Marked via CDN.
