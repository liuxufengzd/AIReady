"""Vision-model text used when a reviewed parse is rejected or too long to embed."""

import base64
import mimetypes
from collections.abc import Callable
from pathlib import Path
from typing import Any

import dagster as dg
from langchain_core.messages import AIMessage, HumanMessage

from dataprep.common import const
from dataprep.resources.llm import LLM

_PROMPT_FOR_SEMANTIC = """
### Role
You are an expert **RAG (Retrieval-Augmented Generation) Data Engineer**. Your goal is to transform raw content into a "Search-Optimized Semantic Summary" specifically designed to maximize retrieval accuracy in a Vector Database.

### Task
Generate a dense, entity-rich summary of the provided file. Focus on capturing the **latent intent**, **technical relationships**, and **specific terminology** that a user would likely use in a semantic search query.

### Guidelines
1. **Semantic Overview**: Provide a 1-5 sentence high-level summary that captures the "Core Purpose" of the document.
2. **Entity & Relationship Mapping**: Extract key entities (people, products, APIs, dates, locations). List them alongside their specific role or relationship in the text (e.g., `[Entity] -> [Role]`).
3. **Technical Deep-Dive**: Summarize the "How" and "Why." Focus on processes, logic, or architectural decisions. Preserve all specific version numbers, error codes, or technical jargon.
4. **Keyword Expansion (Lexical Boost)**: Include a dedicated "Search Terms" section. Include 5-10 synonyms, acronyms, or broader category terms that are semantically related but not explicitly mentioned (e.g., if the text is about "latency," include "delay," "performance bottleneck," and "lag").
5. **Inferred User Intent**: Identify 3-5 specific questions this document is the perfect answer for (e.g., "How do I configure the X protocol?").

### Constraints
- **Zero Noise**: Do not use filler phrases like "This document discusses..." or "The author provides..."
- **Tone Preservation**: Maintain the original tone (e.g., technical, legal, casual).
- **Language Integrity**: All descriptions, extracted text, and summaries must be written in the **ORIGINAL LANGUAGE** found within the file. Do not translate any terms or concepts.
- **Density over Length**: Prioritize information density and keyword richness over word count.

### Output Format
Strict well-structured Markdown:

# SEMANTIC SUMMARY
[1-5 sentence overview]

# KEY ENTITIES & RELATIONSHIPS
* **Entity**: [Description/Relationship]

# TECHNICAL PROCESSES & LOGIC
* [Point-by-point functional breakdown]

# SEARCH TERMS (LEXICAL BOOST)
* [Synonyms, Acronyms, Related Categories]

# TARGET QUERIES (INFERRED INTENT)
* [Question 1?]
* [Question 2?]
"""

_PROMPT_FOR_KEYWORD = """
# Role
You are an expert Document Intelligence Specialist. Your task is to transform raw PDF/images into a **BM25-optimized Markdown** format, focusing on **Signal-to-Noise Ratio (SNR)** enhancement.

# Primary Objective
Extract and reconstruct the document into a high-fidelity Markdown file. Your goal is to maximize the "Signal" (actual data and meaningful content) and eliminate "Noise" (static form templates and redundant UI elements).

# Requirements & Noise Reduction Logic
1. **Signal-Only Extraction (Crucial)**:
   - Remove decorative symbols (e.g., icons, lines, boxes) and repetitive boilerplate text (e.g., "Please fill in block capitals", page numbers, or legal footers) that do not contribute to the document's core meaning.
2. **Structural Fidelity**:
   - Use Markdown headers (`#` to `####`) to maintain the logical hierarchy.
   - For tables, omit entirely empty rows or columns to keep the lexical density high.
3. **Verbatim Data Integrity**: Preserve the exact wording of all "Signal" data. Do not summarize or paraphrase.
4. **Acronym & Term Expansion**: To enhance BM25 recall, provide the full form of acronyms when they first appear (e.g., "ROI (Return on Investment)").
5. **Formula & Technical Notation**: Render mathematical formulas in LaTeX (e.g., $E=mc^2$) to capture variables as searchable tokens.
6. **Visual Element Description**: For non-text elements (charts, diagrams, images), provide a brief, text-based description of their key information to capture missing keywords.
7. **Language Policy**: Maintain the **ORIGINAL LANGUAGE** of the document.

# BM25 Optimization Strategy
- **Lexical Density**: By removing unselected options and empty templates, you ensure that the remaining keywords have a higher relative weight in the BM25 calculation.
- **Contextual Labeling**: Ensure every value is associated with its label (e.g., "Category: [Value]") so that search queries for either the label or the value will hit the document.

# Output Format
1. **[Clean Markdown Body]**: The reconstructed content, containing only active data and meaningful text.
2. **---**
3. **[Search Index & Metadata]**:
   - **Core Entities**: Specific names, technical terms, or IDs.
   - **Synonyms & Variations**: Provide 2-3 industry-standard synonyms for core terms to broaden search reach.

# Task
Identify the active content in the provided document. Strip away all static template noise, unselected options, and empty fields. Output a clean, high-signal Markdown.
"""


