"""Smoke tests of the web interface: every page renders and a consultation runs from start to finish.

A minimal ASGI driver keeps the test free of an HTTP client dependency.
"""
import asyncio
import re
import urllib.parse

import pytest

from ddxes.paths import PROCESSED, RESULTS

pytestmark = pytest.mark.skipif(not (PROCESSED / "sample_patients.json").exists(), reason="demo patients not built")


def call(app, method, path, form=None, cookies=None):
    path_only, _, query = path.partition("?")
    body = urllib.parse.urlencode(form or {}, doseq=True).encode()
    headers = [(b"host", b"testserver")]
    if form is not None:
        headers.append((b"content-type", b"application/x-www-form-urlencoded"))
    if cookies:
        headers.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": path_only, "raw_path": path_only.encode(), "query_string": query.encode(), "root_path": "",
             "headers": headers, "client": ("127.0.0.1", 50000), "server": ("testserver", 80)}
    inbox = [{"type": "http.request", "body": body, "more_body": False}]
    sent = []

    async def receive():
        if inbox:
            return inbox.pop(0)
        await asyncio.Event().wait()     # the client stays connected until the response is complete

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = [(k.decode().lower(), v.decode()) for k, v in start["headers"]]
    text = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body").decode("utf-8", "replace")
    return start["status"], headers, text


@pytest.fixture(scope="module")
def app():
    from ddxes.web.app import create_app
    return create_app()


def test_pages_render(app):
    for path, needle in [("/consult", "Start a consultation"), ("/cases", "Case analysis"), ("/kb", "Knowledge base"),
                         ("/kb?tab=pathologies&d=Pneumonia", "Pneumonia"), ("/kb?tab=sources", "Wells"),
                         ("/kb?f=1&cat=triage&fid=faithful&fid=partial&fid=proxy&q=sepsis", "Kind of rule"),
                         ("/sparql", "Query the knowledge graph"), ("/static/css/app.css", "--ink")]:
        status, _, text = call(app, "GET", path)
        assert status == 200, path
        assert needle in text, path
    if (RESULTS / "main.json").exists():
        assert "Diagnostic accuracy" in call(app, "GET", "/evaluation")[2]


def test_case_explanations(app):
    for tab in ("how", "whynot", "concepts", "trace", "provenance"):
        status, _, text = call(app, "GET", f"/cases?patient=T002456&tab={tab}")
        assert status == 200, tab
        assert "Pulmonary embolism" in text
    assert '<svg class="proof"' in call(app, "GET", "/cases?patient=T002456&tab=how")[2]
    status, headers, ttl = call(app, "GET", "/cases/T002456/provenance.ttl")
    assert status == 200 and dict(headers)["content-type"].startswith("text/turtle") and "prov:" in ttl


def test_sparql(app):
    q12 = (PROCESSED.parent.parent / "queries" / "q12-ask-guideline.rq").read_text(encoding="utf-8")
    assert "Answer: yes" in call(app, "POST", "/sparql", form={"query": q12, "patient": ""})[2]
    assert "did not run" in call(app, "POST", "/sparql", form={"query": "SELECT nonsense", "patient": ""})[2]


def test_consultation_runs_to_the_end(app):
    status, headers, _ = call(app, "POST", "/consult/start", form={"age": "63", "sex": "F", "complaint": "E_66"})
    assert status == 303
    sid = next(v for k, v in headers if k == "set-cookie").split(";")[0].split("=", 1)[1]
    cookies = {"ddxes_session": sid}
    page = call(app, "GET", "/consult", cookies=cookies)[2]
    assert "Question 1" in page and "Why this question" in page
    assert "A 63-year-old woman. Main complaint: shortness of breath or difficulty breathing." in page
    for _ in range(30):
        m = re.search(r'name="evidence" value="([^"]+)"', page)
        if not m:
            break
        value = re.search(r'name="value"[^>]*value="([^"]+)"', page) or re.search(r'value="([^"]+)"[^>]*name="value"', page)
        form = {"evidence": m.group(1), "value": "no" if 'value="no"' in page else value.group(1)}
        assert call(app, "POST", "/consult/answer", form=form, cookies=cookies)[0] == 303
        page = call(app, "GET", "/consult", cookies=cookies)[2]
    assert "Consultation complete" in page and 'id="explanation"' in page
    assert call(app, "POST", "/consult/undo", form={}, cookies=cookies)[0] == 303
    assert "Question" in call(app, "GET", "/consult", cookies=cookies)[2]


def test_free_text_intake_flow(app, monkeypatch):
    """Description -> reading to confirm -> consultation that starts from the confirmed answers (model stubbed)."""
    from ddxes import intake, llm
    from test_intake import READING, TEXT
    monkeypatch.setattr(llm, "configured", lambda *a, **k: [(llm.PROVIDERS[0], "test-model", "not-a-key")])
    assert "Or describe the patient in your own words" in call(app, "GET", "/consult")[2]
    monkeypatch.setattr(intake, "read", lambda kb, text, **k: intake.parse(kb, TEXT, READING))
    status, _, page = call(app, "POST", "/consult/intake", form={"text": TEXT})
    assert status == 200 and "Check what the description says" in page and "really short of breath" in page
    tokens = re.findall(r'name="finding" value="([^"]+)"', page)
    assert {"E_66=yes", "E_151=yes", "E_196=yes", "E_91=no"} <= set(tokens)
    form = {"age": "39", "sex": "M", "complaint": "E_66", "finding": [t for t in tokens if t != "E_196=yes"],
            "description": TEXT}
    status, headers, _ = call(app, "POST", "/consult/start", form=form)
    sid = next(v for k, v in headers if k == "set-cookie").split(";")[0].split("=", 1)[1]
    page = call(app, "GET", "/consult", cookies={"ddxes_session": sid})[2]
    assert "From the description" in page and "Where is the swelling located?" in page
    assert "Have you had surgery within the last month?</span>" not in page      # unticked: asked, not prefilled
