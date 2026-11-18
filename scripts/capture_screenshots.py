"""Screenshots of the web interface for the report and the slides -> docs/figures/ui-*.png.

Starts the web interface on a free port (or uses --url), drives it with Playwright in the installed Microsoft Edge
and saves one screenshot per view, at twice the CSS resolution.

The free-text intake view (ui-intake.png) replays a recorded reading of the example description instead of calling a
language model, so it needs no API key and always shows the same reading.

Usage (from the repository root):
    python -I scripts/capture_screenshots.py [--url http://127.0.0.1:8000] [--out DIR] [--only intake,case,...]
Views: consultation, case (with the proof graph and WHY NOT), kb, sparql, evaluation, intake.
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
# the free-text example of the README and the report (a 39-year-old man, breathless, swollen calf, recent surgery)
INTAKE_TEXT = ("Bapak 39 tahun, sejak pagi sesak napas berat. Betis kanan bengkak sejak tiga hari, dan dua minggu lalu "
               "operasi lutut. Tidak demam, tidak batuk.")
# the reading that qwen/qwen3.8-27b on Groq returned for INTAKE_TEXT in the live test of 18 November 2026; it is
# replayed for the screenshot, so the figure can be made again without an API key and shows exactly this reading
RECORDED_READING = {"provider": "groq", "model": "qwen/qwen3.8-27b", "seconds": 1.1, "reply": {
    "age": 39, "sex": "M", "chief_complaint": "E66", "findings": [
        {"code": "E66", "present": True, "quote": "sesak napas berat"},
        {"code": "E151", "present": True, "quote": "Betis kanan bengkak", "where": [{"part": "calf", "side": "R"}]},
        {"code": "E196", "present": True, "quote": "dua minggu lalu operasi lutut"},
        {"code": "E91", "present": False, "quote": "Tidak demam"},
        {"code": "E201", "present": False, "quote": "tidak batuk"}]}}


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


def replay_server():
    """The web interface in this process, with the intake replaying RECORDED_READING instead of calling a provider."""
    import threading

    import uvicorn
    sys.path.insert(0, str(ROOT / "src"))
    from ddxes import intake, llm
    from ddxes.web.app import create_app

    def read(kb, text, **_):
        res = intake.parse(kb, text, RECORDED_READING["reply"])
        res.provider, res.model, res.seconds = (RECORDED_READING[k] for k in ("provider", "model", "seconds"))
        return res
    intake.read = read
    llm.configured = lambda *a, **k: [(llm.PROVIDERS[0], RECORDED_READING["model"], "replayed")]
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    return f"http://127.0.0.1:{port}", server


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
    only = set(args[args.index("--only") + 1].split(",")) if "--only" in args else None
    want = lambda view: only is None or view in only  # noqa: E731
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
            if want("consultation"):
                page.goto(url + "/consult")
                settle(page)
                page.fill("#age", "63")
                page.select_option("#complaint", "E_66")
                page.get_by_role("button", name="Start consultation").click()
                settle(page)
                answer_questions(page, ["yes", "yes", "no"])
                shoot(page, "consultation", out)
                page.goto(url + "/consult")
                page.get_by_role("button", name="Start over").click()

            # 2. case analysis: the pulmonary-embolism patient, the proof tree and WHY NOT
            if want("case"):
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
            if want("kb"):
                page.goto(url + "/kb")
                settle(page)
                shoot(page, "kb", out)

            # 4. SPARQL: the provenance of a consultation written back to the graph
            if want("sparql"):
                page.goto(url + "/sparql?example=q08-provenance")
                settle(page)
                page.get_by_role("button", name="Run query").click()
                settle(page)
                page.evaluate("document.querySelector('.sparql').scrollIntoView({block: 'start'})")
                page.evaluate("window.scrollBy(0, -6)")
                shoot(page, "sparql", out)

            # 5. evaluation
            if want("evaluation"):
                page.goto(url + "/evaluation")
                settle(page)
                shoot(page, "evaluation", out)

            # 6. free-text intake: the confirmation page of the example description (recorded reading replayed)
            if want("intake"):
                rurl, server = replay_server()
                wait_ready(rurl + "/static/css/app.css")
                page.goto(rurl + "/consult")
                settle(page)
                page.fill("#description", INTAKE_TEXT)
                page.get_by_role("button", name="Read the description").click()
                page.wait_for_selector(".readings", timeout=120000)
                settle(page)
                for name, sel in (("intake", ".start"), ("intake-readings", ".readings")):   # the page, the card
                    page.locator(sel).screenshot(path=str(out / f"ui-{name}.png"))
                    print(f"wrote ui-{name}.png")
                server.should_exit = True
            browser.close()
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    main()
