"""Capture Streamlit UI screenshots for the infographic (needs the app running on :8502).

    uv run streamlit run app.py --server.port 8502
    uv run python docs/infographic/capture_screens.py

Uses the installed Google Chrome through Playwright (no browser download).
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

APP = "http://localhost:8502"
OUT = Path(__file__).parent / "screens"
QUESTION = "Who does Bob report to, and what decisions has that person approved?"
HIDE_CHROME = '[data-testid="stToolbar"], [data-testid="stDecoration"] { display: none !important; }'


def settle(page, seconds: float = 1.5):
    page.wait_for_timeout(seconds * 1000)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=2,
                                color_scheme="light")

        # ---- Chat: side-by-side answer to a two-hop question
        page.goto(APP)
        page.get_by_text("Try a question").wait_for(timeout=60_000)
        page.add_style_tag(content=HIDE_CHROME)
        page.get_by_role("button", name=QUESTION).click()
        page.get_by_text("route: graph").wait_for(timeout=90_000)
        page.wait_for_function("document.querySelectorAll('[data-testid=\"stExpander\"]').length >= 4")
        settle(page)
        page.screenshot(path=OUT / "chat_side_by_side.png")

        # Close-ups: tall viewport and no sticky chat box, so nothing clips the expanders.
        page.add_style_tag(content='[data-testid="stBottom"] { display: none !important; }')
        page.set_viewport_size({"width": 1440, "height": 3400})
        settle(page)

        # ---- "How this answer was found" (GraphRAG column)
        how = page.locator('[data-testid="stExpander"]').filter(has_text="How this answer was found").first
        how.locator("summary").click()
        settle(page)
        how.screenshot(path=OUT / "chat_how_found.png")
        how.locator("summary").click()

        # ---- Graph behind the answer (GraphRAG column, then vector column)
        graphs = page.locator('[data-testid="stExpander"]').filter(has_text="Graph behind the answer")
        for i, name in enumerate(["chat_graph_graphrag.png", "chat_graph_vector.png"]):
            exp = graphs.nth(i)
            exp.locator("summary").click()
            settle(page, 4)
            exp.screenshot(path=OUT / name)

        # ---- Evaluation page
        page.set_viewport_size({"width": 1440, "height": 1100})
        page.goto(f"{APP}/evaluation")
        page.locator('[data-testid="stMetricValue"]').first.wait_for(timeout=60_000)
        page.locator('[data-testid="stVegaLiteChart"] canvas, [data-testid="stVegaLiteChart"] svg').first.wait_for(
            timeout=60_000)
        page.add_style_tag(content=HIDE_CHROME)
        settle(page, 3)
        page.screenshot(path=OUT / "eval_top.png")
        page.locator('[data-testid="stVegaLiteChart"]').first.scroll_into_view_if_needed()
        settle(page, 1.5)
        page.locator('[data-testid="stVegaLiteChart"]').first.screenshot(path=OUT / "eval_chart.png")

        # ---- Knowledge graph page
        page.goto(f"{APP}/graph")
        page.get_by_text("relationships in Neo4j").wait_for(timeout=60_000)
        page.add_style_tag(content=HIDE_CHROME)
        settle(page, 6)
        page.screenshot(path=OUT / "graph_page.png")

        browser.close()
    print("Saved:", ", ".join(sorted(f.name for f in OUT.glob("*.png"))))


if __name__ == "__main__":
    main()
