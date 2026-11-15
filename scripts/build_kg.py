"""Step 2 of the pipeline: data/processed + knowledge/rules.yaml -> the knowledge graph in kg/.

Writes
  kg/kb.ttl        domain knowledge base: 49 pathologies, 223 evidences, answer values, ICD-10, severity, and the
                   expert-curated pathology -> symptom / antecedent links; priors of the validation split
  kg/rules.ttl     the rule base as RDF, with its sources, value sets, clinical concepts, scores, recommendations
  kg/stats.ttl     calibration of the knowledge-base links: counts, likelihood ratios and certainty factors
  kg/cases.ttl     the 980 demo patients (20 per pathology) of the test split
  kg/inferred.ttl  the OWL 2 RL closure of ontology + kb + rules (owlrl), only the new, non-trivial triples
  kg/stats.json    triple counts

Before writing, every reference in the rule base is checked (evidences, values, concepts, pathologies, sources),
and the round trip YAML -> RDF -> rules must give back the same rules.

Usage (from the repository root):  python -I scripts/build_kg.py
"""
import json
import pathlib
import sys
import time
from collections import Counter
from decimal import Decimal

import owlrl
from rdflib import BNode, Graph, Literal
from rdflib.namespace import OWL, RDF, RDFS, XSD

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from ddxes import rulebase as rb  # noqa: E402
from ddxes.certainty import cf_from_lr  # noqa: E402
from ddxes.kb import ALPHA  # noqa: E402
from ddxes.paths import BIBO, DCT, DDX, KG, KNOWLEDGE, PROCESSED, PROV, SKOS  # noqa: E402

P = lambda name: json.loads((PROCESSED / name).read_text(encoding="utf-8"))  # noqa: E731


def new_graph() -> Graph:
    g = Graph()
    for prefix, ns in (("", DDX), ("dct", DCT), ("prov", PROV), ("skos", SKOS), ("bibo", BIBO), ("owl", OWL)):
        g.bind(prefix, ns)
    return g


def dec(x, digits=6) -> Literal:
    return Literal(Decimal(f"{x:.{digits}g}"), datatype=XSD.decimal)


def build_kb(pathologies, evidences, values, links, stats_path):
    g = new_graph()
    chapters = {p["chapter"]: (p["chapter_title"], p["chapter_range"]) for p in pathologies}
    for ch, (title, rng) in chapters.items():
        n = DDX["ICD10_" + ch]
        g.add((n, RDF.type, DDX.ICD10Chapter))
        g.add((n, DDX.code, Literal(ch)))
        g.add((n, RDFS.label, Literal(f"ICD-10 {ch}: {title} ({rng})", lang="en")))
    for lvl in range(1, 6):
        n = DDX[f"Severity{lvl}"]
        g.add((n, RDF.type, DDX.SeverityLevel))
        g.add((n, DDX.code, Literal(lvl)))
        g.add((n, RDFS.label, Literal(f"Severity {lvl}" + (" (most severe)" if lvl == 1 else " (least severe)" if lvl == 5 else ""), lang="en")))
    n_total = sum(x["patients"] for x in stats_path)
    n_by = {x["pathology"]: x["patients"] for x in stats_path}
    for p in pathologies:
        n = DDX[p["id"]]
        g.add((n, RDF.type, DDX.Pathology))
        g.add((n, DDX.code, Literal(p["id"])))
        g.add((n, RDFS.label, Literal(p["name_en"], lang="en")))
        g.add((n, RDFS.label, Literal(p["name_fr"], lang="fr")))
        g.add((n, DDX.icd10Code, Literal(p["icd10"])))
        g.add((n, DDX.inChapter, DDX["ICD10_" + p["chapter"]]))
        g.add((n, DDX.severity, Literal(p["severity"])))
        g.add((n, DDX.severityLevel, DDX[f"Severity{p['severity']}"]))
        g.add((n, DDX.validationPatients, Literal(n_by[p["id"]])))
        g.add((n, DDX.prior, dec(n_by[p["id"]] / n_total)))
    for l in links:
        g.add((DDX[l["pathology"]], DDX.hasSymptom if l["kind"] == "symptom" else DDX.hasAntecedent, DDX[l["evidence"]]))
    dtype_class = {"B": DDX.BinaryEvidence, "C": DDX.CategoricalEvidence, "M": DDX.MultiChoiceEvidence}
    for e in evidences:
        n = DDX[e["code"]]
        g.add((n, RDF.type, DDX.Evidence))
        g.add((n, RDF.type, DDX.Antecedent if e["antecedent"] else DDX.Symptom))
        g.add((n, RDF.type, dtype_class[e["data_type"]]))
        g.add((n, DDX.code, Literal(e["code"])))
        g.add((n, RDFS.label, Literal(e["label"], lang="en")))
        g.add((n, DDX.question, Literal(e["question_en"], lang="en")))
        g.add((n, DDX.question, Literal(e["question_fr"], lang="fr")))
        g.add((n, DDX.defaultValue, Literal(e["default"])))
        if e["parent"]:
            g.add((n, DDX.subQuestionOf, DDX[e["parent"]]))
        if e["values"] and all(v.isdigit() for v in e["values"]):
            g.add((n, DDX.scaleMax, Literal(max(int(v) for v in e["values"]))))
        else:
            for v in e["values"]:
                g.add((n, DDX.hasPossibleValue, DDX[v]))
    for v in values:
        n = DDX[v["code"]]
        g.add((n, RDF.type, DDX.AnswerValue))
        g.add((n, DDX.code, Literal(v["code"])))
        g.add((n, RDFS.label, Literal(v["en"], lang="en")))
        g.add((n, RDFS.label, Literal(v["fr"], lang="fr")))
    return g, n_total


