"""Screenshots of the web interface for the report and the slides -> docs/figures/ui-*.png.

Starts the web interface on a free port (or uses --url), drives it with Playwright in the installed Microsoft Edge
and saves one screenshot per view, at twice the CSS resolution.

Usage (from the repository root):  python -I scripts/capture_screenshots.py [--url http://127.0.0.1:8000] [--out DIR]
"""
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "figures"
PATIENT = "T002456"          # the pulmonary-embolism patient of the report's worked example


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_ready(url, seconds=120):
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            urllib.request.urlopen(url, timeout=3).close()
            return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"{url} did not start")


def settle(page):
    page.wait_for_load_state("networkidle")
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(250)


def answer_questions(page, answers):
    """Answer yes/no questions from `answers`; tick nothing on lists, pick 6 on a scale, the first option otherwise."""
    for ans in answers:
        if not page.locator('input[name="evidence"]').count():
            return
        if page.locator('button[name="value"][value="yes"]').count():
            page.locator(f'button[name="value"][value="{ans}"]').click()
        elif page.locator(".scale__btn").count():
            page.locator('.scale__btn[value="6"]').click()
        elif page.locator('input[type="radio"][name="value"]').count():
            page.locator('input[type="radio"][name="value"]').first.check()
            page.get_by_role("button", name="Answer", exact=True).click()
        else:
            page.get_by_role("button", name="Answer", exact=True).click()
        settle(page)


def shoot(page, name, out, full=False):
    path = out / f"ui-{name}.png"
    page.screenshot(path=str(path), full_page=full)
    print(f"wrote {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")


def main():
    args = sys.argv[1:]
    url = args[args.index("--url") + 1] if "--url" in args else None
    out = pathlib.Path(args[args.index("--out") + 1]) if "--out" in args else OUT
    out.mkdir(parents=True, exist_ok=True)
    proc = None
    if not url:
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        proc = subprocess.Popen([sys.executable, "-m", "ddxes.web", "--port", str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_ready(url + "/static/css/app.css")
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge")
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2, color_scheme="light")

            # 1. consultation: a 63-year-old woman short of breath, a few answers in
            page.goto(url + "/consult")
            settle(page)
            page.fill("#age", "63")
            page.select_option("#complaint", "E_66")
            page.get_by_role("button", name="Start consultation").click()
            settle(page)
            answer_questions(page, ["yes", "yes", "no"])
            shoot(page, "consultation", out)

            # 2. case analysis: the pulmonary-embolism patient, the proof tree and WHY NOT
            page.goto(f"{url}/cases?patient={PATIENT}")
            settle(page)
            shoot(page, "case", out)
            # the figures show the whole proof tree, scaled to the column (on screen a wide tree scrolls instead)
            page.evaluate("document.querySelectorAll('svg.proof').forEach(s => { s.style.width = '100%'; })")
            page.evaluate("document.getElementById('explanation').scrollIntoView({block: 'start'})")
            settle(page)
            shoot(page, "case-proof", out)
            page.locator(".proof-wrap").first.screenshot(path=str(out / "ui-proof-graph.png"))
            print("wrote ui-proof-graph.png")
            page.goto(f"{url}/cases?patient={PATIENT}&tab=whynot#explanation")
            settle(page)
            page.evaluate("document.getElementById('explanation').scrollIntoView({block: 'start'})")
            shoot(page, "whynot", out)

            # 3. knowledge base
            page.goto(url + "/kb")
            settle(page)
            shoot(page, "kb", out)

            # 4. SPARQL: the provenance of a consultation written back to the graph
            page.goto(url + "/sparql?example=q08-provenance")
            settle(page)
            page.get_by_role("button", name="Run query").click()
            settle(page)
            page.evaluate("document.querySelector('.sparql').scrollIntoView({block: 'start'})")
            page.evaluate("window.scrollBy(0, -6)")
            shoot(page, "sparql", out)

            # 5. evaluation
            page.goto(url + "/evaluation")
            settle(page)
            shoot(page, "evaluation", out)
            browser.close()
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    main()