def _file_content(source: Path) -> dict[str, Any]:
    """Encode a PDF or image the way the chat model accepts file input."""
    suffix = source.suffix.lower()
    encoded = base64.b64encode(source.read_bytes()).decode("utf-8")
    if suffix == ".pdf":
        return {
            "type": "file",
            "source_type": "base64",
            "mime_type": "application/pdf",
            "data": encoded,
        }
    if suffix in const.IMAGE_EXTENSIONS:
        mime_type = mimetypes.guess_type(source)[0]
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
        }
    raise ValueError(f"Unsupported file type for VLM extraction: {source.suffix}")


class VLMExtractor(dg.ConfigurableResource):
    """Summarize or transcribe a document with the shared vision model.

    Rejected parses use both a semantic summary and a keyword transcription of
    the original file. Approved text that is too long to embed uses the summary
    only.
    """

    llm: LLM

    async def extract_keyword(
        self,
        source: Path,
        logger: Callable[[str], None] | None = None,
    ) -> str:
        """Transcribe ``source`` into keyword-oriented markdown."""
        if logger is not None:
            logger(f"Extracting keyword text with VLM for {source.name}")
        result = await self.llm.client.ainvoke(
            [
                HumanMessage(
                    content=[
                        {"type": "text", "text": _PROMPT_FOR_KEYWORD},
                        _file_content(source),
                    ]
                )
            ]
        )
        return result.text

    async def extract_summary(
        self,
        source: Path | None = None,
        text: str | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> str:
        """Summarize a file or a markdown string for semantic search.

        A second, shorter request is sent when the first answer exceeds the
        embedding token limit. The answer is then cut to that limit.
        """
        if logger is not None:
            logger("Extracting summary with VLM")
        if source is not None:
            content: str | dict[str, Any] = _file_content(source)
        elif text is not None:
            content = text
        else:
            raise ValueError("Either source or text must be provided")

        message = HumanMessage(
            content=[
                {"type": "text", "text": _PROMPT_FOR_SEMANTIC},
                content,
            ]
        )
        result = await self.llm.client.ainvoke([message])
        summary = result.text
        token_number = self.llm.client.get_num_tokens(summary)
        if token_number <= const.EMBEDDING_TOKEN_LIMIT:
            return summary

        if logger is not None:
            logger(
                f"Summary has {token_number} tokens; asking for a shorter summary "
                f"within {const.EMBEDDING_TOKEN_LIMIT}"
            )
        result = await self.llm.client.ainvoke(
            [
                message,
                AIMessage(content=summary),
                HumanMessage(
                    content=(
                        f"The output tokens {token_number} exceeds the limit "
                        f"{const.EMBEDDING_TOKEN_LIMIT}. Summarize the file in a "
                        "more CONCISE way."
                    )
                ),
            ]
        )
        summary = result.text
        token_number = self.llm.client.get_num_tokens(summary)
        if token_number > const.EMBEDDING_TOKEN_LIMIT:
            if logger is not None:
                logger(
                    f"Summary still has {token_number} tokens; truncating to the limit"
                )
            summary = summary[: const.EMBEDDING_TOKEN_LIMIT]
        return summary
