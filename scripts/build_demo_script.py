"""Build docs/demo_script.pdf: the live demo on one A4 page, with the exact inputs and what each step should show.

The page is docs/demo_script.html.j2 (Jinja2). Its questions, labels and numbers are not typed in by hand: this script
drives the web interface in-process along the demo (the questionnaire consultation, the same patient from free text,
case T002456, the knowledge base and SPARQL example q05) and reads them off the pages, so the printed script matches
what the app shows. The free-text step replays the reading recorded in scripts/capture_screenshots.py, so it needs no
API key and uses no quota. If the demo path has changed (another question, another conclusion), the build stops rather
than print a wrong script. Playwright prints the page with Microsoft Edge, and the build checks that it fits one page.

    python scripts/build_demo_script.py [--copy-to PATH]

Needs the `docs` extra (playwright) and Microsoft Edge.
"""
import asyncio
import csv
import datetime
import html
import json
import pathlib
import re
import shutil
import sys
import urllib.parse

import jinja2
from markupsafe import Markup
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import build_explainer as bx  # noqa: E402  (fonts and number formats shared with the explainer)
import capture_screenshots as cs  # noqa: E402  (the demo sentence and its recorded reading)

SOURCE = ROOT / "docs" / "demo_script.html.j2"
BUILD = ROOT / "docs" / "build" / "demo_script"
OUT = ROOT / "docs" / "demo_script.pdf"
FONT_FACES = bx.FONT_FACES + [("IBM Plex Mono", "IBMPlexMono-Bold", 700, "normal")]
DEMO_PATIENT = "T056557"            # the questionnaire step gives this test patient's answers
CASE = "T002456"                    # the case-analysis step
PLAN = ["yes", "Calf (R)", "yes", "no", "no"]
SHORT = {"E_66": "shortness of breath", "E_151": "swelling", "E_152": "where", "E_196": "surgery in the last month",
         "E_91": "fever", "E_201": "cough"}
GRAPH_IRI = "http://example.org/ddx#Rule_D-PE-1"
OPTION = re.compile(r'<label class="choice"><input type="(?:checkbox|radio)" name="value" value="([^"]+)"[^>]*>'
                    r'<span>([^<]*)</span>')
WHY_TEST = re.compile(r"rule out (?P<dx>.+?) \(P = (?P<p>[\d.]+), severity \d\)\. Rule (?P<rule>\S+) \((?P<cite>.+?)\) "
                      r"needs this answer.*?information gain: (?P<bits>[\d.]+) bits")
WHY_RULE_OUT = re.compile(r"I must rule out (?P<dx>.+?), a severity-(?P<severity>\d) condition")


def need(ok, what):
    if not ok:
        sys.exit(f"build_demo_script: the demo path has changed ({what}). Check the app before printing the script.")


def call(app, method, path, form=None, cookies=None):
    """One request to the ASGI app, without a server or an HTTP client (the driver of tests/test_web.py)."""
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
    inbox, sent = [{"type": "http.request", "body": body, "more_body": False}], []

    async def receive():
        if inbox:
            return inbox.pop(0)
        await asyncio.Event().wait()     # the client stays connected until the response is complete

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    head = [(k.decode().lower(), v.decode()) for k, v in start["headers"]]
    return start["status"], head, b"".join(m.get("body", b"") for m in sent
                                           if m["type"] == "http.response.body").decode("utf-8", "replace")


def get(app, path, cookies=None):
    status, _, page = call(app, "GET", path, cookies=cookies)
    need(status == 200, f"GET {path} returned {status}")
    return page


def session(headers):
    return {"ddxes_session": next(v for k, v in headers if k == "set-cookie").split(";")[0].split("=", 1)[1]}


def plain(fragment):
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()
    return re.sub(r"\s+([,.;:)])", r"\1", text)


def find(page, cls, tag=r"\w+", every=False):
    hits = [plain(m.group(2)) for m in re.finditer(rf'<({tag})[^>]*class="[^"]*\b{cls}\b[^"]*"[^>]*>(.*?)</\1>',
                                                   page, re.S)]
    return hits if every else (hits[0] if hits else None)


