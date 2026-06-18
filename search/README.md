# Search Microservice

A gRPC microservice that provides document indexing and hybrid retrieval capabilities. It combines semantic search (ChromaDB + Gemini embeddings) with keyword search (Elasticsearch) and uses Reciprocal Rank Fusion (RRF) to merge results, with an optional cross-encoder reranking stage.

---

## Architecture

```
Client (gRPC)
     │
     ▼
SearchServiceServicer          ← grpc_server.py
     ├── Store(StoreRequest)    → Importer  → SemanticClient (Chroma)
     │                                      → KeywordClient  (Elasticsearch)
     └── Query(QueryRequest)   → Retriever → SemanticClient (Chroma)
                                           → KeywordClient  (Elasticsearch)
                                           → Reranker (RRF + optional cross-encoder)
```

## gRPC API

Defined in [`grpc_protos/search/search.proto`](../grpc_protos/search/search.proto).


### Install dependencies

```bash
pip install -r search/requirements.txt
```

### Start the server

Run from the **workspace root** (required so that `grpc_protos` and `common` packages are importable):

```bash
python -m search.main
```

### Regenerate protobuf stubs

Only needed when `grpc_protos/search/search.proto` changes:

```bash
python search/scripts/generate_proto.py
```

---

## Debugging with grpcurl

Enable reflection (`ENABLE_REFLECTION=true`), then:

```bash
# List all services
grpcurl -plaintext localhost:50051 list

# Describe the Search service
grpcurl -plaintext localhost:50051 describe search.SearchService

# Send a query
grpcurl -plaintext -d '{"project":"my_project","query":"what is RAG?"}' \
  localhost:50051 search.SearchService/Query

# Store documents
grpcurl -plaintext -d '{"project":"my_project","metadata_file_names":["doc.json"]}' \
  localhost:50051 search.SearchService/Store
```

---

## Health Checks

The service exposes the standard gRPC health protocol for Kubernetes probes:

| Service name | Probe type |
|---|---|
| `""` (empty) | Liveness — overall server health |
| `search.SearchService` | Readiness — search service availability |

```bash
grpcurl -plaintext localhost:50051 grpc.health.v1.Health/Check
```
