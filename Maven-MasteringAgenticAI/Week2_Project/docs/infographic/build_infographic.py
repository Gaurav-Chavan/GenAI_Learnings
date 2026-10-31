"""Build the 10-page study-guide infographic (HTML -> PDF via the installed Chrome).

    uv run python docs/infographic/build_infographic.py            # writes docs/OrgGraph_RAG_Infographic.pdf
    uv run python docs/infographic/build_infographic.py --preview  # also saves page PNGs to docs/infographic/preview/

Page layout and text live in infographic_template.html; this script fills in the diagrams and
charts (generated as SVG) and the evaluation numbers. Numbers come from eval/results.jsonl plus
the one documented human-review override (vector Q10, see docs/comparison_analysis.md).
Screenshots come from capture_screens.py.
"""

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
TEMPLATE = HERE / "infographic_template.html"
HTML_OUT = HERE / "infographic.html"
PDF_OUT = ROOT / "docs" / "OrgGraph_RAG_Infographic.pdf"

GRAPH, VECTOR = "#2a78d6", "#eb6834"
VIOLET, LAV_LINE, INK, INK2, GRID = "#6250d6", "#b9b0f0", "#1d1b2e", "#4a4760", "#e8e5f5"
HAND = "Patrick Hand, Comic Sans MS, cursive"
MONO = "JetBrains Mono, monospace"
KIND = {  # kind: (colour from the validated palette, emoji)
    "Person": ("#2a78d6", "👤"), "Project": ("#eb6834", "📁"), "Team": ("#1baf7a", "👥"),
    "Skill": ("#eda100", "🎯"), "Tool": ("#e87ba4", "🛠️"), "Document": ("#008300", "📄"),
    "Decision": ("#6250d6", "⚖️"),
}
HUMAN_REVIEW = {("Q10", "vector"): 1.0}  # judge error: the MFA date it flagged is correct

# ---------------------------------------------------------------- data


def load_metrics():
    rows = [json.loads(l) for l in (ROOT / "eval" / "results.jsonl").read_text(encoding="utf-8").splitlines() if l]
    for r in rows:
        r["correct_hr"] = HUMAN_REVIEW.get((r["id"], r["system"]), r["correctness"])
    by = {s: [r for r in rows if r["system"] == s] for s in ("graph", "vector")}
    m = {}
    for s, rs in by.items():
        lat = sorted(r["latency_s"] for r in rs)
        answered = [r for r in rs if not r["refused"]]
        m[s] = {
            "correct": statistics.mean(r["correct_hr"] for r in rs),
            "full": sum(r["correct_hr"] == 1 for r in rs),
            "recall": statistics.mean(r["entity_recall"] for r in rs if r["entity_recall"] is not None),
            "faith": statistics.mean(r["faithfulness"] for r in answered if r["faithfulness"] is not None),
            "refusal": sum(r["refusal_ok"] for r in rs),
            "median": statistics.median(lat),
            "p95": lat[max(0, round(0.95 * len(lat)) - 1)],
            "tokens": statistics.mean(r["input_tokens"] + r["output_tokens"] for r in rs),
            "n": len(rs),
        }
    cats = list(dict.fromkeys(r["category"] for r in by["graph"]))
    per_cat = {c: {s: statistics.mean(r["correct_hr"] for r in by[s] if r["category"] == c) for s in by} for c in cats}
    wins = defaultdict(int)
    vec = {r["id"]: r for r in by["vector"]}
    for g in by["graph"]:
        v = vec[g["id"]]
        wins["graph" if g["correct_hr"] > v["correct_hr"] else "vector" if v["correct_hr"] > g["correct_hr"] else "tie"] += 1
    return m, per_cat, wins


# ---------------------------------------------------------------- SVG pieces


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def bar(x, y, w, h, color):
    """Horizontal bar: 4px rounded data end, square baseline end."""
    if w <= 0:
        return ""
    r = min(4, h / 2, w)
    return (f'<path d="M{x},{y} H{x + w - r} Q{x + w},{y} {x + w},{y + r} V{y + h - r} '
            f'Q{x + w},{y + h} {x + w - r},{y + h} H{x} Z" fill="{color}"/>')


