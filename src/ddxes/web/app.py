"""DDX-ES web interface: Starlette routes and Jinja2 templates.

Run with `ddxes serve` or `python -m ddxes.web` and open http://127.0.0.1:8000.

Pages: Consultation (goal-driven questioning with WHY), Case analysis (DDXPlus test patients), Knowledge base,
SPARQL and Evaluation. Every page is rendered on the server and works without JavaScript; `static/js/app.js`
only swaps page regions in place and adds keyboard shortcuts.
"""
from __future__ import annotations

import contextlib
import pathlib
import random
import re
import secrets

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from ..consult import Consultation
from . import views as V

HERE = pathlib.Path(__file__).resolve().parent
COOKIE = "ddxes_session"
DEFAULT_PATIENT = "T002456"
DEFAULT_COMPLAINT = "E_201"

templates = Jinja2Templates(directory=str(HERE / "templates"))
MINUS = "−"


def _signed(v, digits=2):
    return f"{v:+.{digits}f}".replace("-", MINUS)


def _num(v, digits=3):
    return f"{v:.{digits}f}".replace("-", MINUS)


def _pct(v, digits=1):
    return f"{v * 100:.{digits}f}%"


templates.env.filters.update(signed=_signed, num=_num, pct=_pct, thousands=lambda n: f"{n:,}", ordinal=V.ordinal,
                             phrase=V.phrase, complaint=V.complaint)
templates.env.globals.update(TRIAGE_LEVELS=V.TRIAGE_ORDER)


def render(request: Request, name: str, section: str, **ctx) -> Response:
    return templates.TemplateResponse(request, name, {"section": section, **ctx})


def see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


# ------------------------------------------------------------------------------------------------ consultation state
class ConsultState:
    """One consultation in progress: the engine's consultation plus what the page needs to show changes."""

    def __init__(self, cons: Consultation):
        self.cons = cons
        self.question = cons.next_question()      # computed once per answer: next_question() has side effects
        self.prev_triage = None
        self.prev_probs = None

    def answer(self, evidence, value):
        res = self.cons.result
        self.prev_triage = res.triage
        self.prev_probs = {h.pathology: h.probability for h in res.hypotheses}
        self.cons.answer(evidence, value)
        self.question = self.cons.next_question()

    def undo(self, svc):
        """Replay every answer but the last on a fresh consultation (selection is deterministic)."""
        old = self.cons
        cons = Consultation(svc.engine, age=old.age, sex=old.sex, chief_complaint=old.chief_complaint)
        for e, v in old.asked[:-1]:
            cons.next_question()
            cons.answer(e, v)
        self.__init__(cons)


def _session(request: Request):
    sid = request.cookies.get(COOKIE)
    svc = V.Services.get()
    return sid, (svc.sessions.touch(sid) if sid else None)


def _with_cookie(resp: Response, sid: str) -> Response:
    resp.set_cookie(COOKIE, sid, httponly=True, samesite="lax", max_age=60 * 60 * 12)
    return resp


def _parse_answer(svc, q, form):
    ev = svc.kb.evidences[q.evidence]
    if q.dtype == "B":
        v = form.get("value")
        return None if v not in ("yes", "no") else v == "yes"
    if q.dtype == "scale":
        try:
            return max(0, min(10, int(form.get("value", ""))))
        except ValueError:
            return None
    if q.dtype == "M":
        chosen = {v for v in form.getlist("value") if v in ev.values}
        return chosen or {ev.default}
    v = form.get("value")
    return {v} if v in ev.values else None


async def consult(request: Request) -> Response:
    svc = await run_in_threadpool(V.Services.get)
    sid, state = _session(request)
    if state is None:
        return render(request, "consult_start.html", "consult", complaints=svc.complaints, default=DEFAULT_COMPLAINT,
                      age=45, sex="F")
    cons, res = state.cons, state.cons.result
    qp = request.query_params
    done = state.question is None
    show = done or qp.get("explain") == "1"
    ctx = {
        "q": V.question_view(svc, state.question), "number": len(cons.asked) + 1, "done": done,
        "vignette": V.vignette(cons.age, cons.sex, svc.kb.label("evidence", cons.chief_complaint)),
        "answers": [{"q": svc.kb.evidences[e].question, "a": V.answer_text(svc, e, v)} for e, v in cons.asked],
        "result": V.result_view(svc, res, state.prev_triage, state.prev_probs),
        "explain": V.explanation(svc, res, qp.get("tab", "how"), qp.get("d")) if show else None,
        "show": show, "base": "/consult?explain=1&", "ttl": "/consult/provenance.ttl",
        "path": "/consult", "keep": {"explain": "1"},
    }
    return render(request, "consult.html", "consult", **ctx)


