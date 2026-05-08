from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import Paths, has_llm_config
from .documents import extract_text
from .llm import chat, parse_json_object
from .wiki import (
    append_log,
    first_sentence,
    init_workspace,
    list_pages,
    rebuild_index,
    slugify,
    strip_frontmatter,
    title_from_slug,
    wiki_path_for_title,
    write_wiki_page,
)

MAX_SOURCE_CHARS = 45_000
MAX_CONTEXT_CHARS = 18_000


@dataclass(frozen=True)
class IngestResult:
    written_paths: list[Path]
    analysis: str
    used_llm: bool


@dataclass(frozen=True)
class ReingestResult:
    sources: list[Path]
    results: list[IngestResult]


def save_uploaded_file(paths: Paths, filename: str, data: bytes) -> Path:
    init_workspace(paths)
    target = unique_path(paths.raw_sources / filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def copy_source_into_workspace(paths: Paths, source: Path) -> Path:
    init_workspace(paths)
    target = unique_path(paths.raw_sources / source.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def reingest_raw_sources(
    paths: Paths,
    guidance: str = "",
    progress: Callable[[str], None] | None = None,
) -> ReingestResult:
    init_workspace(paths)
    sources = sorted(path for path in paths.raw_sources.rglob("*") if path.is_file())

    if paths.wiki.exists():
        shutil.rmtree(paths.wiki)
    init_workspace(paths)

    results: list[IngestResult] = []
    for source_path in sources:
        if progress:
            progress(f"Re-ingesting `{source_path.name}`...")
        results.append(ingest_source(paths, source_path, guidance=guidance, progress=progress))

    append_log(paths, "reingest", f"Cleaned wiki and re-ingested {len(sources)} raw source file(s).")
    return ReingestResult(sources=sources, results=results)


def ingest_source(
    paths: Paths,
    source_path: Path,
    guidance: str = "",
    progress: Callable[[str], None] | None = None,
) -> IngestResult:
    init_workspace(paths)
    source_text = extract_text(source_path)
    if has_llm_config():
        analysis, pages = _llm_ingest(paths, source_path, source_text, guidance, progress)
        used_llm = True
    else:
        if progress:
            progress("Fallback ingest: LLM is not configured.")
        analysis = "LLM is not configured. Used heuristic fallback ingest instead of two-stage LLM ingest."
        pages = _fallback_ingest(paths, source_path, source_text)
        used_llm = False

    written: list[Path] = []
    for page in pages:
        content = _ensure_frontmatter(page["content"], page["title"], page["type"], source_path.name)
        written.append(write_wiki_page(paths, page["path"], content))

    rebuild_index(paths)
    append_log(
        paths,
        "ingest",
        f"Ingested `{source_path.name}` with {'two-stage LLM ingest' if used_llm else 'fallback ingest'} and wrote {len(written)} wiki page(s).",
    )
    return IngestResult(written_paths=written, analysis=analysis, used_llm=used_llm)


def answer_question(paths: Paths, question: str, save: bool = False) -> tuple[str, list[Path]]:
    init_workspace(paths)
    matches = search_pages(paths, question, limit=6)
    if has_llm_config():
        answer = _llm_answer(question, matches)
    else:
        answer = _fallback_answer(question, matches)

    saved_paths: list[Path] = []
    if save:
        title = f"Query - {question[:60]}"
        rel_path = f"queries/{datetime.now().strftime('%Y%m%d-%H%M%S')}-{slugify(question[:50])}.md"
        content = f"""---
title: "{title}"
type: query
sources: []
---

# {title}

## Question

{question}

## Answer

{answer}
"""
        saved_paths.append(write_wiki_page(paths, rel_path, content))
        rebuild_index(paths)
        append_log(paths, "query", f"Answered and saved query: `{question}`")

    return answer, saved_paths


def search_pages(paths: Paths, query: str, limit: int = 6) -> list[Path]:
    terms = [slugify(term) for term in query.split() if len(term) > 2]
    scored: list[tuple[int, Path]] = []
    for page in list_pages(paths):
        haystack = slugify(page.title + " " + strip_frontmatter(page.text))
        score = sum(haystack.count(term) for term in terms)
        if score:
            scored.append((score, page.path))

    scored.sort(key=lambda item: item[0], reverse=True)
    if scored:
        return [path for _, path in scored[:limit]]

    return [page.path for page in list_pages(paths)[:limit]]


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    i = 2
    while True:
        candidate = parent / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def _llm_ingest(
    paths: Paths,
    source_path: Path,
    source_text: str,
    guidance: str,
    progress: Callable[[str], None] | None,
) -> tuple[str, list[dict[str, str]]]:
    index = (paths.wiki / "index.md").read_text(encoding="utf-8", errors="replace")
    schema = (paths.wiki / "schema.md").read_text(encoding="utf-8", errors="replace")
    overview = (paths.wiki / "overview.md").read_text(encoding="utf-8", errors="replace")
    truncated_source = source_text[:MAX_SOURCE_CHARS]

    if progress:
        progress("Stage 1/2: analyzing source and existing wiki context...")
    analysis = _llm_ingest_analysis(
        source_path=source_path,
        source_text=truncated_source,
        index=index,
        overview=overview,
        guidance=guidance,
    )
    if progress:
        progress("Stage 2/2: generating wiki pages from the analysis...")
    pages = _llm_ingest_generation(
        source_path=source_path,
        source_text=truncated_source,
        analysis=analysis,
        schema=schema,
        index=index,
        overview=overview,
        guidance=guidance,
    )
    return analysis, pages


def _llm_ingest_analysis(
    *,
    source_path: Path,
    source_text: str,
    index: str,
    overview: str,
    guidance: str,
) -> str:
    prompt = f"""
Analyze this source before any wiki files are written.

The goal is to help a later generation step integrate the source into a persistent LLM Wiki.

Return concise markdown with these sections:
## Key Takeaways
## Important Entities And Concepts
## Connections To Existing Wiki
## Contradictions Or Tensions
## Recommended Wiki Updates

Do not write final wiki pages in this stage.

Current index:
{index}

Current overview:
{overview}

User guidance:
{guidance or "(none)"}

Source file: {source_path.name}
Source text:
{source_text}
"""
    return chat(
        [
            {
                "role": "system",
                "content": "You are Stage 1 of a two-stage LLM Wiki ingest. Analyze only; do not generate files.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
    )


def _llm_ingest_generation(
    *,
    source_path: Path,
    source_text: str,
    analysis: str,
    schema: str,
    index: str,
    overview: str,
    guidance: str,
) -> list[dict[str, str]]:
    prompt = f"""
You are Stage 2 of a two-stage LLM Wiki ingest.

Use the Stage 1 analysis as context, but do not echo its prose. Generate wiki page JSON only.

Return this shape:
{{
  "pages": [
    {{
      "path": "sources/source-title.md",
      "title": "Page Title",
      "type": "source|concept|entity|overview",
      "content": "Full markdown page including useful [[wikilinks]]"
    }}
  ]
}}

Rules:
- Always create one source summary page under sources/.
- Create or update only the few pages needed to integrate the source.
- Use concise markdown and Obsidian-style [[wikilinks]].
- Include source traceability in frontmatter.
- If updating overview, use path "overview.md".
- Do not include raw source text verbatim except short quotes.
- Return valid JSON only. No preamble. No markdown fences.

Schema:
{schema}

Current index:
{index}

Current overview:
{overview}

User guidance:
{guidance or "(none)"}

Stage 1 analysis:
{analysis}

Source file: {source_path.name}
Source text:
{source_text}
"""
    raw = chat(
        [
            {
                "role": "system",
                "content": "You are a disciplined wiki generation assistant. Return valid JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
    )
    data = parse_json_object(raw)
    pages = data.get("pages", [])
    if not isinstance(pages, list):
        raise ValueError("LLM response did not include a pages list.")
    return [_clean_page_dict(page, source_path) for page in pages]


def _fallback_ingest(paths: Paths, source_path: Path, source_text: str) -> list[dict[str, str]]:
    title = title_from_slug(source_path.stem)
    source_summary = first_sentence(source_text, max_len=320)
    concepts = _fallback_concepts(source_text)
    concept_links = ", ".join(f"[[{concept}]]" for concept in concepts) or "[[Overview]]"

    pages = [
        {
            "path": f"sources/{slugify(title)}.md",
            "title": title,
            "type": "source",
            "content": f"""# {title}

## Summary

{source_summary}

## Key Links

{concept_links}

## Extract

{source_text[:3000].strip() or "(No extractable text.)"}
""",
        }
    ]

    for concept in concepts:
        pages.append(
            {
                "path": str(wiki_path_for_title(paths, concept, "concept").relative_to(paths.wiki)),
                "title": concept,
                "type": "concept",
                "content": f"""# {concept}

This concept was found while ingesting [[{title}]].

## Notes

- Source summary: {source_summary}
""",
            }
        )

    return pages


def _llm_answer(question: str, matches: list[Path]) -> str:
    context_parts = []
    used = 0
    for path in matches:
        text = path.read_text(encoding="utf-8", errors="replace")
        chunk = f"\n\n---\nPage: {path.name}\n{text}"
        if used + len(chunk) > MAX_CONTEXT_CHARS:
            break
        used += len(chunk)
        context_parts.append(chunk)

    return chat(
        [
            {
                "role": "system",
                "content": "Answer from the wiki context. Cite markdown page names inline, for example `(overview.md)`.",
            },
            {
                "role": "user",
                "content": f"Question: {question}\n\nWiki context:{''.join(context_parts)}",
            },
        ],
        temperature=0.2,
    )


def _fallback_answer(question: str, matches: list[Path]) -> str:
    if not matches:
        return "No wiki pages exist yet. Upload and ingest a source first."
    bullets = []
    for path in matches:
        text = strip_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        bullets.append(f"- `{path.name}`: {first_sentence(text)}")
    return "LLM is not configured, so this is a lexical wiki lookup.\n\n" + "\n".join(bullets)


def _clean_page_dict(page: object, source_path: Path) -> dict[str, str]:
    if not isinstance(page, dict):
        raise ValueError("Each page must be an object.")
    title = str(page.get("title") or source_path.stem)
    page_type = str(page.get("type") or "concept")
    path = str(page.get("path") or f"{page_type}s/{slugify(title)}.md")
    content = str(page.get("content") or f"# {title}\n")
    return {"path": path, "title": title, "type": page_type, "content": content}


def _ensure_frontmatter(content: str, title: str, page_type: str, source_name: str) -> str:
    if content.lstrip().startswith("---"):
        return content
    return f"""---
title: "{title}"
type: {page_type}
sources:
  - "{source_name}"
---

{content.strip()}
"""


def _fallback_concepts(text: str, limit: int = 4) -> list[str]:
    candidates: list[str] = []
    for line in text.splitlines():
        stripped = line.strip().strip("#:-")
        if 3 <= len(stripped.split()) <= 7 and len(stripped) <= 80:
            candidates.append(stripped)
    unique = []
    seen = set()
    for candidate in candidates:
        key = slugify(candidate)
        if key and key not in seen:
            seen.add(key)
            unique.append(candidate)
        if len(unique) >= limit:
            break
    return unique
