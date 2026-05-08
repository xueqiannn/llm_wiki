from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import Paths, has_llm_config
from .llm import chat
from .wiki import (
    FRONTMATTER_RE,
    append_log,
    extract_frontmatter_value,
    init_workspace,
    list_pages,
    rebuild_index,
    slugify,
    strip_frontmatter,
    write_wiki_page,
)

MAX_LINT_CONTEXT_CHARS = 24_000
SPECIAL_FILES = {"index.md", "log.md", "schema.md"}
GRAPH_HEALTH_EXCLUDED_TYPES = {"lint"}


@dataclass(frozen=True)
class LintFinding:
    severity: str
    category: str
    page: str
    message: str
    suggestion: str


@dataclass(frozen=True)
class LintReport:
    findings: list[LintFinding]
    llm_review: str
    saved_path: Path | None


def lint_wiki(paths: Paths, *, include_llm: bool = False, save: bool = False) -> LintReport:
    init_workspace(paths)
    findings = deterministic_lint(paths)
    llm_review = _llm_lint_review(paths, findings) if include_llm and has_llm_config() else ""
    saved_path = save_lint_report(paths, findings, llm_review) if save else None
    if saved_path:
        rebuild_index(paths)
        append_log(paths, "lint", f"Saved lint report to `{saved_path.relative_to(paths.wiki).as_posix()}`.")
    return LintReport(findings=findings, llm_review=llm_review, saved_path=saved_path)


def deterministic_lint(paths: Paths) -> list[LintFinding]:
    pages = list_pages(paths)
    findings: list[LintFinding] = []

    _check_frontmatter(paths, findings)
    _check_broken_links(pages, findings)
    _check_orphans(pages, findings)
    _check_index(paths, pages, findings)

    return findings


