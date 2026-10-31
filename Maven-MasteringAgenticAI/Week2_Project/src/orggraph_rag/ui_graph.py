"""Interactive graph rendering for the Streamlit app (vis-network inside an HTML component).

A plain HTML component (not streamlit-agraph) so the same graph can appear several times
on one page without duplicate-widget errors.
"""

import json
from typing import Dict, List

VIS_NETWORK_JS = "https://cdn.jsdelivr.net/npm/vis-network@9.1.9/standalone/umd/vis-network.min.js"

# Validated categorical palette, slots 1-7 in fixed order. With 7 kinds on one network
# every node also carries a visible name label, so identity is never colour-alone.
KIND_COLORS = {
    "Person": "#2a78d6",
    "Project": "#eb6834",
    "Team": "#1baf7a",
    "Skill": "#eda100",
    "Tool": "#e87ba4",
    "Document": "#008300",
    "Decision": "#6250d6",
}


def graph_html(nodes: List[Dict], edges: List[Dict], height: int = 380, highlight: List[str] = ()) -> str:
    """nodes: [{id, name, kind}], edges: [{source, target, type}] -> standalone HTML page."""
    highlight = set(highlight)
    vis_nodes = [{
        "id": n["id"],
        "label": n["name"] if len(n["name"]) <= 28 else n["name"][:26] + "…",
        "title": f"{n['kind']}: {n['name']} [{n['id']}]",
        "color": KIND_COLORS.get(n["kind"], "#888888"),
        "size": 22 if n["id"] in highlight else 14,
        "borderWidth": 3 if n["id"] in highlight else 1,
    } for n in nodes]
    vis_edges = [{"from": e["source"], "to": e["target"], "label": e["type"], "arrows": "to"} for e in edges]
    legend = "".join(
        f'<span><i style="background:{color}"></i>{kind}</span>'
        for kind, color in KIND_COLORS.items() if any(n["kind"] == kind for n in nodes)
    )
    # Script text is not HTML-decoded, so only "</" needs escaping (to keep </script> out).
    data = json.dumps({"nodes": vis_nodes, "edges": vis_edges}).replace("</", "<\\/")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<script src="{VIS_NETWORK_JS}"></script>
<style>
  :root {{ --fg: #31333F; --edge: #9AA0A6; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --fg: #FAFAFA; --edge: #6B7280; }} }}
  body {{ margin: 0; font: 12px system-ui, sans-serif; color: var(--fg); background: transparent; }}
  #net {{ height: {height - 28}px; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 10px; padding: 4px 2px; }}
  .legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; }}
</style></head><body>
<div class="legend">{legend}</div>
<div id="net"></div>
<script type="application/json" id="data">{data}</script>
<script>
  const css = getComputedStyle(document.documentElement);
  const fg = css.getPropertyValue('--fg').trim(), edge = css.getPropertyValue('--edge').trim();
  const data = JSON.parse(document.getElementById('data').textContent);
  const network = new vis.Network(document.getElementById('net'), data, {{
    nodes: {{ shape: 'dot', font: {{ color: fg, size: 12 }} }},
    edges: {{ color: edge, font: {{ color: fg, size: 9, strokeWidth: 0, align: 'middle' }},
             smooth: {{ type: 'dynamic' }} }},
    physics: {{ stabilization: {{ iterations: 200 }}, barnesHut: {{ springLength: 140 }} }},
    interaction: {{ hover: true, tooltipDelay: 100 }}
  }});
  // Fit again once physics settles (the first fit happens before the layout spreads out).
  network.once('stabilizationIterationsDone', () => network.fit({{ animation: false }}));
  window.addEventListener('resize', () => network.fit({{ animation: false }}));
</script></body></html>"""