def build_stats(pathologies, evidences, links, stats_path, stats_feat, stats_ev, n_total):
    """One :EvidenceStrength node per observed (pathology, feature) pair and per (pathology, yes/no profile evidence)."""
    g = new_graph()
    n_by = {x["pathology"]: x["patients"] for x in stats_path}
    totals = Counter()
    for x in stats_feat:
        totals[x["feature"]] += x["count"]
    ev_count = {(x["pathology"], x["evidence"]): x["count"] for x in stats_ev}
    ev_total = Counter()
    for (d, e), c in ev_count.items():
        ev_total[e] += c
    ev_type = {e["code"]: e for e in evidences}
    for x in stats_feat:
        d, f, c = x["pathology"], x["feature"], x["count"]
        e, _, v = f.partition("=")
        p_d = (c + ALPHA) / (n_by[d] + 2 * ALPHA)
        p_o = (totals[f] - c + ALPHA) / (n_total - n_by[d] + 2 * ALPHA)
        n = DDX[f"S_{d}_{f.replace('=', '_')}"]
        g.add((n, RDF.type, DDX.EvidenceStrength))
        g.add((n, DDX.ofPathology, DDX[d]))
        g.add((n, DDX.ofEvidence2, DDX[e]))
        if v:
            if v.isdigit():
                g.add((n, DDX.atScaleValue, Literal(int(v))))
            else:
                g.add((n, DDX.ofValue, DDX[v]))
        g.add((n, DDX.polarity, Literal("present")))
        g.add((n, DDX.patientCount, Literal(c)))
        g.add((n, DDX.pGivenPathology, dec(p_d)))
        g.add((n, DDX.pGivenOthers, dec(p_o)))
        g.add((n, DDX.likelihoodRatio, dec(p_d / p_o)))
        g.add((n, DDX.certaintyFactor, dec(cf_from_lr(p_d / p_o), 4)))
    for l in links:
        d, e = l["pathology"], l["evidence"]
        if ev_type[e]["data_type"] != "B":
            continue
        c = ev_count.get((d, e), 0)
        q_d = 1 - (c + ALPHA) / (n_by[d] + 2 * ALPHA)
        q_o = 1 - (ev_total[e] - c + ALPHA) / (n_total - n_by[d] + 2 * ALPHA)
        n = DDX[f"A_{d}_{e}"]
        g.add((n, RDF.type, DDX.EvidenceStrength))
        g.add((n, DDX.ofPathology, DDX[d]))
        g.add((n, DDX.ofEvidence2, DDX[e]))
        g.add((n, DDX.polarity, Literal("absent")))
        g.add((n, DDX.patientCount, Literal(c)))
        g.add((n, DDX.pGivenPathology, dec(q_d)))
        g.add((n, DDX.pGivenOthers, dec(q_o)))
        g.add((n, DDX.likelihoodRatio, dec(q_d / q_o)))
        g.add((n, DDX.certaintyFactor, dec(cf_from_lr(q_d / q_o), 4)))
    # the rule schemas whose instances these nodes are, so that the graph documents itself
    for rid, label, comment, src in [
        ("KB-COVER", "Parsimonious set covering",
         "IF a symptom is present AND pathology d is not characterizedBy it THEN d is excluded (unless no pathology explains every symptom).",
         "reggia1983"),
        ("KB-EVIDENCE", "Evidence strength of a knowledge-base link",
         "IF feature f of the profile of d is present (absent) THEN d with CF = Heckerman(lr), lr from :EvidenceStrength.",
         "heckerman1986"),
        ("KB-SEVERITY", "Severity triage",
         "IF P(d) >= threshold THEN triage level from the DDXPlus severity of d (1 Emergency, 2 Urgent, 3-4 Non-urgent, 5 Self-care).",
         "ddxplus2022")]:
        n = DDX["Rule_" + rid]
        g.add((n, RDF.type, DDX.RuleSchema))
        g.add((n, DDX.code, Literal(rid)))
        g.add((n, RDFS.label, Literal(label, lang="en")))
        g.add((n, DDX.ruleText, Literal(comment, lang="en")))
        g.add((n, DCT.source, rb.iri_source(src)))
    return g


