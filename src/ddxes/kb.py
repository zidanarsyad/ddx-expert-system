"""The knowledge base the inference engine works with, compiled from the knowledge graph.

`KnowledgeBase.load()` parses the Turtle files of kg/ (ontology, domain KB, rule base, calibration statistics and
the OWL 2 RL closure computed by scripts/build_kg.py) and compiles them into Python structures. Several things
the engine needs exist only as *entailed* triples, so the closure must be present:
  * :characterizedBy links (from rdfs:subPropertyOf of :hasSymptom / :hasAntecedent) -> the disease profiles;
  * class memberships such as :LifeThreateningPathology or :RespiratoryDisease (from owl:hasValue restrictions).
The compiled result is cached in kg/.cache/ and rebuilt automatically when a Turtle file changes.
"""
from __future__ import annotations

import hashlib
import pickle
import warnings
from dataclasses import dataclass
from functools import cached_property

from rdflib import Graph
from rdflib.namespace import RDF, RDFS

from . import rulebase as rb
from .certainty import cf_from_lr
from .paths import DDX, KG, KG_FILES, local

ALPHA = 1.0  # Laplace smoothing of the validation-split counts
SEVERITY_TRIAGE = {1: "Emergency", 2: "Urgent", 3: "NonUrgent", 4: "NonUrgent", 5: "SelfCare"}
CACHE_VERSION = 3


@dataclass(frozen=True)
class Pathology:
    id: str
    name: str
    icd10: str
    chapter: str
    severity: int
    prior: float
    patients: int
    symptoms: frozenset
    antecedents: frozenset
    classes: frozenset          # OWL classes the pathology belongs to (asserted and entailed)

    @property
    def profile(self) -> frozenset:
        return self.symptoms | self.antecedents

    @property
    def triage(self) -> str:
        return SEVERITY_TRIAGE[self.severity]


@dataclass(frozen=True)
class Evidence:
    code: str
    label: str
    question: str
    kind: str                  # "symptom" | "antecedent"
    dtype: str                 # "B" yes/no, "C" categorical (or 0-10 scale), "M" multi-choice
    parent: str | None
    default: str
    values: tuple              # possible answers (value codes, or "0".."10" for a scale)

    @property
    def is_scale(self) -> bool:
        return self.dtype == "C" and bool(self.values) and all(v.isdigit() for v in self.values)


@dataclass(frozen=True)
class Strength:
    """How strongly one feature supports one pathology (one instance of the KB-EVIDENCE rule schema)."""
    pathology: str
    feature: str
    polarity: str              # "present" | "absent"
    p_d: float                 # P(feature | pathology)
    p_other: float             # P(feature | other pathologies)
    lr: float
    cf: float


