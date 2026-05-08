# LLM Wiki Core

Minimal Python proof of concept for the LLM Wiki pattern described in `../llm-wiki.md`.

This repo intentionally keeps only the core mechanism:

- Upload source files into an immutable raw folder.
- Ask an LLM to integrate each source into a persistent markdown wiki with two-stage ingest.
- Maintain `schema.md`, `index.md`, `log.md`, and `overview.md`.
- Use `[[wikilinks]]` to create a graph.
- Query the generated wiki instead of re-reading raw sources every time.
- Lint the wiki for health issues and save lint reports as wiki pages.

The mature desktop app in the parent repo has many product features: queueing, Tauri, review items, deep research, embeddings, advanced graph scoring, localization, and more. This POC leaves those out so the mechanism is easy to demo.

## Quick Start

Install `uv`, then run:

```bash
cd llm-wiki-core
uv sync
uv run streamlit run app.py
```

Open the local Streamlit URL, upload a few files, ingest them, inspect the graph, and ask questions.

## Two-Stage Ingest

When an LLM is configured, ingest runs in two calls:

1. Analysis: the LLM reads the source plus current `index.md` and `overview.md`, then identifies key takeaways, concepts, existing-wiki connections, contradictions, and recommended updates.
2. Generation: the LLM uses that analysis as context and emits structured wiki page JSON. The app writes those pages, rebuilds `index.md`, and appends `log.md`.

This mirrors the core flow from the mature app while keeping the POC easy to inspect.

## LLM Configuration

Copy `.env.example` to `.env` and set:

```bash
OPENAI_API_KEY=sk-your-key
OPENAI_MODEL=gpt-4o-mini
```

For OpenAI-compatible providers, also set:

```bash
OPENAI_BASE_URL=https://your-provider.example/v1
```

For Azure OpenAI, use these instead:

```bash
AZURE_OPENAI_API_KEY=your-azure-key
AZURE_OPENAI_ENDPOINT=https://your-resource-name.openai.azure.com
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini
```

`AZURE_OPENAI_DEPLOYMENT` must be your Azure deployment name. It may be the same as the model name, but Azure lets you choose a custom deployment name.

If neither `OPENAI_API_KEY` nor `AZURE_OPENAI_API_KEY` is set, the app still runs with a simple heuristic fallback. That mode is useful for smoke testing the UI, but it will not produce high-quality wiki synthesis.

## Workspace Layout

By default, runtime data is stored under `workspace/`:

```text
workspace/
  raw/
    sources/
      uploaded-files
  wiki/
    schema.md
    overview.md
    index.md
    log.md
    sources/
    concepts/
    entities/
    queries/
    lint/
```

Set `LLM_WIKI_HOME` to use another data directory.

## Supported Source Types

- Markdown and text
- CSV, JSON, log-like text files
- PDF
- DOCX
- PPTX

## Demo Flow

1. Upload one or more source files in the Ingest tab.
2. Click "Ingest uploaded files".
3. Open the Wiki tab to inspect generated markdown.
4. Open the Graph tab to see `[[wikilink]]` connections.
5. Ask a question in the Query tab. Optionally save the answer back into the wiki.
6. Run the Lint tab to find broken links, orphan pages, stale index entries, and optional LLM maintenance suggestions.

## Lint Demo

The Lint tab has two layers:

- Deterministic checks: missing frontmatter, broken `[[wikilinks]]`, orphan pages, and pages missing from `index.md`.
- LLM review: contradictions, stale claims, missing concept pages, and suggested cross-links.

Enable "Save lint report back into the wiki" to write the report under `wiki/lint/`. This demonstrates the same compounding idea as saved query answers: maintenance work becomes part of the persistent wiki artifact.

## What This Demonstrates

Traditional RAG retrieves raw chunks at query time. LLM Wiki compiles knowledge into an evolving, interlinked artifact first. The query step then reads the wiki, so each ingest can compound what the system already knows.