def build_cases(sample, evidences):
    g = new_graph()
    scale = {e["code"] for e in evidences if e["values"] and all(v.isdigit() for v in e["values"])}
    for p in sample:
        n = DDX["Patient_" + p["id"]]
        g.add((n, RDF.type, DDX.Patient))
        g.add((n, DDX.code, Literal(p["id"])))
        g.add((n, RDFS.label, Literal(f"Patient {p['id']} ({p['sex']}, {p['age']})", lang="en")))
        g.add((n, DDX.age, Literal(p["age"])))
        g.add((n, DDX.sex, DDX.Female if p["sex"] == "F" else DDX.Male))
        g.add((n, DDX.hasPathology, DDX[p["pathology"]]))
        g.add((n, DDX.initialEvidence, DDX[p["initial"]]))
        for e in p["binary"]:
            g.add((n, DDX.hasEvidence, DDX[e]))
        for e, v in p["findings"]:
            a = DDX[f"Answer_{p['id']}_{e}_{v}"]
            g.add((n, DDX.hasAnswer, a))
            g.add((a, RDF.type, DDX.Answer))
            g.add((a, DDX.ofEvidence, DDX[e]))
            if e in scale:
                g.add((a, DDX.scaleValue, Literal(int(v))))
            else:
                g.add((a, DDX.answerValue, DDX[v]))
    return g


def owl_rl_closure(base: Graph) -> Graph:
    """Materialise the OWL 2 RL entailments with owlrl and keep only the new, informative triples."""
    work = Graph()
    for t in base:
        work.add(t)
    owlrl.DeductiveClosure(owlrl.OWLRL_Semantics, axiomatic_triples=False, datatype_axioms=False).expand(work)
    inferred = new_graph()
    vocab = (str(OWL), str(RDF), str(RDFS), str(XSD))
    for s, p, o in work:
        if (s, p, o) in base:
            continue
        if p == OWL.sameAs and s == o:
            continue
        if p == RDF.type and o in (OWL.Thing, RDFS.Resource, OWL.NamedIndividual, RDFS.Class, OWL.Class):
            continue
        # a blank node is local to the file it is written in: an entailed triple that mentions the anonymous
        # restriction classes of ontology.ttl (or the condition nodes of rules.ttl) would point at a different,
        # meaningless node once inferred.ttl is loaded on its own, so such triples are not kept
        if isinstance(s, BNode) or isinstance(o, BNode):
            continue
        if str(s).startswith(vocab) or isinstance(s, Literal):
            continue
        inferred.add((s, p, o))
    return inferred