def save_lint_report(paths: Paths, findings: list[LintFinding], llm_review: str = "") -> Path:
    title = f"Lint Report {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    rel_path = f"lint/{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    content = _format_report_markdown(title, findings, llm_review)
    return write_wiki_page(paths, rel_path, content)


def _check_frontmatter(paths: Paths, findings: list[LintFinding]) -> None:
    for path in _wiki_markdown_paths(paths):
        text = path.read_text(encoding="utf-8", errors="replace")
        page = path.relative_to(paths.wiki).as_posix()
        if not FRONTMATTER_RE.match(text):
            findings.append(
                LintFinding(
                    severity="error",
                    category="frontmatter",
                    page=page,
                    message="Page is missing YAML frontmatter.",
                    suggestion="Add frontmatter with at least `title`, `type`, and `sources`.",
                )
            )
            continue

        for key in ("title", "type"):
            if not extract_frontmatter_value(text, key):
                findings.append(
                    LintFinding(
                        severity="warning",
                        category="frontmatter",
                        page=page,
                        message=f"Page frontmatter is missing `{key}`.",
                        suggestion=f"Add a `{key}` field so the wiki can be indexed consistently.",
                    )
                )

        if "sources:" not in FRONTMATTER_RE.match(text).group(1):
            findings.append(
                LintFinding(
                    severity="warning",
                    category="frontmatter",
                    page=page,
                    message="Page frontmatter is missing `sources`.",
                    suggestion="Add `sources: []` or list the raw source filenames that support this page.",
                )
            )


def _check_broken_links(pages, findings: list[LintFinding]) -> None:
    title_lookup = _page_title_lookup(pages)
    for page in pages:
        if page.type in GRAPH_HEALTH_EXCLUDED_TYPES:
            continue
        page_path = page.path.name
        for link in page.links:
            if slugify(link) not in title_lookup:
                findings.append(
                    LintFinding(
                        severity="error",
                        category="broken-link",
                        page=page_path,
                        message=f"`[[{link}]]` does not resolve to a wiki page.",
                        suggestion="Create the missing page or update the link text to match an existing page title.",
                    )
                )


def _check_orphans(pages, findings: list[LintFinding]) -> None:
    title_lookup = _page_title_lookup(pages)
    link_counts = {page.path: {"in": 0, "out": 0} for page in pages}

    for page in pages:
        if page.type in GRAPH_HEALTH_EXCLUDED_TYPES:
            continue
        for link in page.links:
            target = title_lookup.get(slugify(link))
            if target and target != page.path:
                link_counts[page.path]["out"] += 1
                link_counts[target]["in"] += 1

    for page in pages:
        if page.type in {"overview", "lint"}:
            continue
        counts = link_counts.get(page.path, {"in": 0, "out": 0})
        if counts["in"] == 0 and counts["out"] == 0:
            findings.append(
                LintFinding(
                    severity="warning",
                    category="orphan",
                    page=page.path.name,
                    message="Page has no incoming or outgoing wikilinks.",
                    suggestion="Add links to related pages, or consider merging/removing the page.",
                )
            )
        elif counts["in"] == 0:
            findings.append(
                LintFinding(
                    severity="info",
                    category="orphan",
                    page=page.path.name,
                    message="Page has outgoing links but no incoming links.",
                    suggestion="Link to this page from an overview, source summary, or related concept page.",
                )
            )


def _check_index(paths: Paths, pages, findings: list[LintFinding]) -> None:
    index_path = paths.wiki / "index.md"
    index_text = index_path.read_text(encoding="utf-8", errors="replace") if index_path.exists() else ""
    for page in pages:
        if page.type == "lint":
            continue
        if f"[[{page.title}]]" not in index_text:
            findings.append(
                LintFinding(
                    severity="info",
                    category="index",
                    page=page.path.name,
                    message="Page is not listed in `index.md`.",
                    suggestion="Rebuild the index or add a catalog entry for this page.",
                )
            )


def _llm_lint_review(paths: Paths, findings: list[LintFinding]) -> str:
    pages = list_pages(paths)
    context_parts: list[str] = []
    used = 0
    for page in pages:
        if page.type == "lint":
            continue
        rel = page.path.relative_to(paths.wiki).as_posix()
        chunk = f"\n\n---\nPage: {rel}\n{page.text}"
        if used + len(chunk) > MAX_LINT_CONTEXT_CHARS:
            break
        used += len(chunk)
        context_parts.append(chunk)

    deterministic_summary = "\n".join(
        f"- {f.severity} | {f.category} | {f.page}: {f.message}" for f in findings
    )
    prompt = f"""
Review this small LLM-maintained wiki for health issues.

Look for:
- contradictions or stale claims across pages
- important concepts mentioned but not promoted to pages
- missing cross-links between related pages
- weak summaries or pages that need better source traceability

Return concise markdown with sections:
## Contradictions Or Tensions
## Missing Pages Or Links
## Maintenance Suggestions

Deterministic lint findings:
{deterministic_summary or "(none)"}

Wiki pages:
{''.join(context_parts)}
"""
    return chat(
        [
            {"role": "system", "content": "You are a careful wiki maintainer doing a lint pass."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )


def _format_report_markdown(title: str, findings: list[LintFinding], llm_review: str) -> str:
    rows = "\n".join(
        f"| {f.severity} | {f.category} | `{f.page}` | {f.message} | {f.suggestion} |"
        for f in findings
    )
    if not rows:
        rows = "| ok | deterministic | - | No deterministic issues found. | Keep ingesting and re-run lint periodically. |"

    llm_section = llm_review.strip() if llm_review.strip() else "LLM review was not run for this report."
    return f"""---
title: "{title}"
type: lint
sources: []
---

# {title}

## Deterministic Checks

| Severity | Category | Page | Finding | Suggestion |
| --- | --- | --- | --- | --- |
{rows}

## LLM Review

{llm_section}
"""


def _wiki_markdown_paths(paths: Paths) -> list[Path]:
    if not paths.wiki.exists():
        return []
    return sorted(path for path in paths.wiki.rglob("*.md") if path.name not in SPECIAL_FILES)


def _page_title_lookup(pages) -> dict[str, Path]:
    lookup: dict[str, Path] = {}
    for page in pages:
        lookup[slugify(page.title)] = page.path
        lookup[slugify(page.path.stem)] = page.path
    return lookup
