IMAGE_SUMMARIZE = """
Act as an expert metadata tagger for an image retrieval system. Your goal is to summarize this image into a structured format that will be indexed for RAG (Retrieval-Augmented Generation).
**STRICT RULE:** 
1. All descriptions, extracted text, and summaries must be written in the **ORIGINAL LANGUAGE** found within the image. Do not translate any terms or concepts.
2. Cover all important information in the image, otherwise the summary will be incomplete and RAG system will not be able to search for them.

Please provide the following:
1. **Semantic Summary:** A detailed description of the image focusing on high-level concepts and the primary subject.
2. **Inferred Context:** What is the likely intent or category of this image? (e.g., Technical Architecture, Marketing Asset, UI Screenshot, Natural Landscape).

**Output Format:** Markdown

You can use the following context to help you summarize the image:
{context}
"""

PDF_SUMMARIZE = """
Act as an expert metadata tagger for a document retrieval system. Your goal is to summarize this **PDF file** into a structured format that will be indexed for RAG (Retrieval-Augmented Generation).

**STRICT RULES:**
1. **Linguistic Integrity:** All descriptions, extracted headers, and summaries must be written in the **ORIGINAL LANGUAGE** found within the document. Do not translate any terms or concepts.
2. **Completeness:** Ensure you capture the core thesis, key findings, and structural markers. Cover all important information in the text; otherwise, the summary will be incomplete, and the RAG system will not be able to search for it effectively.

Please provide the following:

1. **Semantic Summary:** A detailed overview of the document's purpose, high-level concepts, and the primary subject matter.
2. **Structural Breakdown:** A summary of the document's hierarchy (e.g., sections, chapters, or key headings) and the specific information contained in each.
3. **Inferred Context:** What is the likely intent or category of this document? (e.g., Technical Whitepaper, Legal Contract, Financial Report, Academic Paper, Internal Memo).
4. **Key Entities & Metadata:** Identify critical names, dates, organizations, or technical terms central to the document.

**Output Format:** Markdown

You can use the following context to help you summarize the PDF:
{context}
"""

REFINE_OCR_TEXT = """
### Role
You are an expert Document Processing Specialist. Your task is to refine noisy OCR text extracted from a document to make it high-quality, structured, and optimized for BM25 search indexing.

### Inputs
1. **Raw file:** [Attached File]
2. **Raw OCR Text:** {ocr_text}

### Task Instructions
Compare the "Raw OCR Text" against the "Raw file" and generate a cleaned version of the text. Follow these strict rules:
1. **Denoise:** Remove "OCR artifacts" such as random symbols (e.g., ~, |, _, [], ©), broken characters, and page numbers/headers/footers that interrupt the flow of content.
2. **Fix Structural Errors:** 
    - **Tables:** If the OCR text has scrambled a table, reconstruct the data into a readable, linearized format (e.g., "Category: Value") so that keywords are associated correctly.
    - **Figures/Captions:** Ensure figure captions are clearly associated with the text describing them.
3. **Correct Spelling:** Fix typos caused by OCR misrecognition (e.g., "Teb1e" -> "Table") based on the visual context.
4. **Preserve BM25 Value:** Do NOT summarize. Keep all technical terms, proper nouns, and specific data points. BM25 relies on term frequency, so ensure important keywords are present and correctly spelled.
5. **Linearize:** Reorder the text into a logical reading order (left-to-right, top-to-bottom), ignoring multi-column layout breaks that might have confused the OCR.
6. **Language:** All text must be written in the **ORIGINAL LANGUAGE** found within the file. Do not translate any terms or concepts.
7. **Correction:** If there is any difference between the "Raw OCR Text" and the "Raw file", trust the "Raw file" more.

### Output Format
Provide only the cleaned, refined text. Do not include introductory remarks or explanations.
"""
