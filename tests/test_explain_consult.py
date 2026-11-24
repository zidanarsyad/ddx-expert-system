"""Explanations, provenance and the interactive consultation."""
import json
import os
import pathlib
import subprocess
import sys

import pytest
from rdflib.namespace import RDF

from ddxes.case import Case
from ddxes.consult import Consultation, simulate
from ddxes.explain import Explainer, tree_to_dot, tree_to_markdown
from ddxes.paths import DDX, PROCESSED, PROV
from ddxes.provenance import result_to_graph

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"


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


REPLAY = """
import json, sys
sys.path.insert(0, sys.argv[1])
from ddxes.case import Case
from ddxes.consult import Consultation
from ddxes.engine import InferenceEngine
from ddxes.kb import KnowledgeBase
from ddxes.paths import PROCESSED
kb = KnowledgeBase.load()
engine = InferenceEngine(kb)
sample = {r["id"]: r for r in json.loads((PROCESSED / "sample_patients.json").read_text(encoding="utf-8"))}
out = []
for pid in sys.argv[2:]:
    rec = sample[pid]
    full = Case.from_ddxplus(rec, kb)
    cons = Consultation(engine, age=full.age, sex=full.sex, chief_complaint=rec["initial"])
    while (q := cons.next_question()) is not None:
        ev = kb.evidences[q.evidence]
        if ev.dtype == "B":
            ans = q.evidence in full.yes
        elif ev.is_scale:
            ans = full.scales.get(q.evidence, int(ev.default or 0))
        else:
            ans = full.answers.get(q.evidence, {ev.default})
        out.append([pid, q.evidence, q.why])
        cons.answer(q.evidence, ans)
    out.append([pid, cons.result.ranking[:3]])
print(json.dumps(out))
"""


@pytest.mark.skipif(not (PROCESSED / "sample_patients.json").exists(), reason="demo patients not built")
def test_consultation_does_not_depend_on_the_hash_seed():
    """Python orders sets of strings differently in every process (PYTHONHASHSEED), and the knowledge base keeps
    evidences in sets; the same answers must still give the same questions, WHY texts and ranking. Before the
    consultation walked its sets in sorted order, seeds 1 and 3 disagreed on these demo patients: the rule named for
    T056557's first question, the question order for T061607, and the order and ranking for T002570."""
    ids = ["T056557", "T061607", "T002570"]
    runs = []
    for seed in ("1", "3"):
        done = subprocess.run([sys.executable, "-c", REPLAY, str(SRC), *ids], capture_output=True, text=True,
                              check=True, env={**os.environ, "PYTHONHASHSEED": seed})
        runs.append(json.loads(done.stdout))
    assert runs[0] == runs[1]
    first = next(step for step in runs[0] if step[0] == "T056557")
    assert "Rule D-PE-1 (Wells et al. 2000)" in first[2]   # of two rules for the same goal, the larger CF is named
