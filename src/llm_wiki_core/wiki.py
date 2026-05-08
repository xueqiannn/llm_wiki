from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import Paths

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


DEFAULT_SCHEMA = """# Wiki Schema

Use this schema when creating or updating generated wiki pages.

## Page Types

- `overview`: high-level map of the wiki's current knowledge.
- `source`: summary of one raw source and the key facts it supports.
- `concept`: reusable idea, pattern, technology, risk, or behavior.
- `entity`: named system, service, team, person, model, company, or dataset.
- `query`: saved question, investigation, or unresolved issue.
- `lint`: maintenance report about wiki health.

## Required Frontmatter

Every generated wiki page starts with YAML frontmatter:

```yaml
---
title: Human-readable page title
type: overview | source | concept | entity | query | lint
sources: []
created: ISO-8601 timestamp
updated: ISO-8601 timestamp
---
```

Use `sources` for raw source filenames or wiki pages that support the page. The application preserves `created` and refreshes `updated` when writing pages.

## Body Conventions

- Start with one `# Title` heading that matches the frontmatter title.
- Keep pages concise and update-friendly.
- Prefer bullets and short sections over long prose.
- Use Obsidian-style `[[wikilinks]]` for related pages.
- Link important concepts, entities, source summaries, and open questions.
- Do not paste long raw-source excerpts; summarize and quote only short evidence.

## Page-Specific Guidance

- `source` pages should include summary, key facts, and links to extracted concepts/entities.
- `concept` and `entity` pages should merge new evidence with existing knowledge instead of duplicating near-identical pages.
- `query` pages should record the question, current answer or uncertainty, and related evidence.
- `overview` should summarize the wiki's shape and point to the most important pages.
"""

DEFAULT_OVERVIEW = """---
title: Overview
type: overview
sources: []
---

# Overview

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
    folder = "sources" if page_type == "source" else page_type + "s"
    return paths.wiki / folder / f"{slugify(title)}.md"


def write_wiki_page(paths: Paths, relative_path: str, content: str) -> Path:
    safe_rel = _safe_relative_markdown_path(relative_path)
    target = paths.wiki / safe_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    existing_text = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
    content = add_frontmatter_timestamps(content, existing_text)
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
        if path.name in {"index.md", "log.md", "schema.md"}:
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


def add_frontmatter_timestamps(content: str, existing_text: str | None = None) -> str:
    content = content.strip()
    match = FRONTMATTER_RE.match(content)
    if not match:
        return content

    now = _timestamp()
    created = (
        (extract_frontmatter_value(existing_text, "created") if existing_text else None)
        or extract_frontmatter_value(content, "created")
        or now
    )
    frontmatter = _set_frontmatter_value(match.group(1), "created", created)
    frontmatter = _set_frontmatter_value(frontmatter, "updated", now)
    body = content[match.end() :].strip()
    return f"---\n{frontmatter}\n---\n\n{body}"


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


def _timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _set_frontmatter_value(frontmatter: str, key: str, value: str) -> str:
    line = f'{key}: "{value}"'
    lines = frontmatter.splitlines()
    for i, existing_line in enumerate(lines):
        if existing_line.startswith(f"{key}:"):
            lines[i] = line
            return "\n".join(lines)
    return "\n".join([*lines, line])


def _safe_relative_markdown_path(relative_path: str) -> Path:
    rel = Path(relative_path.strip().replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"Unsafe wiki path: {relative_path}")
    if rel.suffix.lower() != ".md":
        rel = rel.with_suffix(".md")
    return rel
