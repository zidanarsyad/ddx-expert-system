"""The inference engine on hand-made and DDXPlus cases."""
import pytest

from ddxes.case import Case
from ddxes.engine import InferenceEngine
from ddxes.evaluate import triage_truth
from ddxes.rulebase import TRIAGE_RANK


def anaphylaxis_case(kb, closed=True):
    c = Case(id="ana", age=30, sex="F", closed_world=closed)
    c.answer(kb, "E_42", True)          # contact with an allergen
    c.answer(kb, "E_129", True)         # skin lesions
    c.answer(kb, "E_66", True)          # dyspnoea
    c.answer(kb, "E_82", True)          # near-syncope
    return c


def test_sampson_criterion_fires_and_triage_is_emergency(kb, engine):
    res = engine.run(anaphylaxis_case(kb))
    assert res.fired("A-SKIN") and res.fired("A-RESP") and res.fired("D-ANA-1")
    assert res.ranking[0] == "Anaphylaxis"
    assert res.triage == "Emergency"
    assert "REC_ADRENALINE" in [r for r, _, _ in res.recommendations]


def test_rules_fire_in_dependency_order(kb, engine):
    res = engine.run(anaphylaxis_case(kb))
    step = {f.rule.id: f.step for f in res.trace}
    assert step["A-SKIN"] < step["D-ANA-1"] and step["A-RESP"] < step["D-ANA-1"]
    assert step["D-ANA-1"] < step["T-ANA"] < step["R-ANA"]   # triage and recommendations after aggregation


def test_open_world_premise_is_unknown_and_lists_missing_questions(kb, engine):
    c = Case(id="ow", age=30, sex="F", closed_world=False)
    c.answer(kb, "E_42", True)
    res = engine.run(c)
    ev = res.rule_evals["D-ANA-1"]
    assert ev.status is None
    assert "E_66" in ev.missing and "E_129" in ev.missing
    closed = engine.run(Case(id="cw", age=30, sex="F", yes={"E_42"}, closed_world=True))
    assert closed.rule_evals["D-ANA-1"].status is False


def test_set_covering_excludes_pathologies_that_cannot_explain_a_symptom(kb, engine):
    c = Case(id="cov", age=40, sex="M", yes={"E_173", "E_201"}, closed_world=True)  # heartburn + cough
    res = engine.run(c)
    for h in res.hypotheses:
        profile = kb.pathologies[h.pathology].profile
        assert h.excluded == (not {"E_173", "E_201"} <= profile)
    assert res.ranking[0] == "GERD"


def test_no_single_cover_keeps_the_most_parsimonious(kb, engine):
    # stridor (croup...) and heartburn (GERD): no pathology explains both
    c = Case(id="two", age=40, sex="M", yes={"E_173", "E_194"}, closed_world=True)
    res = engine.run(c)
    assert res.ranking, "candidates must remain"
    assert all(len(h.unexplained) == 1 for h in res.hypotheses if not h.excluded)


def test_log_domain_combination_equals_mycin_when_not_saturated(kb, engine):
    from ddxes.certainty import combine_all
    res = engine.run(anaphylaxis_case(kb))
    for h in res.hypotheses:
        if not h.excluded and abs(h.cf) < 0.99:
            assert h.cf == pytest.approx(combine_all(c.cf for c in h.contributions), abs=1e-6)


def test_ablation_switches(kb):
    c = anaphylaxis_case(kb)
    no_rules = InferenceEngine(kb, use_rules=False).run(c)
    assert not any(x.kind == "rule" for h in no_rules.hypotheses for x in h.contributions)
    with pytest.raises(TypeError):
        InferenceEngine(kb, nonsense=True)


def test_accuracy_and_safety_on_demo_patients(kb, engine, sample):
    hits, under_emergency = 0, 0
    for rec in sample[::5]:                       # 196 patients, 4 per pathology
        res = engine.run(Case.from_ddxplus(rec, kb))
        hits += res.ranking[:1] == [rec["pathology"]]
        truth = triage_truth(kb, rec["pathology"])
        if truth == "Emergency":
            under_emergency += res.triage != "Emergency"
        assert TRIAGE_RANK[res.triage] <= TRIAGE_RANK[truth] + 1
    assert hits / len(sample[::5]) >= 0.95
    assert under_emergency == 0
