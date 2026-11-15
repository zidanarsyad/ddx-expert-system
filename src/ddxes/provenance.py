"""Write a consultation back into the knowledge graph, with W3C PROV-O provenance.

Every inferred fact (clinical concept, differential entry, exclusion, triage level, recommendation) becomes a
:Inference node linked to the case, and to the :RuleFiring activities that generated it; each firing points to
the rule it executed (:firedRule) and to the facts it used (prov:used). The rule, in turn, carries its
dct:source, so SPARQL can answer questions such as "which guideline made this case an emergency?"
(queries/q08-provenance.rq). A provenance node is used instead of an RDF 1.2 annotation for the same reason as
in the course's GraphDB project: RDF-star support in the tooling is still uneven.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from rdflib import Graph, Literal
from rdflib.namespace import RDF, RDFS, XSD

from . import rulebase as rb
from .paths import DCT, DDX, PROV


def _dec(x: float) -> Literal:
    return Literal(Decimal(f"{x:.6f}"), datatype=XSD.decimal)


def result_to_graph(result, kb, include_case: bool = True) -> Graph:
    g = Graph()
    for prefix, ns in (("", DDX), ("prov", PROV), ("dct", DCT)):
        g.bind(prefix, ns)
    case = result.case
    cid = case.id.replace(" ", "_")
    pat = DDX["Patient_" + cid]
    run = DDX[f"Run_{cid}"]

    if include_case:
        g.add((pat, RDF.type, DDX.Patient))
        g.add((pat, DDX.code, Literal(case.id)))
        if case.age is not None:
            g.add((pat, DDX.age, Literal(case.age)))
        if case.sex:
            g.add((pat, DDX.sex, DDX.Female if case.sex == "F" else DDX.Male))
        for e in sorted(case.yes):
            g.add((pat, DDX.hasEvidence, DDX[e]))
        for e, vs in case.answers.items():
            for v in vs:
                a = DDX[f"Answer_{cid}_{e}_{v}"]
                g.add((pat, DDX.hasAnswer, a))
                g.add((a, RDF.type, DDX.Answer))
                g.add((a, DDX.ofEvidence, DDX[e]))
                g.add((a, DDX.answerValue, DDX[v]))
        for e, v in case.scales.items():
            a = DDX[f"Answer_{cid}_{e}_{v}"]
            g.add((pat, DDX.hasAnswer, a))
            g.add((a, RDF.type, DDX.Answer))
            g.add((a, DDX.ofEvidence, DDX[e]))
            g.add((a, DDX.scaleValue, Literal(int(v))))
        if case.initial:
            g.add((pat, DDX.initialEvidence, DDX[case.initial]))

    g.add((run, RDF.type, PROV.Activity))
    g.add((run, RDFS.label, Literal(f"DDX-ES consultation of {case.id}", lang="en")))
    g.add((run, PROV.wasAssociatedWith, DDX.DDXES))
    g.add((run, PROV.used, DDX.RuleBase))
    g.add((run, PROV.used, pat))
    g.add((run, PROV.endedAtTime, Literal(dt.datetime.now(dt.timezone.utc).replace(microsecond=0), datatype=XSD.dateTime)))

    concept_node = lambda name: DDX[f"Inf_{cid}_C_{name}"]  # noqa: E731
    diag_node = lambda d: DDX[f"Inf_{cid}_D_{d}"]  # noqa: E731
    firing_node = lambda f: DDX[f"Firing_{cid}_{f.step}"]  # noqa: E731

    for f in result.trace:
        n = firing_node(f)
        g.add((n, RDF.type, DDX.RuleFiring))
        g.add((n, DDX.firedRule, rb.iri_rule(f.rule.id)))
        g.add((n, DDX.step, Literal(f.step)))
        g.add((n, DDX.premiseCertainty, _dec(f.premise_cf)))
        g.add((n, PROV.wasInformedBy, run))
        g.add((n, PROV.wasAssociatedWith, DDX.DDXES))
        for fact in f.used:
            if fact.kind == "evidence" and fact.value:
                g.add((n, PROV.used, DDX[fact.key]))
            elif fact.kind == "answer":
                for v in fact.value:
                    g.add((n, PROV.used, DDX[f"Answer_{cid}_{fact.key}_{v}"]))
            elif fact.kind == "scale":
                g.add((n, PROV.used, DDX[f"Answer_{cid}_{fact.key}_{fact.value}"]))
            elif fact.kind == "concept" and fact.value:
                g.add((n, PROV.used, concept_node(fact.key)))
            elif fact.kind == "belief":
                g.add((n, PROV.used, diag_node(fact.key)))
            elif fact.kind == "score":
                g.add((n, PROV.used, rb.iri_score(fact.key)))

    for name, cf in result.concepts.items():
        n = concept_node(name)
        g.add((n, RDF.type, DDX.ConceptInference))
        g.add((n, DDX.aboutCase, pat))
        g.add((n, DDX.inferredConcept, rb.iri_concept(name)))
        g.add((n, DDX.certainty, _dec(cf)))
        for f in result.concept_firings.get(name, []):
            g.add((n, PROV.wasGeneratedBy, firing_node(f)))

    for h in result.differential():
        n = diag_node(h.pathology)
        g.add((n, RDF.type, DDX.DiagnosisInference))
        g.add((n, DDX.aboutCase, pat))
        g.add((n, DDX.inferredPathology, DDX[h.pathology]))
        g.add((n, DDX.certainty, _dec(h.cf)))
        g.add((n, DDX.probability, _dec(h.probability)))
        g.add((n, DDX.rank, Literal(h.rank)))
        g.add((n, PROV.wasDerivedFrom, rb.iri_rule("KB-COVER")))
        g.add((n, PROV.wasDerivedFrom, rb.iri_rule("KB-EVIDENCE")))
        for c in h.contributions:
            if c.kind == "rule":
                g.add((n, PROV.wasGeneratedBy, firing_node(c.firing)))
        strongest = sorted((c for c in h.contributions if c.kind in ("kb", "kb-absent")), key=lambda c: -abs(c.cf))[:5]
        for c in strongest:
            s = c.strength
            node = DDX[f"S_{s.pathology}_{s.feature.replace('=', '_')}"] if c.kind == "kb" else DDX[f"A_{s.pathology}_{s.feature}"]
            if (node, None, None) in kb.graph:
                g.add((n, PROV.wasInfluencedBy, node))
    for h in result.hypotheses:
        if h.excluded:
            n = DDX[f"Inf_{cid}_X_{h.pathology}"]
            g.add((n, RDF.type, DDX.ExclusionInference))
            g.add((n, DDX.aboutCase, pat))
            g.add((n, DDX.inferredPathology, DDX[h.pathology]))
            g.add((n, PROV.wasDerivedFrom, rb.iri_rule("KB-COVER")))
            for e in h.unexplained:
                g.add((n, DDX.unexplainedEvidence, DDX[e]))

    if result.hypotheses and not result.hypotheses[0].excluded:
        g.add((pat, DDX.hasDiagnosis, DDX[result.hypotheses[0].pathology]))
    g.add((pat, DDX.hasTriage, DDX[result.triage]))
    t = DDX[f"Inf_{cid}_Triage"]
    g.add((t, RDF.type, DDX.TriageInference))
    g.add((t, DDX.aboutCase, pat))
    g.add((t, DDX.inferredTriage, DDX[result.triage]))
    for reason in result.triage_reasons:
        g.add((t, DDX.certainty, _dec(reason.cf)))
        if reason.firing is not None:
            g.add((t, PROV.wasGeneratedBy, firing_node(reason.firing)))
        else:
            g.add((t, PROV.wasDerivedFrom, rb.iri_rule("KB-SEVERITY")))
            g.add((t, PROV.wasDerivedFrom, diag_node(reason.pathology)))
        break  # the certainty of the deciding reason

    for rec, cf, f in result.recommendations:
        n = DDX[f"Inf_{cid}_R_{rec}"]
        g.add((n, RDF.type, DDX.RecommendationInference))
        g.add((n, DDX.aboutCase, pat))
        g.add((n, DDX.inferredRecommendation, DDX[rec]))
        g.add((n, DDX.certainty, _dec(cf)))
        g.add((n, PROV.wasGeneratedBy, firing_node(f)))
    return g
