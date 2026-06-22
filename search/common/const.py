import os
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent.parent.resolve()

# Retriever
TOP_K = 5
# The smoothing constant, its function is to reduce the extreme weight brought by high ranks (such as rank = 1). If your recall list is short,
# or you want the weight of rank = 1 to be higher, you can try to reduce K (for example, K = 20). Keeping K = 60 is usually a safe starting point.
RRF_SMOOTHING_CONSTANT = 20

# Elasticsearch
NUMBER_OF_SHARDS = 1
NUMBER_OF_REPLICAS = 0
KEYWORD_TOP_K = 10
TEXT_MAPPING_PROPERTY = "content"

# Semantic
SEMANTIC_TOP_K = 10

# Model
EMBEDDING_MODEL_NAME = "models/gemini-embedding-001"  # input token limit: 2,048, default/maximum output dimension size: 3072
CROSS_ENCODER_MODEL_NAME = "mixedbread-ai/mxbai-rerank-large-v2"  # Default Limit: 8,192 tokens. This is the length the model was primarily fine-tuned for and is the default setting in many implementations.
RERANKER_BATCH_SIZE = 4  # Batch size for reranker to avoid OOM on GPU
