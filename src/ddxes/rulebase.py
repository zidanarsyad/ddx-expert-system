"""Rule base: data model of the IF-THEN rules, their YAML authoring format and their RDF form in the KG.

The rules are authored in knowledge/rules.yaml, validated against the vocabulary of the knowledge graph,
written to kg/rules.ttl as RDF (premise = a tree of :Condition nodes, consequents = :Consequent nodes), and
read back from the graph by the inference engine. `rules_from_graph(rules_to_graph(r)) == r` is a test.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass
from decimal import Decimal

import yaml
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.collection import Collection
from rdflib.namespace import RDF, RDFS, XSD

from .paths import BIBO, DCT, DDX, local

OPS = {"ge": operator.ge, "gt": operator.gt, "le": operator.le, "lt": operator.lt, "eq": operator.eq}
OP_TEXT = {"ge": "≥", "gt": ">", "le": "≤", "lt": "<", "eq": "="}
TRIAGE_LEVELS = ("Emergency", "Urgent", "NonUrgent", "SelfCare")
TRIAGE_RANK = {t: i for i, t in enumerate(TRIAGE_LEVELS, 1)}
TRIAGE_LABEL = {"Emergency": "Emergency", "Urgent": "Urgent", "NonUrgent": "Non-urgent", "SelfCare": "Self-care"}
SOURCE_TYPES = ("ClinicalGuideline", "CaseDefinition", "DiagnosticCriteria", "ClinicalDecisionRule",
                "ReviewArticle", "ClinicalStudy", "ExpertKnowledgeBase", "MethodSource")
CATEGORY_CLASS = {"abstraction": DDX.AbstractionRule, "scoring": DDX.ScoringRule, "diagnostic": DDX.DiagnosticRule,
                  "triage": DDX.TriageRule, "recommendation": DDX.RecommendationRule}
FIDELITY = ("faithful", "partial", "proxy")


# ------------------------------------------------------------------------------------------------
#  Conditions (the IF part)
# ------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Has:
    """A yes/no evidence was answered yes (for a categorical evidence: any non-default answer)."""
    evidence: str


@dataclass(frozen=True)
class AnswerIn:
    """One of the answers to a categorical or multi-choice evidence lies in a value set."""
    evidence: str
    values: frozenset
    value_set: str | None = None


@dataclass(frozen=True)
class Scale:
    """The answer to a 0-10 scale evidence compares to a threshold."""
    evidence: str
    op: str
    threshold: float


@dataclass(frozen=True)
class Age:
    op: str
    threshold: float


@dataclass(frozen=True)
class Sex:
    sex: str  # "F" or "M"


@dataclass(frozen=True)
class ConceptHolds:
    concept: str


@dataclass(frozen=True)
class BeliefAtLeast:
    """The probability of a pathology in the differential is at least the threshold."""
    pathology: str
    threshold: float


@dataclass(frozen=True)
class ScoreCmp:
    score: str
    op: str
    threshold: float


@dataclass(frozen=True)
class AllOf:
    items: tuple


@dataclass(frozen=True)
class AnyOf:
    items: tuple


@dataclass(frozen=True)
class AtLeast:
    n: int
    items: tuple


@dataclass(frozen=True)
class Not:
    item: object


ATOMS = (Has, AnswerIn, Scale, Age, Sex, ConceptHolds, BeliefAtLeast, ScoreCmp)


def atoms(cond):
    """All atomic conditions of a condition tree, depth first."""
    if isinstance(cond, (AllOf, AnyOf, AtLeast)):
        for c in cond.items:
            yield from atoms(c)
    elif isinstance(cond, Not):
        yield from atoms(cond.item)
    else:
        yield cond


# ------------------------------------------------------------------------------------------------
#  Consequents (the THEN part) and rules
# ------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Consequent:
    kind: str                   # concept | diagnosis | triage | recommend | score
    target: str
    cf: float | None = None     # overrides the rule's certainty factor for this consequent
    points: float | None = None  # for kind == "score"

    def sort_key(self):
        return ("concept diagnosis score triage recommend".split().index(self.kind), self.target)


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    premise: object
    consequents: tuple
    cf: float = 1.0
    salience: int = 0
    source: str = ""
    fidelity: str = "faithful"
    note: str = ""
    index: int = 0              # position in the authoring file: the last conflict-resolution criterion

    @property
    def category(self) -> str:
        kinds = {c.kind for c in self.consequents}
        for kind, cat in (("triage", "triage"), ("recommend", "recommendation"), ("diagnosis", "diagnostic"),
                          ("score", "scoring"), ("concept", "abstraction")):
            if kind in kinds:
                return cat
        raise ValueError(self.id)

    @property
    def specificity(self) -> int:
        return sum(1 for _ in atoms(self.premise))

    def consequent_cf(self, c: Consequent) -> float:
        return self.cf if c.cf is None else c.cf


@dataclass
class Knowledge:
    """Everything in rules.yaml besides the rules: the vocabulary the rules use."""
    sources: dict          # key -> {type, citation, doi?, url?}
    value_sets: dict       # name -> (label, frozenset of value codes)
    concepts: dict         # name -> {label, red_flag}
    scores: dict           # name -> {label, source}
    recommendations: dict  # name -> {label, source}
    meta: dict


# ------------------------------------------------------------------------------------------------
#  YAML -> objects (with validation against the vocabulary of the knowledge graph)
# ------------------------------------------------------------------------------------------------
def _cmp(node, keys=("ge", "gt", "le", "lt", "eq")):
    found = [(k, node[k]) for k in keys if k in node]
    if len(found) != 1:
        raise ValueError(f"expected exactly one comparator in {node}")
    return found[0][0], float(found[0][1])


def parse_condition(node, value_sets) -> object:
    if not isinstance(node, dict):
        raise ValueError(f"condition must be a mapping: {node!r}")
    if "all" in node:
        return AllOf(tuple(parse_condition(x, value_sets) for x in node["all"]))
    if "any" in node:
        return AnyOf(tuple(parse_condition(x, value_sets) for x in node["any"]))
    if "atleast" in node:
        return AtLeast(int(node["atleast"]), tuple(parse_condition(x, value_sets) for x in node["of"]))
    if "not" in node:
        return Not(parse_condition(node["not"], value_sets))
    if "has" in node:
        return Has(str(node["has"]))
    if "value" in node:
        spec = node["in"]
        if isinstance(spec, str):
            if spec not in value_sets:
                raise ValueError(f"unknown value set {spec}")
            return AnswerIn(str(node["value"]), value_sets[spec][1], spec)
        return AnswerIn(str(node["value"]), frozenset(map(str, spec)), None)
    if "scale" in node:
        op, t = _cmp(node)
        return Scale(str(node["scale"]), op, t)
    if "age" in node:
        op, t = _cmp(node["age"])
        return Age(op, t)
    if "sex" in node:
        return Sex(str(node["sex"]))
    if "concept" in node:
        return ConceptHolds(str(node["concept"]))
    if "belief" in node:
        return BeliefAtLeast(str(node["belief"]), float(node["ge"]))
    if "score" in node:
        op, t = _cmp(node)
        return ScoreCmp(str(node["score"]), op, t)
    raise ValueError(f"unknown condition {node}")


def parse_consequent(node) -> Consequent:
    cf = float(node["cf"]) if "cf" in node else None
    for kind in ("concept", "diagnosis", "triage", "recommend"):
        if kind in node:
            return Consequent(kind, str(node[kind]), cf)
    if "score" in node:
        return Consequent("score", str(node["score"]), cf, float(node["add"]))
    raise ValueError(f"unknown consequent {node}")


def load_yaml(path) -> tuple[list[Rule], Knowledge]:
    doc = yaml.safe_load(open(path, encoding="utf-8"))
    value_sets = {k: (v["label"], frozenset(map(str, v["values"]))) for k, v in doc["value_sets"].items()}
    knowledge = Knowledge(sources=doc["sources"], value_sets=value_sets,
                          concepts={k: {"label": v["label"], "red_flag": bool(v.get("red_flag", False))}
                                    for k, v in doc["concepts"].items()},
                          scores=doc["scores"], recommendations=doc["recommendations"], meta=doc.get("meta", {}))
    rules = []
    for i, r in enumerate(doc["rules"]):
        rules.append(Rule(id=r["id"], name=r["name"], premise=parse_condition(r["if"], value_sets),
                          consequents=tuple(sorted((parse_consequent(c) for c in r["then"]), key=Consequent.sort_key)),
                          cf=float(r.get("cf", 1.0)), salience=int(r.get("salience", 0)), source=r.get("source", ""),
                          fidelity=r.get("fidelity", "faithful"), note=" ".join(str(r.get("note", "")).split()), index=i))
    return rules, knowledge


def validate(rules, knowledge, evidences, values, pathologies) -> list[str]:
    """Check every reference in the rule base; returns a list of problems (empty = valid)."""
    problems = []
    ids = [r.id for r in rules]
    problems += [f"duplicate rule id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    concluded_concepts = {c.target for r in rules for c in r.consequents if c.kind == "concept"}
    for r in rules:
        where = f"rule {r.id}"
        if r.source not in knowledge.sources:
            problems.append(f"{where}: unknown source {r.source!r}")
        if r.fidelity not in FIDELITY:
            problems.append(f"{where}: fidelity must be one of {FIDELITY}")
        if not -1 <= r.cf <= 1:
            problems.append(f"{where}: cf outside [-1, 1]")
        for a in atoms(r.premise):
            if isinstance(a, (Has, AnswerIn, Scale)) and a.evidence not in evidences:
                problems.append(f"{where}: unknown evidence {a.evidence}")
            if isinstance(a, AnswerIn):
                problems += [f"{where}: unknown value {v}" for v in sorted(a.values - set(values))]
            if isinstance(a, ConceptHolds):
                if a.concept not in knowledge.concepts:
                    problems.append(f"{where}: undeclared concept {a.concept}")
                elif a.concept not in concluded_concepts:
                    problems.append(f"{where}: no rule concludes concept {a.concept}")
            if isinstance(a, BeliefAtLeast) and a.pathology not in pathologies:
                problems.append(f"{where}: unknown pathology {a.pathology}")
            if isinstance(a, ScoreCmp) and a.score not in knowledge.scores:
                problems.append(f"{where}: undeclared score {a.score}")
            if isinstance(a, Sex) and a.sex not in ("F", "M"):
                problems.append(f"{where}: sex must be F or M")
        for c in r.consequents:
            if c.kind == "concept" and c.target not in knowledge.concepts:
                problems.append(f"{where}: undeclared concept {c.target}")
            if c.kind == "diagnosis" and c.target not in pathologies:
                problems.append(f"{where}: unknown pathology {c.target}")
            if c.kind == "triage" and c.target not in TRIAGE_LEVELS:
                problems.append(f"{where}: unknown triage level {c.target}")
            if c.kind == "recommend" and c.target not in knowledge.recommendations:
                problems.append(f"{where}: undeclared recommendation {c.target}")
            if c.kind == "score" and c.target not in knowledge.scores:
                problems.append(f"{where}: undeclared score {c.target}")
        if r.category in ("abstraction", "scoring", "diagnostic") and any(isinstance(a, BeliefAtLeast) for a in atoms(r.premise)):
            problems.append(f"{where}: only triage and recommendation rules may read beliefs")
    for key, s in knowledge.sources.items():
        if s.get("type") not in SOURCE_TYPES:
            problems.append(f"source {key}: type must be one of {SOURCE_TYPES}")
        if not (s.get("doi") or s.get("url")):
            problems.append(f"source {key}: needs a doi or a url")
    return problems


# ------------------------------------------------------------------------------------------------
#  IRIs
# ------------------------------------------------------------------------------------------------
def iri_rule(rule_id):
    return DDX["Rule_" + rule_id]


def iri_concept(name):
    return DDX["C_" + name]


def iri_score(name):
    return DDX["Score_" + name]


def iri_source(key):
    return DDX["Src_" + key]


def iri_value_set(name):
    return DDX["VS_" + name]


def _dec(x) -> Literal:
    return Literal(Decimal(repr(float(x))), datatype=XSD.decimal)


# ------------------------------------------------------------------------------------------------
#  objects -> RDF
# ------------------------------------------------------------------------------------------------
def condition_to_rdf(g: Graph, c) -> BNode:
    n = BNode()
    if isinstance(c, (AllOf, AnyOf, AtLeast)):
        g.add((n, RDF.type, {AllOf: DDX.AllOf, AnyOf: DDX.AnyOf, AtLeast: DDX.AtLeast}[type(c)]))
        head = BNode()
        Collection(g, head, [condition_to_rdf(g, x) for x in c.items])
        g.add((n, DDX.operands, head))
        if isinstance(c, AtLeast):
            g.add((n, DDX.minCount, Literal(c.n)))
    elif isinstance(c, Not):
        g.add((n, RDF.type, DDX.Not))
        g.add((n, DDX.operand, condition_to_rdf(g, c.item)))
    elif isinstance(c, Has):
        g.add((n, RDF.type, DDX.EvidencePresent))
        g.add((n, DDX.onEvidence, DDX[c.evidence]))
    elif isinstance(c, AnswerIn):
        g.add((n, RDF.type, DDX.AnswerIn))
        g.add((n, DDX.onEvidence, DDX[c.evidence]))
        if c.value_set:
            g.add((n, DDX.inValueSet, iri_value_set(c.value_set)))
        else:
            for v in sorted(c.values):
                g.add((n, DDX.onValue, DDX[v]))
    elif isinstance(c, Scale):
        g.add((n, RDF.type, DDX.ScaleComparison))
        g.add((n, DDX.onEvidence, DDX[c.evidence]))
        g.add((n, DDX.comparator, Literal(c.op)))
        g.add((n, DDX.threshold, _dec(c.threshold)))
    elif isinstance(c, Age):
        g.add((n, RDF.type, DDX.AgeComparison))
        g.add((n, DDX.comparator, Literal(c.op)))
        g.add((n, DDX.threshold, _dec(c.threshold)))
    elif isinstance(c, Sex):
        g.add((n, RDF.type, DDX.SexIs))
        g.add((n, DDX.onSex, DDX.Female if c.sex == "F" else DDX.Male))
    elif isinstance(c, ConceptHolds):
        g.add((n, RDF.type, DDX.ConceptHolds))
        g.add((n, DDX.onConcept, iri_concept(c.concept)))
    elif isinstance(c, BeliefAtLeast):
        g.add((n, RDF.type, DDX.BeliefAtLeast))
        g.add((n, DDX.onPathology, DDX[c.pathology]))
        g.add((n, DDX.threshold, _dec(c.threshold)))
    elif isinstance(c, ScoreCmp):
        g.add((n, RDF.type, DDX.ScoreComparison))
        g.add((n, DDX.onScore, iri_score(c.score)))
        g.add((n, DDX.comparator, Literal(c.op)))
        g.add((n, DDX.threshold, _dec(c.threshold)))
    else:
        raise TypeError(c)
    return n


CONSEQUENT_RDF = {"concept": (DDX.ConceptConsequent, DDX.concludesConcept, iri_concept),
                  "diagnosis": (DDX.DiagnosisConsequent, DDX.concludesPathology, lambda t: DDX[t]),
                  "triage": (DDX.TriageConsequent, DDX.concludesTriage, lambda t: DDX[t]),
                  "recommend": (DDX.RecommendationConsequent, DDX.concludesRecommendation, lambda t: DDX[t]),
                  "score": (DDX.ScoreConsequent, DDX.onScore, iri_score)}


def knowledge_to_graph(knowledge: Knowledge, g: Graph):
    for key, s in knowledge.sources.items():
        n = iri_source(key)
        g.add((n, RDF.type, DDX[s["type"]]))
        g.add((n, DDX.citationKey, Literal(key)))
        g.add((n, DCT.bibliographicCitation, Literal(" ".join(s["citation"].split()))))
        if s.get("doi"):
            g.add((n, BIBO.doi, Literal(str(s["doi"]))))
            g.add((n, RDFS.seeAlso, URIRef("https://doi.org/" + str(s["doi"]))))
        if s.get("url"):
            g.add((n, RDFS.seeAlso, URIRef(s["url"])))
    for name, (label, members) in knowledge.value_sets.items():
        n = iri_value_set(name)
        g.add((n, RDF.type, DDX.ValueSet))
        g.add((n, RDFS.label, Literal(label, lang="en")))
        g.add((n, DDX.code, Literal(name)))
        for v in sorted(members):
            g.add((n, DDX.hasMember, DDX[v]))
    for name, c in knowledge.concepts.items():
        n = iri_concept(name)
        g.add((n, RDF.type, DDX.RedFlag if c["red_flag"] else DDX.ClinicalConcept))
        g.add((n, RDFS.label, Literal(c["label"], lang="en")))
        g.add((n, DDX.code, Literal(name)))
        g.add((n, DDX.isRedFlag, Literal(c["red_flag"])))
    for name, s in knowledge.scores.items():
        n = iri_score(name)
        g.add((n, RDF.type, DDX.ClinicalScore))
        g.add((n, RDFS.label, Literal(s["label"], lang="en")))
        g.add((n, DDX.code, Literal(name)))
        g.add((n, DCT.source, iri_source(s["source"])))
    for name, rec in knowledge.recommendations.items():
        n = DDX[name]
        g.add((n, RDF.type, DDX.Recommendation))
        g.add((n, RDFS.label, Literal(rec["label"], lang="en")))
        g.add((n, DDX.code, Literal(name)))
        g.add((n, DCT.source, iri_source(rec["source"])))


def rules_to_graph(rules, g: Graph, text_of=None):
    """Write the rules as RDF. `text_of(rule)` renders the human-readable IF-THEN text (optional)."""
    for r in rules:
        n = iri_rule(r.id)
        g.add((n, RDF.type, CATEGORY_CLASS[r.category]))
        g.add((n, DDX.code, Literal(r.id)))
        g.add((n, RDFS.label, Literal(r.name, lang="en")))
        g.add((n, DDX.premise, condition_to_rdf(g, r.premise)))
        for c in r.consequents:
            k = BNode()
            cls, prop, mk = CONSEQUENT_RDF[c.kind]
            g.add((k, RDF.type, cls))
            g.add((k, prop, mk(c.target)))
            if c.cf is not None:
                g.add((k, DDX.certaintyFactor, _dec(c.cf)))
            if c.points is not None:
                g.add((k, DDX.points, _dec(c.points)))
            g.add((n, DDX.asserts, k))
        g.add((n, DDX.certaintyFactor, _dec(r.cf)))
        g.add((n, DDX.salience, Literal(r.salience)))
        g.add((n, DDX.ruleIndex, Literal(r.index)))
        g.add((n, DDX.mappingFidelity, Literal(r.fidelity)))
        if r.source:
            g.add((n, DCT.source, iri_source(r.source)))
        if r.note:
            g.add((n, RDFS.comment, Literal(r.note, lang="en")))
        if text_of:
            g.add((n, DDX.ruleText, Literal(text_of(r), lang="en")))
        # index links for SPARQL: which evidences / concepts / pathologies / scores a rule reads
        for a in atoms(r.premise):
            if isinstance(a, (Has, AnswerIn, Scale)):
                g.add((n, DDX.usesEvidence, DDX[a.evidence]))
            elif isinstance(a, ConceptHolds):
                g.add((n, DDX.usesConcept, iri_concept(a.concept)))
            elif isinstance(a, BeliefAtLeast):
                g.add((n, DDX.usesPathology, DDX[a.pathology]))
            elif isinstance(a, ScoreCmp):
                g.add((n, DDX.usesScore, iri_score(a.score)))


# ------------------------------------------------------------------------------------------------
#  RDF -> objects
# ------------------------------------------------------------------------------------------------
def _num(lit) -> float:
    return float(lit.toPython())


def condition_from_rdf(g: Graph, n, value_sets) -> object:
    types = set(g.objects(n, RDF.type))
    if types & {DDX.AllOf, DDX.AnyOf, DDX.AtLeast}:
        items = tuple(condition_from_rdf(g, x, value_sets) for x in Collection(g, g.value(n, DDX.operands)))
        if DDX.AllOf in types:
            return AllOf(items)
        if DDX.AnyOf in types:
            return AnyOf(items)
        return AtLeast(int(g.value(n, DDX.minCount)), items)
    if DDX.Not in types:
        return Not(condition_from_rdf(g, g.value(n, DDX.operand), value_sets))
    if DDX.EvidencePresent in types:
        return Has(local(g.value(n, DDX.onEvidence)))
    if DDX.AnswerIn in types:
        vs = g.value(n, DDX.inValueSet)
        if vs is not None:
            name = local(vs)[3:]
            return AnswerIn(local(g.value(n, DDX.onEvidence)), value_sets[name][1], name)
        return AnswerIn(local(g.value(n, DDX.onEvidence)), frozenset(local(v) for v in g.objects(n, DDX.onValue)), None)
    if DDX.ScaleComparison in types:
        return Scale(local(g.value(n, DDX.onEvidence)), str(g.value(n, DDX.comparator)), _num(g.value(n, DDX.threshold)))
    if DDX.AgeComparison in types:
        return Age(str(g.value(n, DDX.comparator)), _num(g.value(n, DDX.threshold)))
    if DDX.SexIs in types:
        return Sex("F" if g.value(n, DDX.onSex) == DDX.Female else "M")
    if DDX.ConceptHolds in types:
        return ConceptHolds(local(g.value(n, DDX.onConcept))[2:])
    if DDX.BeliefAtLeast in types:
        return BeliefAtLeast(local(g.value(n, DDX.onPathology)), _num(g.value(n, DDX.threshold)))
    if DDX.ScoreComparison in types:
        return ScoreCmp(local(g.value(n, DDX.onScore))[6:], str(g.value(n, DDX.comparator)), _num(g.value(n, DDX.threshold)))
    raise ValueError(f"unknown condition node {n} with types {types}")


def knowledge_from_graph(g: Graph) -> Knowledge:
    sources = {}
    for n in set(g.subjects(DDX.citationKey, None)):
        types = [local(t) for t in g.objects(n, RDF.type) if local(t) in SOURCE_TYPES]
        doi = g.value(n, BIBO.doi)
        urls = [str(u) for u in g.objects(n, RDFS.seeAlso) if not str(u).startswith("https://doi.org/")]
        sources[str(g.value(n, DDX.citationKey))] = {"type": types[0] if types else "", "citation": str(g.value(n, DCT.bibliographicCitation)),
                                                      **({"doi": str(doi)} if doi else {}), **({"url": urls[0]} if urls else {})}
    value_sets = {str(g.value(n, DDX.code)): (str(g.value(n, RDFS.label)), frozenset(local(v) for v in g.objects(n, DDX.hasMember)))
                  for n in g.subjects(RDF.type, DDX.ValueSet)}
    concepts = {}
    for n in set(g.subjects(RDF.type, DDX.ClinicalConcept)) | set(g.subjects(RDF.type, DDX.RedFlag)):
        concepts[str(g.value(n, DDX.code))] = {"label": str(g.value(n, RDFS.label)), "red_flag": bool(g.value(n, DDX.isRedFlag).toPython())}
    scores = {str(g.value(n, DDX.code)): {"label": str(g.value(n, RDFS.label)), "source": local(g.value(n, DCT.source))[4:]}
              for n in g.subjects(RDF.type, DDX.ClinicalScore)}
    recs = {str(g.value(n, DDX.code)): {"label": str(g.value(n, RDFS.label)), "source": local(g.value(n, DCT.source))[4:]}
            for n in g.subjects(RDF.type, DDX.Recommendation)}
    return Knowledge(sources=sources, value_sets=value_sets, concepts=concepts, scores=scores, recommendations=recs, meta={})


def rules_from_graph(g: Graph, value_sets) -> list[Rule]:
    rules = []
    for n in set(g.subjects(DDX.premise, None)):
        consequents = []
        for k in g.objects(n, DDX.asserts):
            for kind, (cls, prop, _) in CONSEQUENT_RDF.items():
                if (k, RDF.type, cls) in g:
                    target = local(g.value(k, prop))
                    target = target[2:] if kind == "concept" else target[6:] if kind == "score" else target
                    cf, pts = g.value(k, DDX.certaintyFactor), g.value(k, DDX.points)
                    consequents.append(Consequent(kind, target, _num(cf) if cf is not None else None,
                                                  _num(pts) if pts is not None else None))
        comment, src = g.value(n, RDFS.comment), g.value(n, DCT.source)
        rules.append(Rule(id=str(g.value(n, DDX.code)), name=str(g.value(n, RDFS.label)),
                          premise=condition_from_rdf(g, g.value(n, DDX.premise), value_sets),
                          consequents=tuple(sorted(consequents, key=Consequent.sort_key)),
                          cf=_num(g.value(n, DDX.certaintyFactor)), salience=int(g.value(n, DDX.salience)),
                          source=local(src)[4:] if src is not None else "", fidelity=str(g.value(n, DDX.mappingFidelity)),
                          note=str(comment) if comment is not None else "", index=int(g.value(n, DDX.ruleIndex))))
    return sorted(rules, key=lambda r: r.index)


# ------------------------------------------------------------------------------------------------
#  Human-readable rendering
# ------------------------------------------------------------------------------------------------
class Renderer:
    """Turns conditions and rules into text, using the labels of the knowledge base."""

    def __init__(self, evidence_label, value_label, concept_label, pathology_label, score_label, value_set_label,
                 rec_label=None):
        self.ev, self.val, self.con, self.path = evidence_label, value_label, concept_label, pathology_label
        self.score, self.vs, self.rec = score_label, value_set_label, rec_label or (lambda x: x)

    def cond(self, c, top=True) -> str:
        if isinstance(c, AllOf):
            s = " AND ".join(self.cond(x, False) for x in c.items)
        elif isinstance(c, AnyOf):
            s = " OR ".join(self.cond(x, False) for x in c.items)
        elif isinstance(c, AtLeast):
            return f"at least {c.n} of [" + "; ".join(self.cond(x, True) for x in c.items) + "]"
        elif isinstance(c, Not):
            return "NOT " + self.cond(c.item, False)
        elif isinstance(c, Has):
            return self.ev(c.evidence)
        elif isinstance(c, AnswerIn):
            target = self.vs(c.value_set) if c.value_set else ", ".join(self.val(v) for v in sorted(c.values))
            return f"{self.ev(c.evidence)} ∈ {{{target}}}"
        elif isinstance(c, Scale):
            return f"{self.ev(c.evidence)} {OP_TEXT[c.op]} {c.threshold:g}/10"
        elif isinstance(c, Age):
            return f"age {OP_TEXT[c.op]} {c.threshold:g}"
        elif isinstance(c, Sex):
            return "female" if c.sex == "F" else "male"
        elif isinstance(c, ConceptHolds):
            return self.con(c.concept)
        elif isinstance(c, BeliefAtLeast):
            return f"P({self.path(c.pathology)}) ≥ {c.threshold:g}"
        elif isinstance(c, ScoreCmp):
            return f"{self.score(c.score)} {OP_TEXT[c.op]} {c.threshold:g}"
        else:
            raise TypeError(c)
        return s if top else f"({s})"

    def consequent(self, r: Rule, c: Consequent) -> str:
        if c.kind == "concept":
            body = self.con(c.target)
        elif c.kind == "diagnosis":
            body = self.path(c.target)
        elif c.kind == "triage":
            body = f"triage = {TRIAGE_LABEL[c.target]}"
        elif c.kind == "recommend":
            body = f"recommend: {self.rec(c.target)}"
        else:
            return f"{self.score(c.target)} += {c.points:g}"
        cf = r.consequent_cf(c)
        return body if (c.kind in ("concept",) and cf == 1.0) else f"{body} (CF {cf:+.2f})"

    def rule(self, r: Rule) -> str:
        return f"IF {self.cond(r.premise)} THEN " + " AND ".join(self.consequent(r, c) for c in r.consequents)
