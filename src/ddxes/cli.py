"""Command-line interface:  python -m ddxes <command>   (or `ddxes <command>` after `pip install -e .`)

  diagnose   run the expert system on a demo patient or on answers given on the command line
  consult    interactive consultation in the terminal (WHY / HOW commands)
  explain    HOW a pathology, the triage level or a concept was concluded; WHY NOT another pathology
  rules      list or show rules of the rule base, with their sources
  patients   list the demo patients (20 per pathology)
  evidence   search the questions (evidence codes) of the knowledge base
  sparql     run a SPARQL query over the knowledge graph (optionally with a consultation written back)
  build      rebuild the knowledge graph (scripts/build_kg.py)
  evaluate   run the evaluation (scripts/evaluate.py)
"""
from __future__ import annotations

import argparse
import json
import runpy
import sys
import textwrap

from . import rulebase as rb
from .case import Case
from .consult import Consultation
from .engine import InferenceEngine
from .explain import Explainer, tree_to_markdown
from .kb import KnowledgeBase
from .paths import PROCESSED, ROOT


def _demo_patients() -> list:
    return json.loads((PROCESSED / "sample_patients.json").read_text(encoding="utf-8"))


def _patient(pid: str) -> dict:
    for rec in _demo_patients():
        if rec["id"] == pid:
            return rec
    sys.exit(f"unknown patient {pid}: see `python -m ddxes patients`")


def _case_from_args(args, kb) -> Case:
    if args.patient:
        return Case.from_ddxplus(_patient(args.patient), kb)
    case = Case(id="cli", age=args.age, sex=args.sex, closed_world=not args.open_world)
    for e in args.yes or []:
        case.answer(kb, e, True)
    for e in args.no or []:
        case.answer(kb, e, False)
    for a in args.answer or []:
        e, _, v = a.partition("=")
        if kb.evidences[e].is_scale:
            case.answer(kb, e, int(v))
        else:
            case.answers.setdefault(e, set()).add(v)
    return case


def cmd_diagnose(args):
    kb = KnowledgeBase.load()
    engine = InferenceEngine(kb)
    case = _case_from_args(args, kb)
    result = engine.run(case)
    if args.json:
        print(json.dumps({"case": case.id, "triage": result.triage,
                          "differential": [{"pathology": h.pathology, "probability": round(h.probability, 4), "cf": round(h.cf, 4)}
                                           for h in result.differential()],
                          "recommendations": [r for r, _, _ in result.recommendations],
                          "rules_fired": [f.rule.id for f in result.trace]}, indent=1))
    else:
        print(Explainer(engine).report(result, trace=args.trace))
        if case.truth:
            print(f"\n(ground truth in DDXPlus: {kb.pathologies[case.truth].name})")
    if args.provenance:
        from .provenance import result_to_graph
        g = result_to_graph(result, kb)
        g.serialize(args.provenance, format="turtle", encoding="utf-8")
        print(f"\nprovenance written to {args.provenance} ({len(g)} triples)")


def cmd_explain(args):
    kb = KnowledgeBase.load()
    engine = InferenceEngine(kb)
    ex = Explainer(engine)
    result = engine.run(_case_from_args(args, kb))
    if args.why_not:
        w = ex.why_not(result, args.why_not)
        print(f"WHY NOT {w['label']}? rank {w['rank']}, P = {w['probability']:.3f}; leading: {w['top']} ({w['top_probability']:.3f})")
        print(textwrap.fill(w["reason"], 100))
        for x in w.get("against", []):
            print(f"  against: {x['feature']} (CF {x['cf']:+.2f})")
        for g in w["guideline_rules"]:
            mark = "fired" if g["fired"] else "did not fire"
            print(f"\n  [{g['rule']['id']}] {g['rule']['name']} -- {mark} ({g['rule']['short']})")
            if g["tree"]:
                _print_tree(g["tree"], 2)
    elif args.how_triage:
        print(tree_to_markdown(ex.triage(result)))
    elif args.how_concept:
        print(tree_to_markdown(ex.how(result, "concept", args.how_concept)))
    else:
        d = args.how or result.ranking[0]
        node = ex.diagnosis(result, d)
        print(f"HOW {node['label']}: rank {node['rank']}, P = {node['probability']:.3f}, CF {node['cf']:+.3f}")
        cov = node["covering"]
        print(f"  set covering: explains {cov['explains']} of {cov['of']} reported symptoms"
              + (f"; cannot explain {', '.join(cov['unexplained'])}" if cov["unexplained"] else ""))
        print(f"  guideline rules CF {node['cf_rules']:+.3f}; {node['n_kb']} knowledge-base links CF {node['cf_kb']:+.3f}; prior {node['prior']:.4f}")
        for r in node["rules"]:
            print(tree_to_markdown(r, 1))
        for x in node["kb_for"]:
            print(f"  + {x['feature']}: P(f|d) {x['p_d']:.2f} vs {x['p_other']:.3f} -> CF {x['cf']:+.2f}")
        for x in node["kb_against"]:
            print(f"  - {x['feature']}: P(f|d) {x['p_d']:.2f} vs {x['p_other']:.3f} -> CF {x['cf']:+.2f}")