def grouped_bars(rows, width=520, label_w=150, bar_h=11, gap=2, group_gap=10, ticks=(0, 0.5, 1.0),
                 label_all=True):
    """rows: [(label, graph_value, vector_value, graph_text, vector_text)], values in 0..1."""
    plot_w = width - label_w - 44
    top = 6
    group_h = 2 * bar_h + gap
    base_y = top + len(rows) * (group_h + group_gap) - group_gap + 6
    height = base_y + 18
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    for t in ticks:
        x = label_w + plot_w * t
        out.append(f'<line x1="{x:.1f}" y1="{top - 3}" x2="{x:.1f}" y2="{base_y}" stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{x:.1f}" y="{base_y + 13}" font-size="10" font-family="{HAND}" fill="{INK2}" '
                   f'text-anchor="middle">{t:g}</text>')
    for i, (label, gv, vv, gt, vt) in enumerate(rows):
        y = top + i * (group_h + group_gap)
        out.append(f'<text x="{label_w - 8}" y="{y + group_h / 2 + 4}" font-size="12" font-family="{HAND}" '
                   f'fill="{INK}" text-anchor="end">{esc(label)}</text>')
        for j, (val, txt, color) in enumerate([(gv, gt, GRAPH), (vv, vt, VECTOR)]):
            by = y + j * (bar_h + gap)
            w = plot_w * val
            out.append(bar(label_w, by, w, bar_h, color))
            if label_all or val < 1:  # selective labels: only the bars that differ from a perfect score
                out.append(f'<text x="{label_w + w + 5:.1f}" y="{by + bar_h - 1.5}" font-size="11" font-family="{HAND}" '
                           f'fill="{INK2}">{esc(txt)}</text>')
    out.append(f'<line x1="{label_w}" y1="{top - 3}" x2="{label_w}" y2="{base_y}" stroke="#b5b1c8" stroke-width="1"/>')
    out.append("</svg>")
    return "".join(out)


def marker(ident, color):
    return (f'<marker id="{ident}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{color}"/></marker>')


def schema_svg():
    """Graph schema: one bubble per node kind (emoji + count) and the 10 relationship types."""
    nodes = {"Team": (85, 70, 7), "Person": (205, 205, 8), "Skill": (85, 345, 6), "Decision": (450, 60, 5),
             "Project": (460, 265, 5), "Document": (690, 175, 6), "Tool": (690, 360, 7)}
    r = 34
    edges = [("Person", "Team", "MEMBER_OF", -8, -6), ("Person", "Skill", "HAS_SKILL {level}", 4, 2),
             ("Person", "Decision", "APPROVED", -16, -8), ("Person", "Project", "WORKED_ON {role}", 2, 16),
             ("Person", "Document", "AUTHORED", 78, -6), ("Decision", "Project", "DECISION_FOR", -50, -8),
             ("Decision", "Document", "BASED_ON", 22, -8), ("Document", "Project", "ABOUT", 10, -8),
             ("Project", "Tool", "USED_TOOL", 18, -6)]
    out = [f'<svg viewBox="0 0 780 420" width="100%" role="img"><defs>{marker("sa", VIOLET)}</defs>']
    for s, d, label, dx, dy in edges:
        (x1, y1, _), (x2, y2, _) = nodes[s], nodes[d]
        a = math.atan2(y2 - y1, x2 - x1)
        sx, sy, ex, ey = x1 + r * math.cos(a), y1 + r * math.sin(a), x2 - (r + 4) * math.cos(a), y2 - (r + 4) * math.sin(a)
        out.append(f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{ex:.1f}" y2="{ey:.1f}" stroke="{VIOLET}" '
                   f'stroke-width="1.7" marker-end="url(#sa)"/>')
        out.append(f'<text x="{(sx + ex) / 2 + dx:.1f}" y="{(sy + ey) / 2 + dy:.1f}" font-size="10.5" font-family="{MONO}" '
                   f'fill="{INK2}" text-anchor="middle" paint-order="stroke" stroke="#ffffff" stroke-width="4">{label}</text>')
    px, py, _ = nodes["Person"]
    out.append(f'<path d="M{px - 26},{py - 22} C{px - 115},{py - 75} {px - 120},{py + 35} {px - 33},{py + 10}" '
               f'fill="none" stroke="{VIOLET}" stroke-width="1.7" marker-end="url(#sa)"/>')
    out.append(f'<text x="{px - 92}" y="{py - 48}" font-size="10.5" font-family="{MONO}" fill="{INK2}" '
               f'text-anchor="middle">REPORTS_TO</text>')
    for kind, (x, y, n) in nodes.items():
        color, emoji = KIND[kind]
        out.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{color}" fill-opacity="0.16" stroke="{color}" stroke-width="2.2"/>')
        out.append(f'<text x="{x}" y="{y + 9}" font-size="25" text-anchor="middle">{emoji}</text>')
        out.append(f'<text x="{x}" y="{y + r + 17}" font-size="14" font-family="{HAND}" fill="{INK}" '
                   f'text-anchor="middle">{kind} · {n}</text>')
    out.append("</svg>")
    return "".join(out)


