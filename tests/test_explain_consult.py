"""Explanations, provenance and the interactive consultation."""
from rdflib.namespace import RDF

from ddxes.case import Case
from ddxes.consult import Consultation, simulate
from ddxes.explain import Explainer, tree_to_dot, tree_to_markdown
from ddxes.paths import DDX, PROV
from ddxes.provenance import result_to_graph


def pe_patient(sample):
    return next(r for r in sample if r["id"] == "T002456")


def test_how_explanation_cites_rules_and_sources(kb, engine, sample):
    ex = Explainer(engine)
    res = engine.run(Case.from_ddxplus(pe_patient(sample), kb))
    node = ex.diagnosis(res, "PulmonaryEmbolism")
    assert node["rank"] == 1 and node["covering"]["unexplained"] == []
    ids = {r["rule"]["id"] for r in node["rules"]}
    assert "D-PE-1" in ids
    wells = next(r for r in node["rules"] if r["rule"]["id"] == "D-PE-1")
    assert "Wells" in wells["rule"]["citation"] and wells["rule"]["link"].startswith("https://doi.org/")
    # the proof reaches down to the patient's answers through the Wells score and its scoring rules
    md = tree_to_markdown(ex.how(res, "score", "WellsPE"))
    assert "S-WELLS-DVT" in md and "S-WELLS-IMMOB" in md
    assert tree_to_dot(node).startswith("digraph")
    report = ex.report(res, trace=True)
    assert "Triage: Emergency" in report and "Rule trace" in report


def test_why_not_explains_set_covering_exclusion(kb, engine, sample):
    ex = Explainer(engine)
    res = engine.run(Case.from_ddxplus(pe_patient(sample), kb))
    w = ex.why_not(res, "Pneumonia")
    assert w["excluded"] and "cannot explain" in w["reason"]
    assert any(not g["fired"] and g["tree"] for g in w["guideline_rules"])


def test_provenance_graph_links_inferences_to_rules(kb, engine, sample):
    res = engine.run(Case.from_ddxplus(pe_patient(sample), kb))
    g = result_to_graph(res, kb)
    triage = next(g.subjects(RDF.type, DDX.TriageInference))
    firing = g.value(triage, PROV.wasGeneratedBy)
    assert g.value(firing, DDX.firedRule) == DDX["Rule_T-PE"]
    assert (DDX.Patient_T002456, DDX.hasTriage, DDX.Emergency) in g
    assert any(True for _ in g.subjects(RDF.type, DDX.ExclusionInference))


def test_consultation_asks_parent_questions_first_and_explains_why(kb, engine):
    cons = Consultation(engine, age=60, sex="M", chief_complaint="E_201")
    for _ in range(8):
        q = cons.next_question()
        if q is None:
            break
        parent = kb.evidences[q.evidence].parent
        assert not parent or cons.case.asked(parent), "a sub-question was asked before its parent"
        assert q.why and q.gain > 0
        ev = kb.evidences[q.evidence]
        cons.answer(q.evidence, False if ev.dtype == "B" else ({ev.default} if not ev.is_scale else 0))


def test_simulated_consultations_reach_the_diagnosis(engine, sample):
    recs = sample[::49][:10]
    sims = [simulate(engine, r) for r in recs]
    assert sum(s["top3"] for s in sims) >= 8
    assert all(s["questions"] <= 30 for s in sims)