def selected(page, select_id):
    m = re.search(rf'<select id="{select_id}".*?<option value="([^"]+)" selected>([^<]*)', page, re.S)
    return (m.group(1), html.unescape(m.group(2))) if m else (None, None)


def consult(app, cookies, plan):
    """Answer the consultation with `plan` (yes, no or an option label) and return what each question showed."""
    page = get(app, "/consult", cookies)
    start, asked = find(page, "tag__level"), []
    for want in plan:
        ev = re.search(r'name="evidence" value="([^"]+)"', page)
        need(ev, f"question {len(asked) + 1} was expected, the consultation ended")
        q = dict(n=len(asked) + 1, evidence=ev.group(1), text=find(page, "question__text"), why=find(page, "why__text"),
                 rule_out="phase--rule-out" in page, option_number=None)
        purpose = (WHY_RULE_OUT if q["rule_out"] else WHY_TEST).search(q["why"])
        need(purpose, f"the reason shown for question {q['n']}")
        q["purpose"] = purpose.groupdict()
        if want in ("yes", "no"):
            value, q["answer"], q["key"] = want, want.capitalize(), want[0].upper()
        else:
            options = [(v, html.unescape(label)) for v, label in OPTION.findall(page)]
            number = next((i for i, (_, label) in enumerate(options, 1) if label == want), None)
            need(number, f"question {q['n']} has no option {want!r}")
            value, q["answer"], q["key"], q["option_number"] = options[number - 1][0], want, None, number
        q["value"] = value
        status = call(app, "POST", "/consult/answer", form={"evidence": q["evidence"], "value": value}, cookies=cookies)[0]
        need(status == 303, f"the answer to question {q['n']} was refused")
        page = get(app, "/consult", cookies)
        q["triage"], q["change"] = find(page, "tag__level"), find(page, "tag__change")
        asked.append(q)
    need('name="evidence"' not in page, f"the consultation asks more than {len(plan)} questions")
    return start, asked, page


def conclusion(app, cookies, page):
    how = get(app, "/consult?explain=1&tab=how", cookies)
    concepts = get(app, "/consult?explain=1&tab=concepts", cookies)
    facts = dict(re.findall(r"<div><dt>(.*?)</dt><dd>(.*?)</dd></div>", how))
    rec = re.search(r'<span class="recs__text">(.*?)</span>\s*<span class="recs__src">(.*?)</span>', how, re.S)
    wells = re.search(r'<p class="score">Wells score[^<]*= <b>([^<]+)</b>', concepts)
    need(rec and wells, "no recommendation or Wells score")
    return dict(done=find(page, "question__text"), label=selected(how, "how-d")[1], p=facts["Probability"],
                triage=find(page, "tag__level"), rec=plain(rec.group(1)), rec_src=plain(rec.group(2)),
                wells=wells.group(1), rule=find(how, "rule__id", "b"), tabs=re.findall(r'<a class="tab"[^>]*>(.*?)</a>', how))


def questionnaire(app):
    page = get(app, "/consult")
    complaint = re.search(r'<option value="E_66"[^>]*>([^<]*)</option>', page)
    status, headers, _ = call(app, "POST", "/consult/start", form={"age": "39", "sex": "M", "complaint": "E_66"})
    need(status == 303, "the consultation did not start")
    cookies = session(headers)
    start, asked, end = consult(app, cookies, PLAN)
    record = next(p for p in bx.jload("data/processed/sample_patients.json") if p["id"] == DEMO_PATIENT)
    need((record["age"], record["sex"], record["initial"]) == (39, "M", "E_66"), f"{DEMO_PATIENT}'s profile")
    findings = {tuple(f) for f in record["findings"]}
    for q in asked:                  # the answers are the ones recorded for the demo patient
        if q["value"] in ("yes", "no"):
            same = (q["evidence"] in record["binary"]) == (q["value"] == "yes")
        else:
            same = (q["evidence"], q["value"]) in findings
        need(same, f"{DEMO_PATIENT} answers question {q['n']} differently")
    emergency = next((q for q in asked if q["triage"] == "Emergency"), None)
    need(emergency and emergency["change"], "the triage never turns Emergency")
    need(any(q["rule_out"] for q in asked), "no rule-out question")
    return dict(complaint=html.unescape(complaint.group(1)), start=start, asked=asked, end=conclusion(app, cookies, end),
                emergency_at=emergency["n"], emergency=emergency["triage"], cli=terminal_answers(asked))