async def consult_start(request: Request) -> Response:
    svc = await run_in_threadpool(V.Services.get)
    form = await request.form()
    try:
        age = max(0, min(110, int(form.get("age", "45"))))
    except ValueError:
        age = 45
    sex = form.get("sex") if form.get("sex") in ("F", "M") else "F"
    cc = form.get("complaint") if form.get("complaint") in svc.kb.evidences else DEFAULT_COMPLAINT
    sid = request.cookies.get(COOKIE) or secrets.token_urlsafe(18)
    svc.sessions.put(sid, ConsultState(Consultation(svc.engine, age=age, sex=sex, chief_complaint=cc)))
    return _with_cookie(see_other("/consult"), sid)


async def consult_answer(request: Request) -> Response:
    svc = await run_in_threadpool(V.Services.get)
    sid, state = _session(request)
    if state is None or state.question is None:
        return see_other("/consult")
    form = await request.form()
    q = state.question
    if form.get("evidence") == q.evidence:
        value = _parse_answer(svc, q, form)
        if value is not None:
            state.answer(q.evidence, value)
    return see_other("/consult")


async def consult_undo(request: Request) -> Response:
    svc = await run_in_threadpool(V.Services.get)
    _sid, state = _session(request)
    if state is not None and state.cons.asked:
        state.undo(svc)
    return see_other("/consult")


async def consult_reset(request: Request) -> Response:
    svc = await run_in_threadpool(V.Services.get)
    sid, _state = _session(request)
    if sid:
        svc.sessions.pop(sid, None)
    return see_other("/consult")


def _turtle(result, svc, name: str) -> Response:
    from ..provenance import result_to_graph
    ttl = result_to_graph(result, svc.kb).serialize(format="turtle")
    return Response(ttl, media_type="text/turtle",
                    headers={"Content-Disposition": f'attachment; filename="{name}-provenance.ttl"'})


async def consult_ttl(request: Request) -> Response:
    svc = await run_in_threadpool(V.Services.get)
    _sid, state = _session(request)
    if state is None:
        return see_other("/consult")
    return _turtle(state.cons.result, svc, "consultation")


# ------------------------------------------------------------------------------------------------ case analysis
async def cases(request: Request) -> Response:
    svc = await run_in_threadpool(V.Services.get)
    if not svc.patients:
        return render(request, "missing.html", "cases", what="the demo patients (data/processed/sample_patients.json)",
                      how="python -I scripts/prepare_data.py")
    qp = request.query_params
    pid = qp.get("patient") if qp.get("patient") in svc.patient else None
    path = qp.get("pathology") if qp.get("pathology") in svc.kb.pathologies else None
    if pid and not path:
        path = svc.patient[pid]["pathology"]
    default = svc.patient.get(DEFAULT_PATIENT)
    path = path or (default["pathology"] if default else sorted(svc.kb.pathologies)[0])
    ids = [p for p in svc.patients if p["pathology"] == path]
    if not pid or svc.patient[pid]["pathology"] != path:
        pid = DEFAULT_PATIENT if default and default["pathology"] == path else (ids[0]["id"] if ids else None)
    if pid is None:
        return see_other("/cases")
    rec = svc.patient[pid]
    result = svc.case_result(pid)
    ranking = result.ranking
    ctx = {
        "pathologies": sorted(((d, svc.name(d)) for d in svc.kb.pathologies), key=lambda x: x[1].lower()),
        "pathology": path, "patients": ids, "pid": pid, "rec": rec,
        "vignette": V.vignette(rec["age"], rec["sex"], svc.kb.label("evidence", rec["initial"])),
        "truth": svc.name(rec["pathology"]),
        "truth_rank": ranking.index(rec["pathology"]) + 1 if rec["pathology"] in ranking else None,
        "answers": [V.tidy_parens(a) for a in svc.ex.answers(result.case)],
        "reference": [(svc.name(d), p) for d, p in rec["ddx"][:10]],
        "result": V.result_view(svc, result),
        "explain": V.explanation(svc, result, qp.get("tab", "how"), qp.get("d")),
        "base": f"/cases?patient={pid}&", "ttl": f"/cases/{pid}/provenance.ttl",
        "path": "/cases", "keep": {"patient": pid},
    }
    return render(request, "cases.html", "cases", **ctx)


async def cases_random(request: Request) -> Response:
    svc = await run_in_threadpool(V.Services.get)
    if not svc.patients:
        return see_other("/cases")
    return see_other(f"/cases?patient={random.choice(svc.patients)['id']}")


