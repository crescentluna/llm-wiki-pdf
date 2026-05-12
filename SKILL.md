---
name: llm-wiki-pdf
description: Initialize and maintain a Karpathy-style LLM-Wiki for academic papers. Extracts PDF papers to structured Markdown with figure mapping. Supports dual extraction backends (MinerU VLM API preferred, pymupdf4llm fallback). Use when the user mentions LLM-Wiki, paper knowledge base, PDF extraction for wiki, Karpathy wiki, or wants to build a structured knowledge base from academic papers.
---

# LLM-Wiki PDF Extraction

Build and maintain a Karpathy-style LLM-Wiki knowledge base from academic PDF papers.

## Overview

This skill provides:
1. **PDF to Markdown extraction** with dual backend support (MinerU VLM API / pymupdf4llm)
2. **Figure extraction** with heuristic Figure-number mapping
3. **AGENTS.md template** for initializing new LLM-Wiki projects

## Quick Start

### Initialize a new LLM-Wiki project

1. Copy scripts to the target project:
```bash
cp scripts/extract_pdf.py <project>/
cp scripts/extract_pdf_mineru.py <project>/
cp scripts/extract_pdf_pymupdf.py <project>/
cp AGENTS_TEMPLATE.md <project>/AGENTS.md
```

2. Create the directory structure:
```bash
mkdir -p <project>/raw <project>/wiki/{sources,assets/figures,entities/{models,methods},concepts,synthesis}
```

3. Place PDF papers in `<project>/raw/`

4. Run extraction:
```bash
# Auto-selects: MinerU if TOKEN is set, otherwise pymupdf4llm
python extract_pdf.py

# Or explicitly choose backend:
python extract_pdf_mineru.py --all     # MinerU VLM (recommended)
python extract_pdf_pymupdf.py --all    # pymupdf4llm (fallback)
```

## Backend Selection Logic

The unified `extract_pdf.py` entry point:
- Checks for `MINERU_TOKEN` environment variable or `TOKEN` in `extract_pdf_mineru.py`
- If token is configured and non-empty: uses **MinerU VLM API** (better quality)
- If token is empty/missing: falls back to **pymupdf4llm** (local, no network needed)

### MinerU VLM (Recommended)

| Advantage | Detail |
|-----------|--------|
| Image recognition | VLM-based, distinguishes figures from formula renderings |
| Formula extraction | Higher quality LaTeX restoration |
| Table parsing | Structural recognition, preserves alignment |
| Free quota | 5000 documents/day, max 200 pages each |

**Setup**: Visit https://mineru.net/apiManage/docs, register, get API key. Set as:
```bash
export MINERU_TOKEN="your-api-key-here"
```

### PyMuPDF4LLM (Fallback)

Runs locally, no network needed. Install:
```bash
pip install pymupdf4llm pymupdf pillow
```

## Output Structure

```
wiki/sources/{paper_id}.md          # Paper Markdown with frontmatter
wiki/assets/figures/{paper_id}/
    fig1.png ... figN.png           # Extracted figures (global ordering)
    _index.json                     # Figure index with figure_label mapping
```

## Key Innovation: Figure Mapping

The `_index.json` maps extracted images to paper Figure numbers via:
1. Text-position nearest-neighbor matching (distance < 5000 chars)
2. Area-based primary figure selection (largest image wins per Figure)

LLM agents use `_index.json` to precisely reference correct figures in entity pages.

## Commands

All scripts support:
```bash
python <script>.py              # Incremental extraction
python <script>.py --all        # Full extraction
python <script>.py --force PID  # Force re-extract specific paper
python <script>.py --list       # List extraction status
```

## AGENTS.md Template

Use `AGENTS_TEMPLATE.md` as the Schema layer for new projects. Customize:
- Research topic and tags
- Entity categories (models/methods/datasets)
- Page format conventions

See [AGENTS_TEMPLATE.md](AGENTS_TEMPLATE.md) for the full template.

## Dependencies

```bash
pip install pymupdf pymupdf4llm pillow requests
```