def terminal_answers(asked):
    """What to type in `ddxes consult` for the same answers. The terminal numbers the options in the knowledge base's
    order, while the web interface sorts long lists alphabetically, so the numbers come from the engine itself."""
    from ddxes.consult import Consultation
    from ddxes.engine import InferenceEngine
    from ddxes.kb import KnowledgeBase
    cons = Consultation(InferenceEngine(KnowledgeBase.load()), age=39, sex="M", chief_complaint="E_66")
    typed = []
    for q in asked:
        nq = cons.next_question()
        need(nq is not None and nq.evidence == q["evidence"], f"the terminal asks something else at question {q['n']}")
        if q["value"] in ("yes", "no"):
            cons.answer(nq.evidence, q["value"] == "yes")
            typed.append(dict(text=q["value"][0], label=None))
        else:
            number = next(i for i, (v, _) in enumerate(nq.options, 1) if v == q["value"])
            cons.answer(nq.evidence, {q["value"]})
            typed.append(dict(text=str(number), label=q["answer"]))
    need(cons.next_question() is None, "the terminal consultation asks more questions")
    return typed


def free_text(app):
    from ddxes import intake, llm
    rec = cs.RECORDED_READING

    def read(kb, text, **_):
        res = intake.parse(kb, text, rec["reply"])
        res.provider, res.model, res.seconds = rec["provider"], rec["model"], rec["seconds"]
        return res
    intake.read = read
    llm.configured = lambda *a, **k: [(llm.PROVIDERS[0], rec["model"], "replayed")]
    status, _, page = call(app, "POST", "/consult/intake", form={"text": cs.INTAKE_TEXT})
    need(status == 200 and "Check what the description says" in page, "the check page did not open")
    tokens = re.findall(r'name="finding" value="([^"]+)"', page)
    shown = re.findall(r'<span class="reading__a">(.*?)</span>\s*<span class="reading__quote">(.*?)</span>', page, re.S)
    need(len(tokens) == len(shown) and all(t.split("=")[0] in SHORT for t in tokens), "the readings shown")
    rows = {}
    for token, (answer, quote) in zip(tokens, shown):
        rows.setdefault(plain(quote).strip("“”"), []).append(f"{SHORT[token.split('=')[0]]}: {plain(answer)}")
    age = re.search(r'id="age"[^>]*value="([^"]*)"', page).group(1)
    sex = re.search(r'name="sex" value="(\w)" checked', page).group(1)
    complaint = re.search(r'<option value="([^"]+)" selected>([^<]*)', page)
    form = dict(age=age, sex=sex, complaint=complaint.group(1), finding=tokens, description=cs.INTAKE_TEXT)
    status, headers, _ = call(app, "POST", "/consult/start", form=form)
    need(status == 303, "the consultation did not start from the reading")
    cookies = session(headers)
    _, asked, end = consult(app, cookies, ["no", "no"])
    need(all(q["rule_out"] for q in asked), "questions other than rule-out ones after the reading")
    return dict(text=cs.INTAKE_TEXT, provider=rec["provider"].title(), model=rec["model"], seconds=rec["seconds"],
                age=age, sex={"M": "man", "F": "woman"}[sex], complaint=html.unescape(complaint.group(2)),
                n_readings=len(tokens), rows=[dict(quote=q, text="; ".join(t)) for q, t in rows.items()],
                asked=asked, end=conclusion(app, cookies, end))