async def case_ttl(request: Request) -> Response:
    svc = await run_in_threadpool(V.Services.get)
    pid = request.path_params["pid"]
    if pid not in svc.patient:
        return see_other("/cases")
    return _turtle(svc.case_result(pid), svc, pid)


# ------------------------------------------------------------------------------------------------ knowledge base
async def kb_page(request: Request) -> Response:
    svc = await run_in_threadpool(V.Services.get)
    qp = request.query_params
    tab = qp.get("tab") if qp.get("tab") in ("rules", "pathologies", "sources") else "rules"
    ctx = {"tab": tab, "summary": V.kb_summary(svc)}
    if tab == "rules":
        submitted = qp.get("f") == "1"
        cats = [c for c in qp.getlist("cat") if c in V.CATEGORIES] if submitted else ["diagnostic"]
        fids = [f for f in qp.getlist("fid") if f in V.FIDELITY] if submitted else list(V.FIDELITY)
        query = qp.get("q", "")
        rows = V.rules_view(svc, cats, fids, query)
        rid = qp.get("rule") if qp.get("rule") in svc.kb.rule_by_id else (rows[0]["id"] if rows else None)
        params = [("f", "1")] + [("cat", c) for c in cats] + [("fid", f) for f in fids] + ([("q", query)] if query else [])
        ctx.update(cats=cats, fids=fids, query=query, rows=rows, rid=rid, detail=V.rule_detail(svc, rid) if rid else None,
                   categories=V.CATEGORIES, fidelities=V.FIDELITY, params=params)
    elif tab == "pathologies":
        d = qp.get("d") if qp.get("d") in svc.kb.pathologies else "PulmonaryEmbolism"
        ctx.update(d=d, options=sorted(((k, svc.name(k)) for k in svc.kb.pathologies), key=lambda x: x[1].lower()),
                   p=V.pathology_view(svc, d))
    else:
        ctx.update(sources=V.sources_view(svc))
    return render(request, "kb.html", "kb", **ctx)


# ------------------------------------------------------------------------------------------------ SPARQL
WITH_PATIENT = re.compile(r"--with-patient\s+(\w+)")


async def sparql(request: Request) -> Response:
    svc = await run_in_threadpool(V.Services.get)
    examples = V.examples()
    by_key = {e["key"]: e for e in examples}
    result = None
    if request.method == "POST":
        form = await request.form()
        query = form.get("query", "")
        with_cases = form.get("with_cases") == "on"
        patient = (form.get("patient") or "").strip()
        example = form.get("example") if form.get("example") in by_key else None
        result = await run_in_threadpool(V.run_sparql, svc, query, with_cases, patient) if query.strip() else \
            {"error": "Write a query first, or pick one of the examples."}
    else:
        example = request.query_params.get("example")
        example = example if example in by_key else examples[0]["key"]
        query = by_key[example]["text"]
        hint = WITH_PATIENT.search(query.splitlines()[0]) if query else None
        patient = hint.group(1) if hint else ""
        with_cases = False
    return render(request, "sparql.html", "sparql", examples=examples, example=example, query=query,
                  with_cases=with_cases, patient=patient, result=result)


# ------------------------------------------------------------------------------------------------ evaluation
async def evaluation(request: Request) -> Response:
    svc = await run_in_threadpool(V.Services.get)
    ev = V.evaluation_view(svc)
    if ev is None:
        return render(request, "missing.html", "evaluation", what="the evaluation results (results/main.json)",
                      how="python -I scripts/evaluate.py")
    return render(request, "evaluation.html", "evaluation", ev=ev)


async def home(request: Request) -> Response:
    return see_other("/consult")


@contextlib.asynccontextmanager
async def lifespan(app):
    await run_in_threadpool(V.Services.get)      # load the knowledge base before the first request
    yield


def create_app() -> Starlette:
    routes = [
        Route("/", home),
        Route("/consult", consult),
        Route("/consult/start", consult_start, methods=["POST"]),
        Route("/consult/answer", consult_answer, methods=["POST"]),
        Route("/consult/undo", consult_undo, methods=["POST"]),
        Route("/consult/reset", consult_reset, methods=["POST"]),
        Route("/consult/provenance.ttl", consult_ttl),
        Route("/cases", cases),
        Route("/cases/random", cases_random),
        Route("/cases/{pid}/provenance.ttl", case_ttl),
        Route("/kb", kb_page),
        Route("/sparql", sparql, methods=["GET", "POST"]),
        Route("/evaluation", evaluation),
        Mount("/static", StaticFiles(directory=str(HERE / "static")), name="static"),
    ]
    return Starlette(routes=routes, lifespan=lifespan)


app = create_app()