def _print_tree(node, depth):
    mark = {True: "✓", False: "✗", None: "?"}[node["status"]]
    extra = f"   (unknown: {', '.join(node['missing'])})" if node["status"] is None and node["missing"] else ""
    print("  " * depth + f"{mark} {node['text'][:110]}{extra}")
    if node["status"] is not True:
        for ch in node.get("children", []):
            _print_tree(ch, depth + 1)


def cmd_rules(args):
    kb = KnowledgeBase.load()
    rules = kb.rules
    if args.id:
        rules = [r for r in rules if r.id == args.id]
    if args.category:
        rules = [r for r in rules if r.category == args.category]
    if args.pathology:
        rules = [r for r in rules if any(c.target == args.pathology for c in r.consequents)
                 or any(isinstance(a, rb.BeliefAtLeast) and a.pathology == args.pathology for a in rb.atoms(r.premise))]
    for r in rules:
        print(f"[{r.id}] {r.name}  ({r.category}, CF {r.cf:+.2f}, fidelity {r.fidelity})")
        print(textwrap.indent(textwrap.fill(kb.rule_text(r), 110), "    "))
        print(f"    source: {kb.citation(r.source)}  {kb.source_link(r.source) or ''}")
        if r.note and (args.id or args.verbose):
            print(textwrap.indent(textwrap.fill("note: " + r.note, 110), "    "))
        print()
    print(f"{len(rules)} rule(s)")


def cmd_patients(args):
    kb = KnowledgeBase.load()
    for rec in _demo_patients():
        if args.pathology and rec["pathology"] != args.pathology:
            continue
        print(f"{rec['id']}  {rec['sex']} {rec['age']:3d}  {kb.pathologies[rec['pathology']].name:40s} chief complaint: "
              f"{kb.label('evidence', rec['initial'])}")


def cmd_evidence(args):
    kb = KnowledgeBase.load()
    term = (args.term or "").lower()
    for e in sorted(kb.evidences.values(), key=lambda e: int(e.code[2:])):
        if term in e.question.lower() or term in e.label.lower() or term == e.code.lower():
            kind = "scale 0-10" if e.is_scale else {"B": "yes/no", "C": "categorical", "M": "multi-choice"}[e.dtype]
            print(f"{e.code:6s} [{e.kind}, {kind}{', sub-question of ' + e.parent if e.parent else ''}] {e.question}")


def cmd_sparql(args):
    kb = KnowledgeBase.load()
    g = kb.graph
    from rdflib import Graph
    if args.with_cases:
        g = g + Graph().parse(ROOT / "kg" / "cases.ttl", format="turtle")
    if args.with_patient:
        from .provenance import result_to_graph
        result = InferenceEngine(kb).run(Case.from_ddxplus(_patient(args.with_patient), kb))
        g = g + result_to_graph(result, kb)
    q = open(args.query, encoding="utf-8").read() if args.query.endswith(".rq") else args.query
    res = g.query(q)
    if res.type == "ASK":
        print(res.askAnswer)
        return
    vars_ = [str(v) for v in res.vars]
    print("\t".join(vars_))
    for row in res:
        print("\t".join("" if x is None else str(x).replace("http://example.org/ddx#", ":") for x in row))


def cmd_consult(args):
    kb = KnowledgeBase.load()
    engine = InferenceEngine(kb)
    ex = Explainer(engine)
    print("DDX-ES interactive consultation. Educational prototype on synthetic data -- not for clinical use.")
    print("Answer y/n, option numbers (comma separated) or 0-10. Commands: why, how [n], triage, stop.\n")
    age = args.age if args.age is not None else int(input("Age? ").strip())
    sex = args.sex or input("Sex (F/M)? ").strip().upper()[:1]
    complaint = args.complaint
    while not complaint:
        term = input("Main complaint (a word to search, e.g. cough, pain, fever)? ").strip().lower()
        hits = [e for e in kb.evidences.values() if e.dtype == "B" and e.kind == "symptom" and term in e.question.lower()]
        for i, e in enumerate(hits[:15], 1):
            print(f"  {i:2d}. {e.question}")
        pick = input("Number: ").strip()
        if pick.isdigit() and 1 <= int(pick) <= len(hits[:15]):
            complaint = hits[int(pick) - 1].code
    cons = Consultation(engine, age=age, sex=sex, chief_complaint=complaint, max_questions=args.max_questions)
    while (q := cons.next_question()) is not None:
        print(f"\nQ{len(cons.asked) + 1}. {q.text}")
        if q.dtype in ("C", "M"):
            for i, (v, label) in enumerate(q.options, 1):
                print(f"   {i:3d}. {label}")
        while True:
            raw = input("> ").strip().lower()
            if raw == "why":
                print(textwrap.fill("WHY: " + q.why, 100))
                continue
            if raw.startswith("how"):
                n = int(raw.split()[1]) if len(raw.split()) > 1 else 1
                diff = cons.result.differential()
                if 1 <= n <= len(diff):
                    node = ex.diagnosis(cons.result, diff[n - 1].pathology)
                    print(f"HOW {node['label']} (P {node['probability']:.2f}): covers {node['covering']['explains']}/{node['covering']['of']} symptoms; "
                          f"rules: {', '.join(r['rule']['id'] for r in node['rules']) or '-'}")
                continue
            if raw == "triage":
                print(tree_to_markdown(ex.triage(cons.result)))
                continue
            if raw == "stop":
                q = None
                break
            try:
                if q.dtype == "B" and raw in ("y", "n", "yes", "no"):
                    cons.answer(q.evidence, raw.startswith("y"))
                elif q.dtype == "scale" and raw.isdigit() and 0 <= int(raw) <= 10:
                    cons.answer(q.evidence, int(raw))
                elif q.dtype in ("C", "M"):
                    picks = [q.options[int(x) - 1][0] for x in raw.replace(" ", "").split(",") if x]
                    cons.answer(q.evidence, set(picks))
                else:
                    raise ValueError
                break
            except (ValueError, IndexError):
                print("answer y/n, option numbers or 0-10 (or why / how / triage / stop)")
        if q is None:
            break
        top = cons.result.differential()[:3]
        print("   now: " + "; ".join(f"{kb.pathologies[h.pathology].name} {h.probability:.2f}" for h in top)
              + f" | triage {rb.TRIAGE_LABEL[cons.result.triage]}")
    print("\n" + ex.report(cons.result))


