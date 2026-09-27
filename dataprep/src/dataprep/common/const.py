EMBEDDING_TOKEN_LIMIT = 2048
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 100
MUST_CHUNK_TOKEN_THRESHOLD = 5000
OFFICE_SUFFIXES = [".docx", ".doc", ".xls", ".xlsx", ".ppt", ".pptx"]
SUPPORTED_FILE_TYPES = [".jpg", ".jpeg", ".png", ".pdf", *OFFICE_SUFFIXES]
TYPE_MAPPING = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
}

MARKDOWN_NAME = "markdown.md"
IMAGES_DIRNAME = "images"
LLM_NAME = "gemini-3.8-flash"
IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png"]
