PROMPT_FOR_SEMANTIC = """
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

### Input Context
You can use the following context to help you summarize the file:
{context}
"""

PROMPT_FOR_KEYWORD = """
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


EXTENSION_PROMPT = """
Extract the defined structured information from the given file. 
Each field value must be strictly matched to its description.
Never hallucinate any information.
Never give up too early.
Remember to review your output before returning it.
"""


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
