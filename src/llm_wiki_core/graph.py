from __future__ import annotations

from pathlib import Path

import networkx as nx
from pyvis.network import Network

from .config import Paths
from .wiki import list_pages, slugify

TYPE_COLORS = {
    "overview": "#60a5fa",
    "source": "#34d399",
    "concept": "#fbbf24",
    "entity": "#f472b6",
    "query": "#a78bfa",
    "note": "#cbd5e1",
}


def build_graph(paths: Paths) -> nx.Graph:
    pages = list_pages(paths)
    graph = nx.Graph()
    title_to_id: dict[str, str] = {}

    for page in pages:
        node_id = _node_id(paths, page.path)
        title_to_id[slugify(page.title)] = node_id
        title_to_id[slugify(page.path.stem)] = node_id
        graph.add_node(
            node_id,
            label=page.title,
            type=page.type,
            path=page.path.relative_to(paths.wiki).as_posix(),
        )

    for page in pages:
        source_id = _node_id(paths, page.path)
        for link in page.links:
            target_id = title_to_id.get(slugify(link))
            if target_id and target_id != source_id:
                graph.add_edge(source_id, target_id)

    return graph


def graph_html(paths: Paths) -> str:
    graph = build_graph(paths)
    net = Network(height="650px", width="100%", bgcolor="#0f172a", font_color="#e2e8f0")
    net.repulsion(node_distance=160, spring_length=160)

    for node_id, data in graph.nodes(data=True):
        degree = graph.degree[node_id]
        page_type = data.get("type", "note")
        net.add_node(
            node_id,
            label=data.get("label", node_id),
            title=f"{data.get('path')}<br>type: {page_type}<br>links: {degree}",
            color=TYPE_COLORS.get(page_type, "#cbd5e1"),
            value=max(1, degree + 1),
        )

    for source, target in graph.edges:
        net.add_edge(source, target, color="#94a3b8")

    if graph.number_of_nodes() == 0:
        net.add_node("empty", label="No wiki pages yet", color="#94a3b8")

    return net.generate_html(notebook=False)


def graph_stats(paths: Paths) -> dict[str, int]:
    graph = build_graph(paths)
    return {
        "pages": graph.number_of_nodes(),
        "links": graph.number_of_edges(),
        "components": nx.number_connected_components(graph) if graph.number_of_nodes() else 0,
    }


def _node_id(paths: Paths, path: Path) -> str:
    return path.relative_to(paths.wiki).as_posix()
