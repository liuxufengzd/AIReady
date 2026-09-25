"""Describe an image so its text can be inlined into markdown."""

import base64
import logging
import mimetypes
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_LLM_NAME = "gemini-3.8-flash"
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")

IMAGE_META_PROMPT = """
# Role
You are an expert data extraction assistant. Your task is to analyze the provided image and extract its core content into a structured textual format optimized for consumption by another Large Language Model (LLM). 
Evaluate the image type and apply exactly ONE of the following processing rules based on its characteristics:

---

### RULE 1: IF THE IMAGE CONTAINS A CHART (Bar, Line, Pie, Scatter, etc.)
Transform the chart entirely into a clean Markdown table. Do not truncate data points.
*   **Title/Context:** Extract any main title, subtitle, or source mentioned.
*   **Axes/Legends:** Clearly label columns using the exact names from the chart axes or legend keys.
*   **Data Representation:** Reconstruct the complete dataset. For values that require estimation (e.g., a bar between grid lines), provide your best precise approximation.

### RULE 2: IF THE IMAGE CONTAINS A DIAGRAM (Flowchart, Architecture, Process, Cycle, etc.)
Transform the diagram into valid Mermaid.js flowchart syntax (`graph TD` or `graph LR`). Ensure the layout preserves hierarchical groupings and logical flows perfectly.
*   **Syntax Rules:** Use clean Markdown code blocks fenced with ` ```mermaid ` and ` ``` `.
*   **Subgraphs:** If the diagram visually groups elements into distinct sections or containers, map them out using `subgraph "Name"` and `end`.
*   **Node IDs & Labels:** Create unique alphanumeric IDs for every node (e.g., `NODE_A`). Wrap the textual content of the node inside double quotes and brackets to protect special characters (e.g., `NODE_A["Text Content"]`).
*   **Edge Formats:** Use exact Mermaid arrow markers to dictate the logic:
    *   Directional flow: `-->`
    *   Labeled flow: `-- "Label Text" -->`
    *   Non-directional association: `---` or `-- "Label Text" ---`

### RULE 3: IF THE IMAGE CONTAINS A TABLE
Extract it directly into a clean, well-formatted Markdown table.
* **Structure:** Maintain the exact column headers and row alignment as the original image.
* **Empty Cells:** If a cell is blank in the image, represent it as empty (` `) or use `N/A`. Do not skip rows or columns.
* **Formatting:** Keep bolding or italics if they signify data importance (e.g., totals or averages).

### RULE 4: IF THE IMAGE CONTAINS A FORMULA / EQUATION
Convert the mathematical formula or scientific equation into clean, valid LaTeX format.
* **Standard Enclosure:** Wrap standalone equations in double dollar signs (`$$...$$`) and inline math in single dollar signs (`$ ... $`).
* **Variable Definitions:** Immediately below the formula, provide a bulleted glossary defining what each variable, symbol, or constant represents, if visible or inferable from context.
* **Multi-line Equations:** If it is a step-by-step derivation, use the LaTeX `aligned` environment to keep the steps structurally organized.

### RULE 5: IF THE IMAGE CONTAINS COMPLEX (Infographics, Mixed Layouts, UI Screenshots) INFORMATION
Generate a comprehensive, dense summary that captures all crucial information without losing granularity. In this case, the content may be lossy.

---

# Output Format Requirements
*   **Direct Delivery:** Start directly with the extracted data. Do not include introductory phrases like "Sure, here is the extraction..." or "Based on the image...".
*   **No Placeholders:** Do not use ellipses (`...`) or summaries like "etc." to skip data. Extract everything.
*   **Tone:** Purely objective, structured, and factual.
*   **Info Loss:** If the image contains complex information, the content should be lossy.
"""


class ImageMeta(BaseModel):
    content: str = Field(
        description="The content of the image, which should contain all crucial information of the image"
    )
    info_loss: bool = Field(
        default=False,
        description="Whether the image content is lossy. If True, the original image should be remained",
    )


def _image_content(source: Path) -> dict[str, str]:
    if source.suffix not in _IMAGE_SUFFIXES:
        raise ValueError(f"Unsupported file type: {source.suffix}")
    mime_type = mimetypes.guess_type(source)[0]
    encoded = base64.b64encode(source.read_bytes()).decode("utf-8")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
    }


class ImageExtractor:
    def __init__(self):
        self.llm = ChatGoogleGenerativeAI(
            model=_LLM_NAME,
            temperature=1.0,
            max_retries=3,
        )

    async def extract(self, source: Path) -> ImageMeta:
        logger.info("Extracting image metadata for file: %s", source)
        image_meta: ImageMeta = await self.llm.with_structured_output(
            ImageMeta
        ).ainvoke(
            [
                SystemMessage(content=IMAGE_META_PROMPT),
                HumanMessage(content=[_image_content(source)]),
            ]
        )
        return image_meta
