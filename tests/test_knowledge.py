"""The rule base and the knowledge graph."""
from rdflib import Graph

from ddxes import rulebase as rb
from ddxes.paths import DDX, KNOWLEDGE


def test_rule_base_is_valid(kb):
    rules, knowledge = rb.load_yaml(KNOWLEDGE / "rules.yaml")
    problems = rb.validate(rules, knowledge, set(kb.evidences), set(kb.values), set(kb.pathologies))
    assert problems == []


def test_yaml_rdf_round_trip(kb):
    rules, knowledge = rb.load_yaml(KNOWLEDGE / "rules.yaml")
    g = Graph()
    rb.knowledge_to_graph(knowledge, g)
    rb.rules_to_graph(rules, g)
    assert rb.rules_from_graph(g, knowledge.value_sets) == rules


def test_engine_reads_the_rules_from_the_graph(kb):
    rules, _ = rb.load_yaml(KNOWLEDGE / "rules.yaml")
    assert kb.rules == rules
    assert len(kb.rules) >= 150


def test_validation_catches_bad_references(kb):
    rules, knowledge = rb.load_yaml(KNOWLEDGE / "rules.yaml")
    bad = rb.Rule("X-1", "bad", rb.AllOf((rb.Has("E_999"), rb.ConceptHolds("Nope"))), (rb.Consequent("diagnosis", "Flu"),),
                  source="nobody")
    problems = rb.validate(rules + [bad], knowledge, set(kb.evidences), set(kb.values), set(kb.pathologies))
    assert any("E_999" in p for p in problems)
    assert any("Nope" in p for p in problems)
    assert any("Flu" in p for p in problems)
    assert any("nobody" in p for p in problems)


def test_every_pathology_has_a_positive_guideline_rule(kb):
    covered = {c.target for r in kb.rules if r.category == "diagnostic"
               for c in r.consequents if c.kind == "diagnosis" and r.consequent_cf(c) > 0}
    assert covered == set(kb.pathologies)


def test_every_rule_cites_a_source_with_doi_or_url(kb):
    for r in kb.rules:
        src = kb.knowledge.sources[r.source]
        assert src.get("doi") or src.get("url"), r.id


def test_owl_rl_entailments_are_used(kb):
    # :characterizedBy exists only by rdfs:subPropertyOf entailment; the disease profiles are read from it
    g = kb.graph
    assert len(set(g.triples((None, DDX.characterizedBy, None)))) == 888
    # class membership from owl:hasValue restrictions
    assert {"LifeThreateningPathology", "UrgentPathology", "InfectiousDisease"} <= kb.pathologies["Ebola"].classes
    assert "LifeThreateningPathology" not in kb.pathologies["URTI"].classes
    # rule classes from the structure of the rule (someValuesFrom on the left-hand side)
    assert "RedFlagRule" in kb.rule_classes["T-ANA"]
    assert "RedFlagRule" not in kb.rule_classes["T-PTX"]          # concludes Urgent, not Emergency
    assert "CriteriaBasedRule" in kb.rule_classes["D-ANA-1"]       # source is a set of diagnostic criteria


def test_stored_calibration_matches_the_rule_schema(kb):
    g = kb.graph
    for d, f in [("Pneumonia", "E_91"), ("PulmonaryEmbolism", "E_45"), ("ClusterHeadache", "E_55=V_126")]:
        node = DDX[f"S_{d}_{f.replace('=', '_')}"]
        stored = float(g.value(node, DDX.certaintyFactor))
        assert abs(stored - kb.strength(d, f).cf) < 1e-3