class KnowledgeBase:
    def __init__(self, pathologies, evidences, values, knowledge, rules, counts, totals, ev_counts, ev_totals, n_total,
                 rule_classes, source_classes):
        self.pathologies: dict[str, Pathology] = pathologies
        self.evidences: dict[str, Evidence] = evidences
        self.values: dict[str, str] = values
        self.knowledge: rb.Knowledge = knowledge
        self.rules: list[rb.Rule] = rules
        self.rule_by_id = {r.id: r for r in rules}
        self._counts, self._totals = counts, totals            # (d, feature) -> n ; feature -> n
        self._ev_counts, self._ev_totals = ev_counts, ev_totals  # (d, evidence) -> n present ; evidence -> n
        self.n_total = n_total
        self.rule_classes = rule_classes                       # rule id -> entailed OWL classes (e.g. RedFlagRule)
        self.source_classes = source_classes
        self._strength_cache = {}
        self.binary_profile = {d: frozenset(e for e in p.profile if evidences[e].dtype == "B")
                               for d, p in pathologies.items()}

    # ----------------------------------------------------------------------------- loading
    @staticmethod
    def _fingerprint(kg_dir) -> str:
        h = hashlib.sha1(str(CACHE_VERSION).encode())
        for name in KG_FILES:
            p = kg_dir / name
            if p.exists():
                st = p.stat()
                h.update(f"{name}:{st.st_size}:{st.st_mtime_ns}".encode())
        return h.hexdigest()[:16]

    @classmethod
    def load(cls, kg_dir=KG, use_cache=True) -> "KnowledgeBase":
        cache = kg_dir / ".cache" / f"kb-{cls._fingerprint(kg_dir)}.pkl"
        if use_cache and cache.exists():
            with open(cache, "rb") as f:
                kb = pickle.load(f)
            kb._kg_dir = kg_dir
            return kb
        graph = Graph()
        for name in KG_FILES:
            p = kg_dir / name
            if p.exists():
                graph.parse(p, format="turtle")
            elif name == "inferred.ttl":
                warnings.warn("kg/inferred.ttl is missing: run scripts/build_kg.py; entailed links will be absent")
        kb = cls.from_graph(graph)
        kb._kg_dir = kg_dir
        if use_cache:
            cache.parent.mkdir(exist_ok=True)
            for old in cache.parent.glob("kb-*.pkl"):
                old.unlink()
            with open(cache, "wb") as f:
                pickle.dump(kb, f)
        kb.__dict__["graph"] = graph
        return kb

    def __getstate__(self):
        state = self.__dict__.copy()
        state.pop("graph", None)
        state.pop("renderer", None)
        state.pop("_answer_index", None)
        state["_strength_cache"] = {}
        return state

    @cached_property
    def graph(self) -> Graph:
        """The full knowledge graph (parsed on first use when the KB came from the cache)."""
        g = Graph()
        for name in KG_FILES:
            p = self._kg_dir / name
            if p.exists():
                g.parse(p, format="turtle")
        return g

    @classmethod
    def from_graph(cls, g: Graph) -> "KnowledgeBase":
        g.bind("", DDX)
        knowledge = rb.knowledge_from_graph(g)
        rules = rb.rules_from_graph(g, knowledge.value_sets)

        values = {local(v): str(g.value(v, RDFS.label)) for v in g.subjects(RDF.type, DDX.AnswerValue)}
        evidences = {}
        for e in g.subjects(RDF.type, DDX.Evidence):
            code = local(e)
            types = set(g.objects(e, RDF.type))
            dtype = "B" if DDX.BinaryEvidence in types else "M" if DDX.MultiChoiceEvidence in types else "C"
            parent = g.value(e, DDX.subQuestionOf)
            scale_max = g.value(e, DDX.scaleMax)
            if scale_max is not None:
                possible = [str(i) for i in range(int(scale_max) + 1)]
            else:
                possible = sorted((local(v) for v in g.objects(e, DDX.hasPossibleValue)), key=lambda v: int(v[2:]))
            evidences[code] = Evidence(code=code, label=str(g.value(e, RDFS.label)), question=str(g.value(e, DDX.question)),
                                       kind="antecedent" if DDX.Antecedent in types else "symptom", dtype=dtype,
                                       parent=local(parent) if parent is not None else None,
                                       default=str(g.value(e, DDX.defaultValue)), values=tuple(possible))

        has_entailed = (None, DDX.characterizedBy, None) in g
        if not has_entailed:
            warnings.warn("no :characterizedBy triples: the OWL 2 RL closure was not loaded; using the asserted sub-properties")
        pathologies = {}
        for p in g.subjects(RDF.type, DDX.Pathology):
            pid = local(p)
            if has_entailed:
                profile = {local(e) for e in g.objects(p, DDX.characterizedBy)}
            else:
                profile = {local(e) for e in g.objects(p, DDX.hasSymptom)} | {local(e) for e in g.objects(p, DDX.hasAntecedent)}
            symptoms = frozenset(e for e in profile if evidences[e].kind == "symptom")
            antecedents = frozenset(e for e in profile if evidences[e].kind == "antecedent")
            classes = frozenset(local(c) for c in g.objects(p, RDF.type) if str(c).startswith(str(DDX)))
            pathologies[pid] = Pathology(id=pid, name=str(g.value(p, RDFS.label)), icd10=str(g.value(p, DDX.icd10Code)),
                                         chapter=local(g.value(p, DDX.inChapter)).replace("ICD10_", ""),
                                         severity=int(g.value(p, DDX.severity)), prior=float(g.value(p, DDX.prior)),
                                         patients=int(g.value(p, DDX.validationPatients)), symptoms=symptoms,
                                         antecedents=antecedents, classes=classes)

        counts, ev_counts = {}, {}
        for s in g.subjects(RDF.type, DDX.EvidenceStrength):
            d, e = local(g.value(s, DDX.ofPathology)), local(g.value(s, DDX.ofEvidence2))
            n = int(g.value(s, DDX.patientCount))
            if str(g.value(s, DDX.polarity)) == "absent":
                ev_counts[(d, e)] = n
            else:
                v = g.value(s, DDX.ofValue)
                sv = g.value(s, DDX.atScaleValue)
                feature = f"{e}={local(v)}" if v is not None else f"{e}={sv}" if sv is not None else e
                counts[(d, feature)] = n
        totals, ev_totals = {}, {}
        for (d, f), n in counts.items():
            totals[f] = totals.get(f, 0) + n
        for (d, e), n in ev_counts.items():
            ev_totals[e] = ev_totals.get(e, 0) + n
        n_total = sum(p.patients for p in pathologies.values())

        rule_classes = {}
        for r in rules:
            node = rb.iri_rule(r.id)
            rule_classes[r.id] = frozenset(local(c) for c in g.objects(node, RDF.type) if str(c).startswith(str(DDX)))
        source_classes = {k: frozenset(local(c) for c in g.objects(rb.iri_source(k), RDF.type) if str(c).startswith(str(DDX)))
                          for k in knowledge.sources}
        return cls(pathologies, evidences, values, knowledge, rules, counts, totals, ev_counts, ev_totals, n_total,
                   rule_classes, source_classes)

    # ----------------------------------------------------------------------------- KB-EVIDENCE rule schema
    def strength(self, d: str, feature: str) -> Strength:
        """Present feature -> pathology. lr = P(f|d) / P(f|not d), Laplace-smoothed counts of the validation split."""
        key = (d, feature, "present")
        s = self._strength_cache.get(key)
        if s is None:
            n_d = self.pathologies[d].patients
            c = self._counts.get((d, feature), 0)
            p_d = (c + ALPHA) / (n_d + 2 * ALPHA)
            p_o = (self._totals.get(feature, 0) - c + ALPHA) / (self.n_total - n_d + 2 * ALPHA)
            s = self._strength_cache[key] = Strength(d, feature, "present", p_d, p_o, p_d / p_o, cf_from_lr(p_d / p_o))
        return s

    def absent_strength(self, d: str, evidence: str) -> Strength:
        """Absent yes/no evidence of the pathology's profile -> pathology."""
        key = (d, evidence, "absent")
        s = self._strength_cache.get(key)
        if s is None:
            n_d = self.pathologies[d].patients
            c = self._ev_counts.get((d, evidence), 0)
            q_d = 1 - (c + ALPHA) / (n_d + 2 * ALPHA)
            q_o = 1 - (self._ev_totals.get(evidence, 0) - c + ALPHA) / (self.n_total - n_d + 2 * ALPHA)
            s = self._strength_cache[key] = Strength(d, evidence, "absent", q_d, q_o, q_d / q_o, cf_from_lr(q_d / q_o))
        return s

    def p_present(self, d: str, evidence: str) -> float:
        """P(evidence is present | d), smoothed: used by the question selection of the consultation."""
        n_d = self.pathologies[d].patients
        return (self._ev_counts.get((d, evidence), 0) + ALPHA) / (n_d + 2 * ALPHA) if evidence in self.pathologies[d].profile \
            else ALPHA / (n_d + 2 * ALPHA)

    def p_feature(self, d: str, feature: str) -> float:
        n_d = self.pathologies[d].patients
        return (self._counts.get((d, feature), 0) + ALPHA) / (n_d + 2 * ALPHA)

    def answer_distribution(self, d: str, evidence: str) -> dict:
        """Observed answers of a categorical evidence for a pathology: value -> share of patients."""
        index = self.__dict__.get("_answer_index")
        if index is None:
            index = {}
            for (dd, f), n in self._counts.items():
                if "=" in f:
                    e, v = f.split("=", 1)
                    index.setdefault((dd, e), {})[v] = n / self.pathologies[dd].patients
            self.__dict__["_answer_index"] = index
        return index.get((d, evidence), {})

    # ----------------------------------------------------------------------------- labels
    def label(self, kind: str, key: str) -> str:
        if kind == "evidence":
            return self.evidences[key].label if key in self.evidences else key
        if kind == "value":
            return self.values.get(key, key)
        if kind == "concept":
            return self.knowledge.concepts.get(key, {}).get("label", key)
        if kind == "pathology":
            return self.pathologies[key].name if key in self.pathologies else key
        if kind == "score":
            return self.knowledge.scores.get(key, {}).get("label", key)
        if kind == "recommendation":
            return self.knowledge.recommendations.get(key, {}).get("label", key)
        if kind == "value_set":
            return self.knowledge.value_sets[key][0] if key in self.knowledge.value_sets else key
        return key

    def feature_label(self, feature: str) -> str:
        if "=" in feature:
            e, v = feature.split("=", 1)
            return f"{self.label('evidence', e)}: {self.values.get(v, v + ('/10' if v.isdigit() else ''))}"
        return self.label("evidence", feature)

    def citation(self, key: str) -> str:
        return self.knowledge.sources.get(key, {}).get("citation", key)

    ORGANISATIONS = {"World Health Organization": "WHO", "National Institute for Health and Care Excellence": "NICE",
                     "Centers for Disease Control and Prevention": "CDC", "American Psychiatric Association": "APA (DSM-5)",
                     "Headache Classification Committee": "IHS (ICHD-3)", "HerniaSurge Group": "HerniaSurge"}

    def short_citation(self, key: str) -> str:
        """'Wells PS, Anderson DR, Rodger M, et al. (2000). ...' -> 'Wells et al. 2000'"""
        authors, _, rest = self.citation(key).partition(" (")
        year = rest[:4]
        for org, abbr in self.ORGANISATIONS.items():
            if authors.startswith(org):
                return f"{abbr} {year}"
        names = [a.strip() for a in authors.split(",") if a.strip()]

        def surname(name):   # 'Fansi Tchango A' -> 'Fansi Tchango', 'van Harten PN' -> 'van Harten'
            tokens = name.split()
            return " ".join(tokens[:-1]) if len(tokens) > 1 and tokens[-1].isupper() and len(tokens[-1]) <= 4 else tokens[0]
        surnames = [surname(n) for n in names if n != "et al."]
        if "et al." in names or len(surnames) > 2:
            return f"{surnames[0]} et al. {year}"
        return f"{' & '.join(surnames)} {year}"

    def source_link(self, key: str) -> str | None:
        s = self.knowledge.sources.get(key, {})
        return f"https://doi.org/{s['doi']}" if s.get("doi") else s.get("url")

    @cached_property
    def renderer(self) -> rb.Renderer:
        return rb.Renderer(evidence_label=lambda e: self.label("evidence", e),
                           value_label=lambda v: self.label("value", v),
                           concept_label=lambda c: self.label("concept", c),
                           pathology_label=lambda d: self.label("pathology", d),
                           score_label=lambda s: self.label("score", s),
                           value_set_label=lambda v: self.label("value_set", v),
                           rec_label=lambda r: self.label("recommendation", r))

    def rule_text(self, r: rb.Rule) -> str:
        return self.renderer.rule(r)
