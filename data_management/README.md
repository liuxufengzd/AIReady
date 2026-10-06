install dependencies
```bash
pip install -r data_management/requirements.txt
```

run
```bash
python -m data_management.main
```

Open http://localhost:8003/ for the console (code in `frontend/`):

| Page | What it does |
|---|---|
| Files | Browse `store/s3/raw` with type, size, and modified time. Each file shows its state: extracting, failed, reviewing, indexing, ready. Select files with no state (or failed) to queue extraction. |
| Review | Files of the selected project waiting for review. Open one to approve or reject the parse. |
| Ready | Published, indexed documents of the project: the reviewed markdown, every chunk with its token count, and Delete, which removes the index entries and `processed/{project}/{filename}/` so the raw file has no state again. |
| Assistant | Ask the supervisor about the project's documents. |

The console also needs:
- dataprep (`dg dev`) — `extraction_request_sensor` starts extraction for the files queued here. New raw files are not extracted until they are queued.
- search (`python -m search.main`) — Delete calls its `Delete` RPC.
- supervisor (`python -m supervisor.main`) for the Assistant page.

environment
```env
API_BASE_URL=http://localhost:8003        # how the browser reaches this service
SUPERVISOR_API_URL=http://localhost:8002  # how the browser reaches the supervisor
SEARCH_API_URL=localhost:50051
```
