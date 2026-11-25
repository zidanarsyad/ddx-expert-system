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
    for tab in ("how", "whynot", "concepts", "trace", "provenance", "sparql"):
        status, _, text = call(app, "GET", f"/cases?patient=T002456&tab={tab}")
        assert status == 200, tab
        assert "Pulmonary embolism" in text
    assert '<svg class="proof"' in call(app, "GET", "/cases?patient=T002456&tab=how")[2]
    sparql = call(app, "GET", "/cases?patient=T002456&tab=sparql")[2]
    assert "DiagnosisInference" in sparql and "T-PE" in sparql and "/sparql?patient=T002456&amp;query=" in sparql
    status, headers, ttl = call(app, "GET", "/cases/T002456/provenance.ttl")
    assert status == 200 and dict(headers)["content-type"].startswith("text/turtle") and "prov:" in ttl


def test_sparql(app):
    q12 = (PROCESSED.parent.parent / "queries" / "q12-ask-guideline.rq").read_text(encoding="utf-8")
    assert "Answer: yes" in call(app, "POST", "/sparql", form={"query": q12, "patient": ""})[2]
    assert "did not run" in call(app, "POST", "/sparql", form={"query": "SELECT nonsense", "patient": ""})[2]


def _keep_cookies(cookies, headers):
    """Update the cookie jar from Set-Cookie headers, as a browser does (the consultation lives in the cookie)."""
    for k, v in headers:
        if k == "set-cookie":
            name, _, rest = v.partition("=")
            value = rest.split(";")[0]
            if "max-age=0" in v.lower() or "expires=thu, 01 jan 1970" in v.lower():
                cookies.pop(name, None)
            else:
                cookies[name] = value
    return cookies


def _run_consultation(app, before_each=lambda: None):
    """Answer every question with no (or the first option) until the consultation ends; return its last page."""
    before_each()
    status, headers, _ = call(app, "POST", "/consult/start", form={"age": "63", "sex": "F", "complaint": "E_66"})
    assert status == 303
    cookies = _keep_cookies({}, headers)
    before_each()
    page = call(app, "GET", "/consult", cookies=cookies)[2]
    assert "Question 1" in page and "Why this question" in page
    assert "A 63-year-old woman. Main complaint: shortness of breath or difficulty breathing." in page
    for _ in range(30):
        m = re.search(r'name="evidence" value="([^"]+)"', page)
        if not m:
            break
        value = re.search(r'name="value"[^>]*value="([^"]+)"', page) or re.search(r'value="([^"]+)"[^>]*name="value"', page)
        form = {"evidence": m.group(1), "value": "no" if 'value="no"' in page else value.group(1)}
        before_each()
        status, headers, _ = call(app, "POST", "/consult/answer", form=form, cookies=cookies)
        assert status == 303
        _keep_cookies(cookies, headers)
        assert len(cookies["ddxes_session"]) < 4000
        before_each()
        page = call(app, "GET", "/consult", cookies=cookies)[2]
    assert "Consultation complete" in page and 'id="explanation"' in page
    return page, cookies


def test_consultation_runs_to_the_end(app):
    _page, cookies = _run_consultation(app)
    status, headers, _ = call(app, "POST", "/consult/undo", form={}, cookies=cookies)
    assert status == 303
    _keep_cookies(cookies, headers)
    assert "Question" in call(app, "GET", "/consult", cookies=cookies)[2]
    assert "SPARQL" in call(app, "GET", "/consult?explain=1&tab=sparql", cookies=cookies)[2]
    status, headers, _ = call(app, "POST", "/consult/reset", form={}, cookies=cookies)
    assert status == 303 and "ddxes_session" not in _keep_cookies(cookies, headers)
    assert "Start a consultation" in call(app, "GET", "/consult", cookies=cookies)[2]


def test_consultation_continues_in_a_fresh_process(app):
    """A serverless host may answer every request from a new process: the consultation is rebuilt from its cookie."""
    from ddxes.web import views as V
    sessions = V.Services.get().sessions
    kept, _ = _run_consultation(app)
    rebuilt, _ = _run_consultation(app, before_each=sessions.clear)
    strip = lambda page: re.sub(r"\s+", " ", page.split('id="explanation"')[0])  # noqa: E731
    assert strip(kept) == strip(rebuilt)


def test_tampered_cookie_starts_afresh(app):
    for bad in ("garbage", "eJxLTEoGAAJNASc", ""):
        status, _, page = call(app, "GET", "/consult", cookies={"ddxes_session": bad} if bad else None)
        assert status == 200 and "Start a consultation" in page


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


def test_language_model_key_sidebar(app, monkeypatch):
    """A key entered in the sidebar stays in the browser's cookie, is never shown, and is used for the intake."""
    from ddxes import intake, llm
    from test_intake import READING, TEXT
    monkeypatch.setattr(llm, "read_env_file", lambda *a, **k: {})            # no server key
    for var in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    page = call(app, "GET", "/consult")[2]
    assert "Language-model key" in page and "None: the free-text intake is off" in page
    assert "Or describe the patient in your own words" not in page

    key = "gsk_TEST-0123456789-abcd"
    status, headers, _ = call(app, "POST", "/settings/llm", form={"provider": "groq", "key": key, "model": "",
                                                                  "next": "/kb?tab=sources"})
    cookie = next(v for k, v in headers if k == "set-cookie")
    assert status == 303 and dict(headers)["location"] == "/kb?tab=sources"
    assert "httponly" in cookie.lower() and "samesite=strict" in cookie.lower()
    cookies = {"ddxes_llm": cookie.split(";")[0].split("=", 1)[1]}
    page = call(app, "GET", "/consult", cookies=cookies)[2]
    assert "Your Groq key ending in …abcd" in page and key not in page
    assert "Or describe the patient in your own words" in page

    seen = {}
    def fake_read(kb, text, **k):
        seen.update(k)
        return intake.parse(kb, TEXT, READING)
    monkeypatch.setattr(intake, "read", fake_read)
    status, _, page = call(app, "POST", "/consult/intake", form={"text": TEXT}, cookies=cookies)
    assert status == 200 and seen["providers"] == ["groq"] and seen["env"] == {"GROQ_API_KEY": key}

    status, headers, _ = call(app, "POST", "/settings/llm", form={"provider": "groq", "key": "short", "next": "/consult"})
    assert status == 303 and "llm_error=" in dict(headers)["location"]
    assert "does not look like an API key" in call(app, "GET", dict(headers)["location"])[2]
    assert call(app, "POST", "/settings/llm", form={"provider": "openrouter", "key": key, "next": "/"})[0] == 303
    status, headers, _ = call(app, "POST", "/settings/llm/clear", form={"next": "/consult"}, cookies=cookies)
    assert status == 303 and any(k == "set-cookie" and "ddxes_llm=" in v and "max-age=0" in v.lower() for k, v in headers)
    status, headers, _ = call(app, "POST", "/settings/llm", form={"provider": "groq", "key": key, "next": "//evil.example"})
    assert dict(headers)["location"] == "/consult"