def main(argv=None):
    p = argparse.ArgumentParser(prog="ddxes", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def case_args(sp):
        sp.add_argument("--patient", help="demo patient id, e.g. T002456")
        sp.add_argument("--age", type=int)
        sp.add_argument("--sex", choices=["F", "M"])
        sp.add_argument("--yes", nargs="*", metavar="E_xx", help="evidences answered yes")
        sp.add_argument("--no", nargs="*", metavar="E_xx", help="evidences answered no")
        sp.add_argument("--answer", action="append", metavar="E_xx=V_yy", help="categorical answer or 0-10 scale (E_56=7)")
        sp.add_argument("--open-world", action="store_true", help="treat questions not answered as unknown, not as no")

    sp = sub.add_parser("diagnose", help="run the expert system on one case")
    case_args(sp)
    sp.add_argument("--trace", action="store_true", help="print the rule trace")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--provenance", metavar="FILE.ttl", help="write the inferences with PROV-O provenance as Turtle")
    sp.set_defaults(func=cmd_diagnose)

    sp = sub.add_parser("explain", help="HOW / WHY NOT explanations")
    case_args(sp)
    sp.add_argument("--how", metavar="PATHOLOGY")
    sp.add_argument("--why-not", metavar="PATHOLOGY")
    sp.add_argument("--how-triage", action="store_true")
    sp.add_argument("--how-concept", metavar="CONCEPT")
    sp.set_defaults(func=cmd_explain)

    sp = sub.add_parser("consult", help="interactive consultation")
    sp.add_argument("--age", type=int)
    sp.add_argument("--sex", choices=["F", "M"])
    sp.add_argument("--complaint", metavar="E_xx", help="chief complaint evidence code")
    sp.add_argument("--max-questions", type=int, default=30)
    sp.set_defaults(func=cmd_consult)

    sp = sub.add_parser("rules", help="list rules")
    sp.add_argument("--id")
    sp.add_argument("--category", choices=["abstraction", "scoring", "diagnostic", "triage", "recommendation"])
    sp.add_argument("--pathology")
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.set_defaults(func=cmd_rules)

    sp = sub.add_parser("patients", help="list demo patients")
    sp.add_argument("--pathology")
    sp.set_defaults(func=cmd_patients)

    sp = sub.add_parser("evidence", help="search questions")
    sp.add_argument("term", nargs="?")
    sp.set_defaults(func=cmd_evidence)

    sp = sub.add_parser("sparql", help="SPARQL over the knowledge graph")
    sp.add_argument("query", help="a .rq file or a query string")
    sp.add_argument("--with-cases", action="store_true", help="also load kg/cases.ttl (980 demo patients)")
    sp.add_argument("--with-patient", metavar="ID", help="diagnose a demo patient and add its provenance graph")
    sp.set_defaults(func=cmd_sparql)

    sp = sub.add_parser("serve", help="run the web interface (http://127.0.0.1:8000)")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", default="8000")
    sp.set_defaults(func=lambda a: __import__("ddxes.web.__main__", fromlist=["main"]).main(["--host", a.host, "--port", a.port]))

    sp = sub.add_parser("build", help="rebuild the knowledge graph")
    sp.set_defaults(func=lambda a: runpy.run_path(str(ROOT / "scripts" / "build_kg.py"), run_name="__main__"))
    sp = sub.add_parser("evaluate", help="run the evaluation")
    sp.add_argument("rest", nargs=argparse.REMAINDER)
    sp.set_defaults(func=lambda a: (sys.argv.__setitem__(slice(1, None), a.rest),
                                    runpy.run_path(str(ROOT / "scripts" / "evaluate.py"), run_name="__main__")))

    args = p.parse_args(argv)
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    args.func(args)


if __name__ == "__main__":
    main()