def pipeline_svg():
    """The LangGraph GraphRAG pipeline with its conditional edges."""
    W, H = 150, 54
    boxes = {  # name: (x, y, emoji, subtitle, fill)
        "router": (20, 150, "🔀", "gpt-4o-mini · classify", "#f1eefc"),
        "write_cypher": (230, 40, "✍️", "gpt-4o · Text2Cypher", "#eaf2fd"),
        "run_cypher": (450, 40, "🗄️", "Neo4j · READ tx", "#fdf3dc"),
        "grade": (670, 40, "⚖️", "gpt-4o-mini · enough?", "#eaf2fd"),
        "anchor": (450, 250, "⚓", "full-text + vector seeds", "#fdf3dc"),
        "answer": (670, 250, "💬", "gpt-4o-mini · [id] cites", "#e8f6ee"),
    }

    def port(name, side):
        x, y, *_ = boxes[name]
        return {"l": (x, y + H / 2), "r": (x + W, y + H / 2), "t": (x + W / 2, y), "b": (x + W / 2, y + H)}[side]

    def edge(a, b, label="", dashed=False, lx=0, ly=-6, via=None):
        (x1, y1), (x2, y2) = a, b
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        d = f"M{x1},{y1} Q{via[0]},{via[1]} {x2},{y2}" if via else f"M{x1},{y1} L{x2},{y2}"
        s = f'<path d="{d}" fill="none" stroke="{VIOLET}" stroke-width="1.7"{dash} marker-end="url(#pa)"/>'
        if label:
            mx, my = ((via[0], via[1]) if via else ((x1 + x2) / 2, (y1 + y2) / 2))
            s += (f'<text x="{mx + lx:.0f}" y="{my + ly:.0f}" font-size="12.5" font-family="{HAND}" fill="{INK2}" '
                  f'text-anchor="middle" paint-order="stroke" stroke="#ffffff" stroke-width="4">{label}</text>')
        return s

    out = [f'<svg viewBox="0 0 840 372" width="100%" role="img"><defs>{marker("pa", VIOLET)}</defs>']
    out.append(edge(port("router", "r"), port("write_cypher", "l"), "graph", lx=-14, ly=-2))
    out.append(edge(port("router", "r"), port("anchor", "l"), "semantic", lx=-30, ly=20))
    out.append(edge(port("router", "b"), (port("answer", "b")[0] - 30, 304), "out of scope → refuse 🙅",
                    dashed=True, via=(380, 355), ly=-16))
    out.append(edge(port("write_cypher", "r"), port("run_cypher", "l")))
    out.append(edge(port("run_cypher", "t"), port("write_cypher", "t"), "error → retry once 🔁", via=(415, -10), ly=16))
    out.append(edge(port("run_cypher", "r"), port("grade", "l"), "rows"))
    out.append(edge(port("run_cypher", "b"), port("anchor", "t"), "0 rows / failed", lx=56, ly=2))
    out.append(edge(port("grade", "b"), port("answer", "t"), "sufficient ✅", lx=48, ly=2))
    out.append(edge((port("grade", "b")[0] - 40, 94), (port("anchor", "t")[0] + 45, 250), "insufficient",
                    lx=-6, ly=-16))
    out.append(edge(port("anchor", "r"), port("answer", "l"), "+ context"))
    for name, (x, y, emoji, sub, fill) in boxes.items():
        out.append(f'<rect x="{x}" y="{y}" width="{W}" height="{H}" rx="12" fill="{fill}" stroke="{LAV_LINE}" stroke-width="1.8"/>')
        out.append(f'<text x="{x + W / 2}" y="{y + 23}" font-size="13" font-weight="700" font-family="{MONO}" '
                   f'fill="{INK}" text-anchor="middle">{emoji} {name}</text>')
        out.append(f'<text x="{x + W / 2}" y="{y + 42}" font-size="12" font-family="{HAND}" fill="{INK2}" '
                   f'text-anchor="middle">{sub}</text>')
    out.append("</svg>")
    return "".join(out)