def case(app):
    page = get(app, f"/cases?patient={CASE}")
    why = plain(re.search(r'<details class="tag__why">(.*?)</details>', page, re.S).group(1))
    t = re.search(r"(T-[A-Z0-9-]+) (.+?), CF ([+\-−][\d.]+)\. (.+?\d{4})", why)
    flags = re.search(r'<ul class="flags__list">(.*?)</ul>', page, re.S)
    answers = [plain(a) for a in re.findall(r"<li>(.*?)</li>", re.search(r'<ul class="answers">(.*?)</ul>', page,
                                                                             re.S).group(1), re.S)]
    whynot = get(app, f"/cases?patient={CASE}&tab=whynot&d=Pneumonia")
    reason = find(whynot, "callout", "p")
    rules = re.findall(r'<b class="rule__id">([^<]+)</b>.*?<span class="status status--\w+">([^<]+)</span>', whynot, re.S)
    prov = re.search(r"([\d,]+) triples in all", plain(get(app, f"/cases?patient={CASE}&tab=provenance")))
    need(t and flags and prov, "the case page")
    need(reason.startswith("Set covering") and "Swelling" in reason and "Lose consciousness" in reason,
         "the reason pneumonia is excluded")
    need([r[0] for r in rules] == ["D-PNA-1", "D-PNA-2"] and all(r[1] == "did not fire" for r in rules),
         "the pneumonia rules")
    need(not any(a.lower().startswith("fever") for a in answers), "the case now reports fever")
    truth = re.search(r'<p class="case__truth">(.*?)</p>', page, re.S).group(1)
    return dict(id=CASE, vignette=find(page, "vignette", "p"), truth=plain(re.search(r"<b>(.*?)</b>", truth).group(1)),
                rank=find(truth, "status", "span"), triage=find(page, "tag__level"),
                t_rule=t.group(1), t_text=t.group(2), t_cf=t.group(3), t_src=t.group(4),
                flags=[plain(f).split(" (")[0] for f in re.findall(r"<li>(.*?)</li>", flags.group(1), re.S)],
                pathology=selected(page, "pathology")[1], patient=selected(page, "patient")[1],
                whynot=selected(whynot, "wn-d")[1], triples=prov.group(1))


def knowledge_base(app):
    page = get(app, "/kb?q=D-PE-1&rule=D-PE-1")
    m = re.search(r"(\d+) pathologies, (\d+) questions and (\d+) rules drawn from (\d+) sources, stored as ([\d,]+) RDF "
                  r"triples\. The OWL 2 RL reasoner entails ([\d,]+) more", find(page, "lede", "p"))
    aside = re.search(r'<aside id="rule-detail".*?</aside>', page, re.S).group(0)
    kv = {plain(k): plain(v) for k, v in re.findall(r"<div><dt>(.*?)</dt><dd>(.*?)</dd></div>", aside)}
    row = re.search(r'<tr[^>]*>\s*<td><a [^>]*><b>D-PE-1</b>.*?</tr>', page, re.S)
    cells = [plain(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row.group(0), re.S)] if row else []
    note = find(aside, "muted", "p")
    need(m and len(cells) == 6 and note and "cannot be scored" in note and "lower bound" in note, "the D-PE-1 detail")
    return dict(zip(("pathologies", "questions", "rules", "sources", "triples", "inferred"), m.groups()),
                cf=kv["Certainty factor"], fidelity=kv["Fidelity"], fires=kv["Fires on validation patients"],
                precision=kv["Precision, recall, lift"].split(",")[0], source=cells[3])


