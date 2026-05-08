from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import Paths

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
SPECIAL_PAGE_NAMES = {"index.md", "log.md", "schema.md"}
WIKI_PAGE_TYPES = ("overview", "source", "concept", "entity", "query", "lint")
LLM_GENERATED_PAGE_TYPES = ("overview", "source", "concept", "entity", "query")
REQUIRED_FRONTMATTER_FIELDS = ("title", "type", "sources")
PAGE_TYPE_DIRECTORIES = {
    "source": "sources",
    "concept": "concepts",
    "entity": "entities",
    "query": "queries",
    "lint": "lint",
}


DEFAULT_SCHEMA = f"""# Schema

This wiki demonstrates the minimal LLM Wiki mechanism.

## Layers
- `raw/sources/`: immutable uploaded source files.
- `wiki/`: LLM-maintained markdown pages.
- `wiki/schema.md`: conventions the LLM follows while maintaining the wiki.

## Page conventions
- Every page starts with YAML frontmatter.
- Use `type` values: `{", ".join(WIKI_PAGE_TYPES)}`.
- Required frontmatter fields: `{", ".join(REQUIRED_FRONTMATTER_FIELDS)}`.
- Use Obsidian-style `[[wikilinks]]` to connect pages.
- Prefer concise pages that can be updated incrementally.
- Keep source traceability in `sources`.

## Operations
- Ingest: read a source, create/update wiki pages, update `index.md`, append `log.md`.
- Query: search wiki pages first, then answer with page citations.
- Graph: build links from `[[wikilinks]]`.
- Lint: check wiki health, find broken links/orphans/contradictions, and save lint reports.
"""

DEFAULT_OVERVIEW = """# Overview

This page is updated as sources are ingested. It should summarize the wiki's current shape and point to important pages with `[[wikilinks]]`.
"""


@dataclass(frozen=True)
class Page:
    path: Path
    title: str
    type: str
    sources: list[str]
    links: list[str]
    text: str


def init_workspace(paths: Paths) -> None:
    paths.raw_sources.mkdir(parents=True, exist_ok=True)
    paths.wiki.mkdir(parents=True, exist_ok=True)
    for folder in sorted(set(PAGE_TYPE_DIRECTORIES.values())):
        (paths.wiki / folder).mkdir(parents=True, exist_ok=True)
    _write_if_missing(paths.wiki / "schema.md", DEFAULT_SCHEMA)
    _write_if_missing(paths.wiki / "overview.md", DEFAULT_OVERVIEW)
    _write_if_missing(paths.wiki / "index.md", "# Index\n\n- [[Overview]] - Current wiki summary.\n")
    _write_if_missing(paths.wiki / "log.md", "# Log\n")


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "untitled"


def title_from_slug(value: str) -> str:
    return value.replace("-", " ").replace("_", " ").title()


def wiki_path_for_title(paths: Paths, title: str, page_type: str = "concept") -> Path:
    folder = PAGE_TYPE_DIRECTORIES.get(page_type, f"{page_type}s")
    return paths.wiki / folder / f"{slugify(title)}.md"


def write_wiki_page(paths: Paths, relative_path: str, content: str) -> Path:
    safe_rel = _safe_relative_markdown_path(relative_path)
    target = paths.wiki / safe_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content.strip() + "\n", encoding="utf-8")
    return target


def append_log(paths: Paths, kind: str, message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    log_path = paths.wiki / "log.md"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"\n## [{timestamp}] {kind}\n\n{message.strip()}\n")


def list_pages(paths: Paths) -> list[Page]:
    if not paths.wiki.exists():
        return []
    pages = []
    for path in sorted(paths.wiki.rglob("*.md")):
        if path.name in SPECIAL_PAGE_NAMES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        pages.append(
            Page(
                path=path,
                title=extract_title(text, path),
                type=extract_frontmatter_value(text, "type") or "note",
                sources=extract_sources(text),
                links=extract_wikilinks(text),
                text=text,
            )
        )
    return pages


def rebuild_index(paths: Paths) -> None:
    pages = list_pages(paths)
    by_type: dict[str, list[Page]] = {}
    for page in pages:
        by_type.setdefault(page.type, []).append(page)

    lines = ["# Index", ""]
    for page_type in sorted(by_type):
        lines.append(f"## {title_from_slug(page_type)}")
        for page in sorted(by_type[page_type], key=lambda p: p.title.lower()):
            rel = page.path.relative_to(paths.wiki).as_posix()
            summary = first_sentence(strip_frontmatter(page.text))
            lines.append(f"- [[{page.title}]] (`{rel}`) - {summary}")
        lines.append("")

    (paths.wiki / "index.md").write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def extract_title(text: str, path: Path) -> str:
    frontmatter_title = extract_frontmatter_value(text, "title")
    if frontmatter_title:
        return frontmatter_title
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return title_from_slug(path.stem)


def extract_frontmatter_value(text: str, key: str) -> str | None:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None
    for line in match.group(1).splitlines():
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return None


def has_frontmatter_key(text: str, key: str) -> bool:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return False
    return any(line.startswith(f"{key}:") for line in match.group(1).splitlines())


def extract_sources(text: str) -> list[str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return []
    frontmatter = match.group(1)
    sources: list[str] = []
    in_sources = False
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if stripped == "sources:":
            in_sources = True
            continue
        if in_sources and stripped.startswith("- "):
            sources.append(stripped[2:].strip().strip("\"'"))
        elif in_sources and stripped and not stripped.startswith("- "):
            break
        elif stripped.startswith("sources: ["):
            items = stripped.removeprefix("sources:").strip().strip("[]")
            return [i.strip().strip("\"'") for i in items.split(",") if i.strip()]
    return sources


def extract_wikilinks(text: str) -> list[str]:
    return sorted({match.strip() for match in WIKILINK_RE.findall(text)})


def ensure_wiki_frontmatter(content: str, title: str, page_type: str, sources: list[str]) -> str:
    if content.lstrip().startswith("---"):
        return content
    return build_wiki_page(title=title, page_type=page_type, sources=sources, body=content)


def build_wiki_page(title: str, page_type: str, sources: list[str], body: str) -> str:
    return f"{format_frontmatter(title, page_type, sources)}\n\n{body.strip()}\n"


def format_frontmatter(title: str, page_type: str, sources: list[str]) -> str:
    sources_block = "sources: []" if not sources else f"sources:\n{format_yaml_list(sources)}"
    return f"""---
title: "{title}"
type: {page_type}
{sources_block}
---"""


def format_yaml_list(values: list[str]) -> str:
    return "\n".join(f'  - "{value}"' for value in values)


def strip_frontmatter(text: str) -> str:
    return FRONTMATTER_RE.sub("", text, count=1).strip()


def first_sentence(text: str, max_len: int = 180) -> str:
    text = re.sub(r"\s+", " ", text).strip("# ").strip()
    if not text:
        return "No summary yet."
    sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    return sentence[:max_len].rstrip() + ("..." if len(sentence) > max_len else "")


def _write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.strip() + "\n", encoding="utf-8")


def _safe_relative_markdown_path(relative_path: str) -> Path:
    rel = Path(relative_path.strip().replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"Unsafe wiki path: {relative_path}")
    if rel.suffix.lower() != ".md":
        rel = rel.with_suffix(".md")
    return rel
