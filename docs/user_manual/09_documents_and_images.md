# Chapter 9: Documents and Images

Consensus lets you attach reference materials to discussions so that both human and AI participants can work with real data, papers, and visuals.

## Documents

### Attaching Documents During Setup

In the **New Discussion** tab, the **Reference Documents** card lets you add documents:

- **Upload File** — Click to select a file from your computer. Supported formats:
  - PDF (text extraction via pdfplumber or PyPDF2)
  - HTML (text extraction via trafilatura)
  - Plain text / Markdown (used as-is)
- **Add URL** — Paste a URL and click Add. Consensus fetches the page and extracts text content. Works with web pages, PDFs hosted online, and plain text files.

### How Documents Are Processed

When you add a document, Consensus:

1. **Extracts text** from the source format
2. **Chunks** the text into ~1500-character segments with 200-character overlap, respecting paragraph boundaries
3. **Generates embeddings** for each chunk (if an embedding service is configured)
4. **Stores** everything in the database for retrieval

### What AI Participants Can Do with Documents

AI participants with document tools enabled (see [Chapter 8](08_tools_and_capabilities.md)) can:

- **Browse** — List documents, get section headers, read specific ranges
- **Search** — Semantic search across document content (requires embeddings)
- **Ask questions** — RAG-powered Q&A that retrieves relevant chunks and generates an answer with citations
- **Summarise** — Map-reduce summarisation that works on documents of any length
- **Add new documents** — AI can add documents mid-discussion by URL or inline text

### Cross-Discussion Document Library

Documents persist in a shared library across discussions. AI participants can search the full library (not just documents attached to the current discussion) using `doc_list(full_library=true, query="...")`.

## Images

### Attaching Images During Setup

In the **Reference Images** card:

- **Upload Image** — Select from your computer (max 20 MB per image)
- **Add URL** — Paste an image URL

**Supported formats:** PNG, JPEG, GIF, WebP, SVG

Images larger than 2048 pixels in any dimension are automatically resized (requires Pillow).

### How AI Participants See Images

This depends on the model's capabilities:

- **Vision-capable models** (GPT-4o, Claude 3 Opus/Sonnet/Haiku, Gemini Pro/Flash, etc.) — Images are sent directly as base64-encoded visual content in the model's context. These models can see and reason about the images natively.
- **Text-only models** — Can use the `describe_image` tool to get an LLM-generated text description of any attached image.

### Image Storage

Images are stored on disk in the platform data directory under an `images/` subdirectory. Metadata is tracked in the database, including per-discussion and per-message image associations.
