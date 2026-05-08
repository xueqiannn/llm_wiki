from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from llm_wiki_core.config import get_paths, has_llm_config, openai_model
from llm_wiki_core.core import answer_question, ingest_source, reingest_raw_sources, save_uploaded_file, search_pages
from llm_wiki_core.graph import graph_html, graph_stats
from llm_wiki_core.lint import lint_wiki
from llm_wiki_core.wiki import init_workspace, list_pages, rebuild_index, strip_frontmatter


st.set_page_config(page_title="LLM Wiki Core", layout="wide")

paths = get_paths()
init_workspace(paths)

st.title("LLM Wiki Core")
st.caption("A minimal proof of concept: raw sources -> LLM-maintained markdown wiki -> graph, query, and lint.")

with st.sidebar:
    st.header("Workspace")
    st.code(str(paths.root), language="text")
    st.write("LLM:", f"`{openai_model()}`" if has_llm_config() else "`not configured`")
    st.write("Raw sources:", f"`{paths.raw_sources}`")
    st.write("Wiki:", f"`{paths.wiki}`")
    if st.button("Rebuild index"):
        rebuild_index(paths)
        st.success("Rebuilt `index.md`.")

tab_ingest, tab_wiki, tab_graph, tab_query, tab_lint = st.tabs(["Ingest", "Wiki", "Graph", "Query", "Lint"])

with tab_ingest:
    st.subheader("Upload And Ingest")
    st.write(
        "Upload markdown, text, PDF, DOCX, PPTX, CSV, or JSON files. "
        "Each file is copied to `raw/sources` and integrated into `wiki`."
    )
    uploads = st.file_uploader("Source files", accept_multiple_files=True)
    guidance = st.text_area(
        "Optional ingest guidance",
        placeholder="Example: focus on people, decisions, risks, and reusable concepts.",
    )

    if st.button("Ingest uploaded files", type="primary", disabled=not uploads):
        for upload in uploads or []:
            with st.status(f"Ingesting {upload.name}", expanded=True) as status:
                source_path = save_uploaded_file(paths, upload.name, upload.getvalue())
                st.write(f"Saved source: `{source_path.name}`")
                result = ingest_source(paths, source_path, guidance=guidance, progress=st.write)
                if result.used_llm:
                    with st.expander("Stage 1 analysis"):
                        st.markdown(result.analysis)
                for page in result.written_paths:
                    st.write(f"Wrote wiki page: `{page.relative_to(paths.wiki).as_posix()}`")
                status.update(label=f"Ingested {upload.name}", state="complete")
        st.success("Ingest complete.")

    st.divider()
    st.subheader("Clean And Re-ingest")
    raw_sources = sorted(path for path in paths.raw_sources.rglob("*") if path.is_file())
    st.write(
        "Delete the generated wiki and rebuild it from the raw source files already saved in "
        f"`{paths.raw_sources}`."
    )
    st.caption(f"{len(raw_sources)} raw source file(s) available.")
    confirm_reingest = st.checkbox("I understand this will delete all generated wiki pages first.")

    if st.button("Clean wiki and re-ingest raw sources", disabled=not confirm_reingest or not raw_sources):
        with st.status("Cleaning wiki and re-ingesting raw sources...", expanded=True) as status:
            result = reingest_raw_sources(paths, guidance=guidance, progress=st.write)
            for source_path, ingest_result in zip(result.sources, result.results, strict=True):
                st.write(f"`{source_path.name}` wrote {len(ingest_result.written_paths)} wiki page(s).")
            status.update(label="Clean re-ingest complete", state="complete")
        st.success(f"Re-ingested {len(result.sources)} raw source file(s).")

with tab_wiki:
    st.subheader("Generated Wiki")
    pages = list_pages(paths)
    if not pages:
        st.info("No generated pages yet. Ingest a source first.")
    else:
        labels = [f"{page.title} ({page.type})" for page in pages]
        selected = st.selectbox("Page", labels)
        page = pages[labels.index(selected)]
        st.caption(page.path.relative_to(paths.wiki).as_posix())
        st.markdown(strip_frontmatter(page.text))

        with st.expander("Raw markdown"):
            st.code(page.text, language="markdown")

with tab_graph:
    st.subheader("Wikilink Graph")
    stats = graph_stats(paths)
    col1, col2, col3 = st.columns(3)
    col1.metric("Pages", stats["pages"])
    col2.metric("Links", stats["links"])
    col3.metric("Components", stats["components"])
    components.html(graph_html(paths), height=680, scrolling=True)

with tab_query:
    st.subheader("Ask The Wiki")
    question = st.text_input("Question", placeholder="What does this wiki currently know?")
    save_answer = st.checkbox("Save answer back into the wiki as a query page")

    if st.button("Ask", type="primary", disabled=not question.strip()):
        with st.spinner("Searching wiki pages and answering..."):
            matches = search_pages(paths, question)
            answer, saved_paths = answer_question(paths, question, save=save_answer)

        st.markdown(answer)
        if matches:
            st.caption("Context pages")
            for match in matches:
                st.write(f"- `{match.relative_to(paths.wiki).as_posix()}`")
        if saved_paths:
            st.success("Saved answer: " + ", ".join(p.relative_to(paths.wiki).as_posix() for p in saved_paths))

with tab_lint:
    st.subheader("Lint The Wiki")
    st.write(
        "Run a health check over the generated wiki. Deterministic checks catch structural issues; "
        "the optional LLM review looks for contradictions, missing pages, and weak cross-links."
    )
    include_llm = st.checkbox("Include LLM review", value=has_llm_config(), disabled=not has_llm_config())
    save_report = st.checkbox("Save lint report back into the wiki", value=True)

    if st.button("Run lint", type="primary"):
        with st.spinner("Linting wiki..."):
            report = lint_wiki(paths, include_llm=include_llm, save=save_report)

        if report.findings:
            st.caption(f"{len(report.findings)} deterministic finding(s)")
            st.dataframe(
                [
                    {
                        "severity": finding.severity,
                        "category": finding.category,
                        "page": finding.page,
                        "finding": finding.message,
                        "suggestion": finding.suggestion,
                    }
                    for finding in report.findings
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.success("No deterministic lint issues found.")

        if include_llm:
            st.markdown("### LLM Review")
            st.markdown(report.llm_review or "No LLM review was returned.")
        elif not has_llm_config():
            st.info("Configure OpenAI or Azure OpenAI env vars to demo the LLM lint review.")

        if report.saved_path:
            st.success(f"Saved lint report: `{report.saved_path.relative_to(paths.wiki).as_posix()}`")