def topk_svg(total=44, k=4, cols=11):
    """44 entity cards, only the top-k reach the answer model."""
    rows = math.ceil(total / cols)
    out = [f'<svg viewBox="0 0 {cols * 22 + 4} {rows * 22 + 4}" width="100%" role="img">']
    for i in range(total):
        x, y = 4 + (i % cols) * 22, 4 + (i // cols) * 22
        hit = i in (3, 15, 18, 30)
        out.append(f'<rect x="{x}" y="{y}" width="16" height="16" rx="4" fill="{VECTOR if hit else "#ffffff"}" '
                   f'stroke="{VECTOR if hit else LAV_LINE}" stroke-width="1.5"/>')
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------- assemble


def build_html():
    m, per_cat, wins = load_metrics()
    g, v = m["graph"], m["vector"]
    n = g["n"]
    overall = grouped_bars([
        ("✅ Correctness", g["correct"], v["correct"], f"{g['correct']:.2f}", f"{v['correct']:.2f}"),
        ("💯 Fully correct", g["full"] / n, v["full"] / n, f"{g['full']}/{n}", f"{v['full']}/{n}"),
        ("🎯 Entity recall", g["recall"], v["recall"], f"{g['recall']:.2f}", f"{v['recall']:.2f}"),
        ("🔗 Faithfulness", g["faith"], v["faith"], f"{g['faith']:.2f}", f"{v['faith']:.2f}"),
        ("🙅 Right refusals", g["refusal"] / n, v["refusal"] / n, f"{g['refusal']}/{n}", f"{v['refusal']}/{n}"),
    ], width=330, label_w=104, bar_h=12, group_gap=10)
    categories = grouped_bars(
        [(c, s["graph"], s["vector"], f"{s['graph']:.2f}", f"{s['vector']:.2f}") for c, s in per_cat.items()],
        width=340, label_w=120, bar_h=9, gap=2, group_gap=7, label_all=False)
    values = {
        "SCHEMA": schema_svg(), "PIPELINE": pipeline_svg(), "TOPK": topk_svg(),
        "OVERALL": overall, "CATEGORIES": categories,
        "G_CORRECT": f"{g['correct']:.2f}", "V_CORRECT": f"{v['correct']:.2f}",
        "G_FULL": str(g["full"]), "V_FULL": str(v["full"]), "N": str(n),
        "G_MEDIAN": f"{g['median']:.1f}", "V_MEDIAN": f"{v['median']:.1f}",
        "G_P95": f"{g['p95']:.1f}", "V_P95": f"{v['p95']:.1f}",
        "G_TOKENS": f"{g['tokens']:,.0f}", "V_TOKENS": f"{v['tokens']:,.0f}",
        "SPEEDUP": f"{g['median'] / v['median']:.1f}", "TOKEN_RATIO": f"{g['tokens'] / v['tokens']:.1f}",
        "WINS_G": str(wins["graph"]), "WINS_T": str(wins["tie"]), "WINS_V": str(wins["vector"]),
    }
    html = TEMPLATE.read_text(encoding="utf-8")
    for key, val in values.items():
        html = html.replace(f"@@{key}@@", val)
    assert "@@" not in html, "unfilled placeholder in template"
    return html


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true", help="also save a PNG of every page")
    args = parser.parse_args()

    HTML_OUT.write_text(build_html(), encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 1180, "height": 840}, device_scale_factor=2)
        page.goto(HTML_OUT.as_uri(), wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        if args.preview:
            out = HERE / "preview"
            out.mkdir(exist_ok=True)
            for old in out.glob("*.png"):
                old.unlink()
            for i, section in enumerate(page.locator("section.pg").all(), start=1):
                section.screenshot(path=out / f"page_{i:02d}.png")
        page.pdf(path=str(PDF_OUT), width="297mm", height="210mm", print_background=True,
                 prefer_css_page_size=True, margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
        browser.close()
    print(f"Wrote {PDF_OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