def main():
    t0 = time.time()
    pathologies, evidences, values = P("pathologies.json"), P("evidences.json"), P("values.json")
    links, stats_path = P("kb_links.json"), P("stats_pathology.json")
    stats_feat, stats_ev, sample = P("stats_feature.json"), P("stats_evidence.json"), P("sample_patients.json")

    rules, knowledge = rb.load_yaml(KNOWLEDGE / "rules.yaml")
    problems = rb.validate(rules, knowledge, {e["code"] for e in evidences}, {v["code"] for v in values},
                           {p["id"] for p in pathologies})
    if problems:
        print("rule base problems:\n  " + "\n  ".join(problems))
        sys.exit(1)

    ev_label = {e["code"]: e["label"] for e in evidences}
    renderer = rb.Renderer(evidence_label=lambda e: ev_label.get(e, e),
                           value_label=lambda v: next((x["en"] for x in values if x["code"] == v), v),
                           concept_label=lambda c: knowledge.concepts[c]["label"],
                           pathology_label=lambda d: next(p["name_en"] for p in pathologies if p["id"] == d),
                           score_label=lambda s: knowledge.scores[s]["label"].split(" (")[0],
                           value_set_label=lambda v: knowledge.value_sets[v][0],
                           rec_label=lambda r: knowledge.recommendations[r]["label"])

    kb_g, n_total = build_kb(pathologies, evidences, values, links, stats_path)
    rules_g = new_graph()
    rb.knowledge_to_graph(knowledge, rules_g)
    rb.rules_to_graph(rules, rules_g, text_of=renderer.rule)
    rules_g.add((DDX.RuleBase, RDF.type, PROV.Collection))
    rules_g.add((DDX.RuleBase, RDFS.label, Literal(f"{knowledge.meta.get('title')} v{knowledge.meta.get('version')}", lang="en")))
    for r in rules:
        rules_g.add((DDX.RuleBase, PROV.hadMember, rb.iri_rule(r.id)))

    back = rb.rules_from_graph(rules_g, knowledge.value_sets)
    assert back == rules, "YAML -> RDF -> rules round trip changed the rule base"

    stats_g = build_stats(pathologies, evidences, links, stats_path, stats_feat, stats_ev, n_total)
    cases_g = build_cases(sample, evidences)

    onto = Graph().parse(KG / "ontology.ttl", format="turtle")
    base = Graph()
    for g in (onto, kb_g, rules_g):
        for t in g:
            base.add(t)
    t1 = time.time()
    inferred = owl_rl_closure(base)
    t_reason = time.time() - t1

    for name, g in (("kb.ttl", kb_g), ("rules.ttl", rules_g), ("stats.ttl", stats_g), ("cases.ttl", cases_g),
                    ("inferred.ttl", inferred)):
        g.serialize(KG / name, format="turtle", encoding="utf-8")
    by_pred = Counter(str(p).split("#")[-1].split("/")[-1] for _, p, _ in inferred)
    stats = {"ontology": len(onto), "kb": len(kb_g), "rules": len(rules_g), "stats": len(stats_g), "cases": len(cases_g),
             "inferred": len(inferred), "reasoning_seconds": round(t_reason, 1),
             "rule_counts": dict(Counter(r.category for r in rules)), "sources": len(knowledge.sources),
             "inferred_by_predicate": dict(by_pred.most_common(15))}
    (KG / "stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(json.dumps(stats, indent=2))
    print(f"done in {time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
