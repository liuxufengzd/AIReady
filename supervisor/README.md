# Supervisor

An AI-powered document Q&A service that answers natural-language questions by decomposing them into sub-queries, resolving each in parallel with a ReAct agent, and synthesizing a final answer.

## Architecture

```
POST /query
    │
    ▼
Query Decomposition (LLM)
    │
    ▼
Sub-questions (parallel)
    │
    ├── ReAct Agent ──► search_domain_knowledge (gRPC → Search service)
    │
    ▼
Synthesize Answer (LLM)
    │
    ▼
Final Answer (string)
```

## API

### `POST /query`

Answers a user question scoped to a project's knowledge base.

**Request body**

```json
{
  "project": "my-project",
  "query": "What are the check-in times and is parking free?",
  "filters": { "category": "hotel-policy" }
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `project` | `string` | yes | Project identifier used to scope knowledge searches |
| `query` | `string` | yes | Natural-language question |
| `filters` | `object` | no | Key/value metadata filters applied to knowledge searches |

**Response**

Plain text answer (`200 OK`).

## Setup

### Prerequisites

- Python 3.11+
- Running [Search service](../search/README.md) (gRPC)
- Google Serper API key for web search

### Environment variables

Create a `.env` file in the `supervisor/` directory:

```env
GOOGLE_API_KEY=...
SEARCH_API_URL=localhost:50051
```

### Install dependencies

```bash
pip install -r requirements.txt
```

### Run

```bash
python -m supervisor.main
```