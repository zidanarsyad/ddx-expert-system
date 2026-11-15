"""Shared state and view models of the web interface.

`Services` loads the knowledge base and the inference engine once per process. The functions below turn engine
results into plain dicts for the Jinja2 templates, so that the templates hold no reasoning logic and every
explanation shown in the browser comes from the same `Explainer` the command line uses.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from collections import OrderedDict

from markupsafe import Markup

from .. import rulebase as rb
from ..case import Case
from ..engine import InferenceEngine
from ..explain import Explainer, fmt_lr
from ..kb import KnowledgeBase
from ..paths import KG, PROCESSED, QUERIES, RESULTS
from ..provenance import result_to_graph
from . import svg

TRIAGE_ADVICE = {
    "Emergency": "Call emergency services or go to the emergency department now.",
    "Urgent": "See a doctor today.",
    "NonUrgent": "Book a primary-care appointment within a few days.",
    "SelfCare": "Look after yourself at home, and see a doctor if the symptoms get worse.",
}
TRIAGE_ORDER = list(rb.TRIAGE_LEVELS)
CATEGORIES = ["abstraction", "scoring", "diagnostic", "triage", "recommendation"]
FIDELITY = ["faithful", "partial", "proxy"]
TABS = [("how", "How"), ("whynot", "Why not"), ("concepts", "Concepts and scores"), ("trace", "Rule trace"),
        ("provenance", "Provenance")]


class LRU(OrderedDict):
    """A small least-recently-used store for results and consultation sessions."""

    def __init__(self, size: int):
        super().__init__()
        self.size = size

    def put(self, key, value):
        self[key] = value
        self.move_to_end(key)
        while len(self) > self.size:
            self.popitem(last=False)
        return value

    def touch(self, key):
        value = self.get(key)
        if value is not None:
            self.move_to_end(key)
        return value


class Services:
    """Knowledge base, engine, explainer and demo patients, loaded once."""
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> "Services":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        self.kb = KnowledgeBase.load()
        self.engine = InferenceEngine(self.kb)
        self.ex = Explainer(self.engine)
        path = PROCESSED / "sample_patients.json"
        self.patients = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        self.patient = {p["id"]: p for p in self.patients}
        self.stats = json.loads((KG / "stats.json").read_text(encoding="utf-8"))
        self.results = LRU(48)
        self.sessions = LRU(256)
        self.complaints = sorted((e for e in self.kb.evidences.values() if e.dtype == "B" and e.kind == "symptom"),
                                 key=lambda e: complaint(e.label).lower())

    def name(self, d: str) -> str:
        return self.kb.pathologies[d].name

    def case_result(self, pid: str):
        res = self.results.touch(pid)
        if res is None:
            res = self.results.put(pid, self.engine.run(Case.from_ddxplus(self.patient[pid], self.kb)))
        return res

    @staticmethod
    def result_file(name: str):
        p = RESULTS / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ------------------------------------------------------------------------------------------------ text helpers
def phrase(label: str) -> str:
    """'Shortness of breath' -> 'shortness of breath' (acronyms such as 'HIV' keep their case)."""
    return label[:1].lower() + label[1:] if len(label) > 1 and label[1].islower() else label


_COMPLAINT_CUTS = (", related to your reason for consulting", " in a significant way", " related to your consultation today",
                   " that you believe are related to the condition you are consulting for")
_COMPLAINT_LEADS = re.compile(r"^(Experiencing|Ever|Currently, or did you ever, have|Previously, or do you currently, have any)\s+")


_COMPLAINT_AS = {   # labels that are questions in disguise, rewritten as complaints
    "Lose consciousness": "Loss of consciousness",
    "Lost your sense of smell": "Loss of the sense of smell",
    "Out of breath with minimal physical effort": "Breathlessness on minimal effort",
    "Constantly feel fatigued or do you have non-restful sleep": "Constant fatigue or non-restful sleep",
    "Feeling nauseous or do you feel like vomiting": "Nausea",
    "Vomit after coughing": "Vomiting after coughing",
    "Slightly dizzy or lightheaded": "Slight dizziness",
    "Lightheaded and dizzy or do you feel like you are about to faint": "Dizziness or feeling faint",
    "Recently thrown up blood or something resembling coffee beans": "Vomiting blood",
    "Heart is beating fast (racing), irregularly (missing a beat) or do you feel palpitations": "Palpitations",
    "Heart is beating very irregularly or in a disorganized pattern": "A very irregular heartbeat",
    "So tired that you are unable to do your usual activities or are you stuck in your bed all day long": "Exhaustion",
    "Symptoms that are increased with physical exertion but alleviated with rest": "Symptoms on exertion, relieved by rest",
    "Pain that is increased when you breathe in deeply": "Pain on deep breathing",
    "Symptoms worse when lying down and alleviated while sitting up": "Symptoms worse lying down",
    "Your eyes produce excessive tears": "Watery eyes",
    "Suddenly have difficulty or an inability to open your mouth or have jaw pain when opening it": "Difficulty opening the mouth",
    "Lost consciousness associated with violent and sustained muscle contractions or had an absence episode": "A seizure or absence episode",
    "Loss of appetite or do you get full more quickly then usually": "Loss of appetite",
    "Tone of your voice has become deeper, softer or hoarse": "A hoarse voice",
    "Swelling in one or more areas of your body": "Swelling",
    "Diarrhea or an increase in stool frequency": "Diarrhoea",
    "Wheezing sound when you exhale": "Wheezing",
    "High pitched sound when breathing in": "Stridor",
    "Ever temporarily stop breathing while you’re asleep": "Pauses in breathing during sleep",
    "Involuntary weight loss over the last 3 months": "Weight loss",
    "Significantly increased sweating": "Heavy sweating",
}


def complaint(label: str) -> str:
    """A DDXPlus symptom label as a chief complaint: 'Fever (either felt or measured ...)' -> 'Fever'."""
    if label in _COMPLAINT_AS:
        return _COMPLAINT_AS[label]
    s = re.sub(r"\s*\([^)]*\)", "", label)
    for cut in _COMPLAINT_CUTS:
        s = s.replace(cut, "")
    s = _COMPLAINT_LEADS.sub("", s).strip(" ,")
    return s[:1].upper() + s[1:]


def vignette(age, sex, complaint_label: str | None) -> str:
    """The case presentation: 'A 63-year-old woman. Main complaint: shortness of breath or difficulty breathing.'"""
    who = {"F": "woman", "M": "man"}.get(sex, "patient")
    if age is None:
        head = f"A {who}"
    else:
        article = "An" if str(age).startswith("8") or age in (11, 18) else "A"
        head = f"{article} {age}-year-old {who}"
    return f"{head}. Main complaint: {phrase(complaint(complaint_label))}." if complaint_label else f"{head}."


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ------------------------------------------------------------------------------------------------ result parts
def triage_view(svc: Services, result, previous: str | None = None) -> dict:
    t = svc.ex.triage(result)
    reasons = []
    for n in t["children"]:
        if n.get("rule"):
            r = n["rule"]
            reasons.append({"id": r["id"], "name": r["name"], "cf": n["cf"], "source": r["short"],
                            "citation": r["citation"], "link": r["link"]})
        else:
            reasons.append({"label": n["label"]})
    return {"level": result.triage, "label": rb.TRIAGE_LABEL[result.triage], "advice": TRIAGE_ADVICE[result.triage],
            "threshold": result.settings["triage_cf"], "reasons": reasons, "others": t["other_candidates"],
            "changed": previous is not None and previous != result.triage,
            "previous": rb.TRIAGE_LABEL.get(previous) if previous else None}


def differential_view(svc: Services, result, k: int = 8, previous: dict | None = None) -> list:
    kb = svc.kb
    present = len(result.case.present_symptoms(kb))
    rows = []
    for h in result.differential()[:k]:
        delta = None if previous is None else h.probability - previous.get(h.pathology, 0.0)
        rows.append({"rank": h.rank, "id": h.pathology, "name": svc.name(h.pathology), "p": h.probability, "cf": h.cf,
                     "lr10": h.log_lr / math.log(10), "covers": present - len(h.unexplained), "of": present,
                     "severity": kb.pathologies[h.pathology].severity, "delta": delta,
                     "rules": sorted({c.firing.rule.id for c in h.contributions if c.kind == "rule"})})
    return rows


def red_flags(svc: Services, result) -> list:
    kb = svc.kb
    return [kb.label("concept", c) for c in sorted(result.concepts) if kb.knowledge.concepts[c]["red_flag"]]


def recommendations_view(svc: Services, result) -> list:
    kb, seen, out = svc.kb, set(), []
    for rec, _cf, f in result.recommendations:
        if rec in seen:
            continue
        seen.add(rec)
        src = kb.knowledge.recommendations[rec]["source"]
        out.append({"label": kb.label("recommendation", rec), "source": kb.short_citation(src),
                    "citation": kb.citation(src), "link": kb.source_link(src), "rule": f.rule.id})
    return out


def proof(tree: dict, label: str) -> Markup | None:
    return Markup(svg.proof_tree(tree, label)) if tree.get("children") else None


def how_view(svc: Services, result, d: str) -> dict:
    node = svc.ex.diagnosis(result, d)
    rules = [{"info": r["rule"], "cf": r["cf"], "facts": [ch["label"] for ch in r["children"]]} for r in node["rules"]]
    feats = lambda items: [{"feature": x["feature"], "p_d": x["p_d"], "p_other": x["p_other"],  # noqa: E731
                            "lr": fmt_lr(x["lr"]), "cf": x["cf"]} for x in items]
    tree = {"kind": "pathology", "label": node["label"], "children": node["rules"]}
    return {**node, "rule_rows": rules, "for": feats(node["kb_for"]), "against": feats(node["kb_against"]),
            "families": [c for c in node["classes"] if c != "Pathology"],
            "proof": proof(tree, f"Proof tree for {node['label']}")}


def whynot_view(svc: Services, result, d: str) -> dict:
    return svc.ex.why_not(result, d)


def concepts_view(svc: Services, result) -> dict:
    kb, concepts = svc.kb, []
    for name, cf in sorted(result.concepts.items(), key=lambda kv: kb.label("concept", kv[0]).lower()):
        tree = svc.ex.how(result, "concept", name)
        tree["label"] = f"{tree['label']} (CF {cf:.2f})"
        concepts.append({"label": kb.label("concept", name), "cf": cf, "red_flag": kb.knowledge.concepts[name]["red_flag"],
                         "proof": proof(tree, f"Proof tree for {kb.label('concept', name)}")})
    scores = []
    for s, v in result.scores.items():
        tree = svc.ex.how(result, "score", s)
        scores.append({"label": kb.label("score", s), "value": v, "proof": proof(tree, f"Proof tree for {tree['label']}")})
    return {"concepts": concepts, "scores": scores}


def trace_view(svc: Services, result) -> list:
    kb, ex, groups = svc.kb, svc.ex, []
    for f in result.trace:
        if not groups or groups[-1]["stratum"] != f.stratum:
            groups.append({"stratum": f.stratum, "after": f.stratum > svc.engine.aggregation_stratum, "rows": []})
        outs = ", ".join(f"{ex._consequent_label(c)} {v:+.2f}" if c.kind != "score" else f"{ex._consequent_label(c)} +{v:g}"
                         for c, v in f.outputs)
        groups[-1]["rows"].append({"step": f.step, "id": f.rule.id, "name": f.rule.name, "category": f.rule.category,
                                   "outs": outs, "source": kb.short_citation(f.rule.source)})
    return groups


def provenance_view(svc: Services, result) -> dict:
    g = result_to_graph(result, svc.kb)
    ttl = g.serialize(format="turtle")
    limit = 9000
    return {"triples": len(g), "preview": ttl[:limit], "truncated": len(ttl) > limit, "chars": len(ttl)}


def explanation(svc: Services, result, tab: str, d: str | None) -> dict:
    """Context for one explanation tab of a result (consultation or case)."""
    tab = tab if tab in dict(TABS) else "how"
    out = {"tab": tab, "tabs": TABS, "d": d}
    if tab == "how":
        options = [(h.pathology, svc.name(h.pathology)) for h in result.differential()]
        if options:
            d = d if d in dict(options) else options[0][0]
            out.update(options=options, d=d, how=how_view(svc, result, d))
    elif tab == "whynot":
        # the closest alternatives first: ranked ones, then excluded ones that explain the most symptoms
        others = sorted(result.hypotheses[1:], key=lambda h: (h.excluded, len(h.unexplained), -h.probability,
                                                              svc.name(h.pathology)))
        options = [(h.pathology, svc.name(h.pathology) + (" (excluded)" if h.excluded else "")) for h in others]
        if options:
            d = d if d in dict(options) else options[0][0]
            out.update(options=options, d=d, whynot=whynot_view(svc, result, d))
    elif tab == "concepts":
        out.update(concepts_view(svc, result))
    elif tab == "trace":
        out.update(trace=trace_view(svc, result))
    else:
        out.update(provenance=provenance_view(svc, result))
    return out


def result_view(svc: Services, result, previous_triage=None, previous_probs=None) -> dict:
    return {"triage": triage_view(svc, result, previous_triage), "differential": differential_view(svc, result, 8, previous_probs),
            "recommendations": recommendations_view(svc, result), "red_flags": red_flags(svc, result),
            "concepts_n": len(result.concepts), "fired": len(result.trace)}


# ------------------------------------------------------------------------------------------------ consultation
def answer_text(svc: Services, evidence: str, value) -> str:
    kb = svc.kb
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (set, frozenset)):
        return ", ".join(tidy(kb.label("value", v)) for v in sorted(value)) or "None"
    return f"{value}/10" if kb.evidences[evidence].is_scale else str(value)


def tidy_parens(text: str) -> str:
    """'scapula(R), calf(L)' -> 'scapula (R), calf (L)'."""
    return re.sub(r"(\w)\(", r"\1 (", text)


def tidy(label: str) -> str:
    """DDXPlus answer labels: 'iliac wing(R)' -> 'Iliac wing (R)'."""
    label = tidy_parens(label).strip()
    return label[:1].upper() + label[1:]


def question_view(svc: Services, q) -> dict | None:
    if q is None:
        return None
    options = [(v, tidy(label)) for v, label in q.options]
    if q.dtype == "M" and len(options) > 12:      # long lists (body locations) read better sorted
        options.sort(key=lambda o: o[1].lower())
    goals = [{"id": g, "name": svc.name(g), "severity": svc.kb.pathologies[g].severity} for g in q.goals[:4]]
    return {"evidence": q.evidence, "text": q.text, "dtype": q.dtype, "options": options, "many": len(options) > 12,
            "why": q.why, "gain": q.gain, "phase": q.phase, "goals": goals, "rules": q.rules}


# ------------------------------------------------------------------------------------------------ knowledge base
def kb_summary(svc: Services) -> dict:
    kb, s = svc.kb, svc.stats
    used = {r.source for r in kb.rules}
    return {"pathologies": len(kb.pathologies), "questions": len(kb.evidences), "rules": len(kb.rules),
            "sources": len(used), "triples": s["kb"] + s["rules"] + s["stats"] + s["ontology"], "inferred": s["inferred"],
            "categories": {c: sum(1 for r in kb.rules if r.category == c) for c in CATEGORIES}}


def rules_view(svc: Services, cats: list, fids: list, query: str) -> list:
    kb = svc.kb
    val = {r["id"]: r for r in (svc.result_file("rule_validation.json") or {}).get("rows", [])}
    q = query.lower().strip()
    rows = []
    for r in kb.rules:
        if r.category not in cats or r.fidelity not in fids:
            continue
        if q and q not in (r.id + " " + r.name + " " + kb.rule_text(r)).lower():
            continue
        v = val.get(r.id, {})
        rows.append({"id": r.id, "name": r.name, "category": r.category, "cf": r.cf, "fidelity": r.fidelity,
                     "source": kb.short_citation(r.source), "fires": v.get("fires"), "precision": v.get("precision"),
                     "lift": v.get("lift")})
    return rows


def rule_detail(svc: Services, rid: str) -> dict | None:
    kb = svc.kb
    r = kb.rule_by_id.get(rid)
    if r is None:
        return None
    val = next((x for x in (svc.result_file("rule_validation.json") or {}).get("rows", []) if x["id"] == rid), {})
    return {"id": r.id, "name": r.name, "category": r.category, "cf": r.cf, "fidelity": r.fidelity, "note": r.note,
            "text": kb.rule_text(r), "citation": kb.citation(r.source), "link": kb.source_link(r.source),
            "short": kb.short_citation(r.source), "classes": sorted(kb.rule_classes.get(r.id, ())), "validation": val}


def pathology_view(svc: Services, d: str) -> dict:
    kb = svc.kb
    p = kb.pathologies[d]
    rules = [{"id": r.id, "name": r.name, "source": kb.short_citation(r.source), "category": r.category}
             for r in kb.rules
             if any(c.target == d for c in r.consequents)
             or any(isinstance(a, rb.BeliefAtLeast) and a.pathology == d for a in rb.atoms(r.premise))]
    return {"id": d, "name": p.name, "icd10": p.icd10, "severity": p.severity, "prior": p.prior, "patients": p.patients,
            "classes": sorted(c for c in p.classes if c != "Pathology"),
            "symptoms": sorted(kb.label("evidence", e) for e in p.symptoms),
            "antecedents": sorted(kb.label("evidence", e) for e in p.antecedents), "rules": rules}


def sources_view(svc: Services) -> list:
    kb = svc.kb
    count = {}
    for r in kb.rules:
        count[r.source] = count.get(r.source, 0) + 1
    return [{"key": k, "type": s["type"], "citation": s["citation"], "link": kb.source_link(k), "rules": count.get(k, 0)}
            for k, s in sorted(kb.knowledge.sources.items(), key=lambda kv: kv[1]["citation"].lower())]


# ------------------------------------------------------------------------------------------------ SPARQL
def examples() -> list:
    out = []
    for p in sorted(QUERIES.glob("q*.rq")):
        text = p.read_text(encoding="utf-8")
        first = text.splitlines()[0].lstrip("# ").strip() if text else p.stem
        first = re.sub(r"\s*\(run with:[^)]*\)", "", first)
        out.append({"key": p.stem, "title": first, "text": text})
    return out


def run_sparql(svc: Services, query: str, with_cases: bool, patient: str) -> dict:
    from rdflib import Graph
    from ..paths import DDX
    t0 = time.perf_counter()
    g = svc.kb.graph
    notes = []
    if with_cases:
        g = g + Graph().parse(KG / "cases.ttl", format="turtle")
        notes.append("the 980 demo patients")
    if patient:
        rec = svc.patient.get(patient)
        if rec is None:
            return {"error": f"There is no demo patient {patient}. Patient identifiers look like T002456."}
        g = g + result_to_graph(svc.case_result(patient), svc.kb)
        notes.append(f"the consultation of {patient}")
    try:
        res = g.query(query)
    except Exception as e:  # noqa: BLE001 - the parser's message is the most useful thing to show
        return {"error": f"{type(e).__name__}: {e}"}
    ms = (time.perf_counter() - t0) * 1000
    base = {"ms": ms, "triples": len(g), "notes": notes}
    if res.type == "ASK":
        return {**base, "kind": "ask", "answer": bool(res.askAnswer)}
    if res.type in ("CONSTRUCT", "DESCRIBE"):
        out = Graph()
        for t in res:
            out.add(t)
        return {**base, "kind": "graph", "turtle": out.serialize(format="turtle"), "count": len(out)}
    prefix = str(DDX)
    rows = [[("" if x is None else str(x).replace(prefix, ":")) for x in row] for row in res]
    return {**base, "kind": "select", "columns": [str(v) for v in res.vars], "rows": rows, "count": len(rows)}


# ------------------------------------------------------------------------------------------------ evaluation
def evaluation_view(svc: Services) -> dict | None:
    main = svc.result_file("main.json") or svc.result_file("main_quick.json")
    if not main:
        return None
    systems = [("es", "Expert system"), ("nb", "Naive Bayes"), ("hybrid", "Hybrid")]
    metrics = [("top1", "Top-1 (GTPA@1)"), ("top3", "Top-3 (GTPA@3)"), ("top5", "Top-5 (GTPA@5)"), ("gtpa", "In the differential (GTPA)"),
               ("ddr", "Differential recall (DDR)"), ("ddp", "Differential precision (DDP)"), ("ddf1", "Differential F1 (DDF1)")]
    table = []
    for key, label in metrics:
        vals = [main[s][key] for s, _ in systems]
        table.append({"label": label, "values": vals, "best": max(vals)})
    t = main["triage"]
    levels = TRIAGE_ORDER
    matrix = []
    peak = max(t["matrix"][a][b] for a in levels for b in levels) or 1
    for a in levels:
        row_total = sum(t["matrix"][a].values()) or 1
        cells = [{"n": t["matrix"][a][b], "share": t["matrix"][a][b] / row_total, "level": b,
                  "shade": min(6, int(math.ceil(6 * math.sqrt(t["matrix"][a][b] / peak)))) if t["matrix"][a][b] else 0,
                  "diag": a == b} for b in levels]
        matrix.append({"level": a, "label": rb.TRIAGE_LABEL[a], "cells": cells, "n": row_total})
    abl = svc.result_file("ablation.json")
    cons = svc.result_file("consultation.json")
    curve = svc.result_file("consultation_curve.json")
    curve_svg = None
    if curve:
        pts = [(r["questions_mean"], r["top1"], f"P ≥ {r['confidence']:g}",
                f"Stop at P ≥ {r['confidence']:g}: {r['questions_mean']:.1f} questions, top-1 {r['top1']:.1%}")
               for r in curve["rows"]]
        curve_svg = Markup(svg.line_chart(pts, x_label="Mean questions asked", y_label="Top-1 accuracy"))
    per = sorted(({"name": svc.name(d), "n": v["n"], "top1": v["top1"], "support": v["guideline_support"]}
                  for d, v in main["per_pathology"].items()), key=lambda r: (r["top1"], r["name"]))
    return {"main": main, "systems": [s for _, s in systems], "table": table, "triage": t, "matrix": matrix,
            "levels": [rb.TRIAGE_LABEL[x] for x in levels], "ablation": abl, "consultation": cons, "curve_svg": curve_svg,
            "per": per}