def sparql(app):
    page = get(app, "/sparql")
    examples = re.findall(r'<li><a href="/sparql\?example=([^"]+)"[^>]*>(.*?)</a></li>', page, re.S)
    index = next(i for i, (key, _) in enumerate(examples, 1) if key == "q05-red-flag-rules")
    query = (ROOT / "queries" / "q05-red-flag-rules.rq").read_text(encoding="utf-8")
    res = call(app, "POST", "/sparql", form={"query": query, "patient": "", "example": "q05-red-flag-rules"})[2]
    count = re.search(r'id="res-title">\s*(\d+) rows', res)
    first = [plain(r) for r in re.findall(r"<tr><td>(.*?)</td>", res, re.S)[:3]]
    ask = call(app, "POST", "/sparql", form={"query": f"ASK {{ <{GRAPH_IRI}> ?p ?o }}", "patient": ""})[2]
    with open(ROOT / "queries" / "results-no-inference" / "q05-red-flag-rules.csv", encoding="utf-8") as f:
        no_inference = len(list(csv.reader(f))) - 1
    need(count and "Answer: yes" in ask, "SPARQL example q05 or the GraphDB start node")
    return dict(index=index, title=plain(examples[index - 1][1]), count=int(count.group(1)), first=first,
                plain=no_inference, iri=GRAPH_IRI)


def numbers():
    it, main = bx.jload("results/intake.json"), bx.jload("results/main.json")
    il, n_path = it["languages"], len(bx.jload("data/processed/pathologies.json"))
    return dict(n=it["patients"], per_disease=it["patients"] >= n_path,
                en_prec=bx.pct(il["en"]["precision_vs_record"]), id_prec=bx.pct(il["id"]["precision_vs_record"]),
                en_rec=bx.pct(il["en"]["recall_of_mentioned"]), id_rec=bx.pct(il["id"]["recall_of_mentioned"]),
                base_q=f"{it['baseline']['questions']:.1f}", en_q=f"{il['en']['questions']:.1f}",
                id_q=f"{il['id']['questions']:.1f}", en1=bx.pct(il["en"]["top1"]), id1=bx.pct(il["id"]["top1"]),
                base1=bx.pct(it["baseline"]["top1"]), patients=bx.fmt(main["patients"]),
                top1=bx.pct(main["es"]["top1"], 2), ms=f"{main['ms_per_patient']:.1f}",
                emergency_recall=main["triage"]["emergency_recall"])


def render(context):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(SOURCE.parent), autoescape=True,
                             undefined=jinja2.StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    page_html = env.get_template(SOURCE.name).render(fonts=Markup(bx.fonts_css(FONT_FACES)), **context)
    BUILD.mkdir(parents=True, exist_ok=True)
    page_path = BUILD / "demo_script.html"
    page_path.write_text(page_html, encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page()
        page.goto(page_path.as_uri(), wait_until="networkidle")
        page.evaluate("document.fonts.ready.then(() => true)")
        page.pdf(path=str(OUT), format="A4", print_background=True,
                 margin={"top": "8mm", "bottom": "8mm", "left": "10mm", "right": "10mm"})
        browser.close()
    pages = len(re.findall(rb"/Type\s*/Page\b(?!s)", OUT.read_bytes()))
    if pages != 1:
        sys.exit(f"build_demo_script: the script runs to {pages} pages; shorten docs/demo_script.html.j2")


def main():
    from ddxes.web.app import create_app
    app = create_app()
    a = questionnaire(app)          # before free_text(), which replaces the language model with the recorded reading
    b = free_text(app)
    need(a["end"]["label"] == b["end"]["label"] and a["end"]["rule"] == b["end"]["rule"], "the two paths disagree")
    today = datetime.date.today()
    render(dict(a=a, b=b, c=case(app), k=knowledge_base(app), s=sparql(app), x=numbers(),
                built=f"{today.day} {today:%B %Y}"))
    print(f"wrote {OUT}: questionnaire {len(a['asked'])} questions, free text {b['n_readings']} readings then "
          f"{len(b['asked'])} questions, both {a['end']['label']} (P = {a['end']['p']}, {a['end']['triage']})")
    if "--copy-to" in sys.argv:
        dest = pathlib.Path(sys.argv[sys.argv.index("--copy-to") + 1])
        shutil.copyfile(OUT, dest)
        print(f"copied to {dest}")


if __name__ == "__main__":
    main()
