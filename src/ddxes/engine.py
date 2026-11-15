"""The inference engine of DDX-ES.

One run over a case has four phases; every step is recorded for the explanation facility.

1. Forward chaining (data-driven) over the abstraction, scoring and diagnostic rules. The rules are stratified
   by dependency: a rule is placed after every rule that can conclude a concept or score it reads, so within a
   stratum the conflict set is complete when it is computed. Conflict resolution orders the agenda by salience,
   then specificity (more conditions first), then position in the rule base; refraction lets a rule fire once.
   Conditions are evaluated in three-valued logic (true / false / unknown) so the same engine serves complete
   DDXPlus records (closed world) and interactive consultations (open world, unknown = not asked yet).
2. Abduction by parsimonious set covering (Reggia et al., 1983): a pathology stays a candidate only if its
   expert-curated profile explains every symptom reported; if no pathology explains all of them, the ones that
   leave the fewest symptoms unexplained are kept.
3. Belief aggregation: for every candidate the certainty factors of the fired diagnostic rules and of the
   knowledge-base evidence rules (rule schema: IF feature f of the profile of d is present/absent THEN d with
   CF = Heckerman(lr)) are combined with the MYCIN parallel-combination function -- evaluated as a sum of log
   likelihood ratios, which is the same function under Heckerman's interpretation but does not saturate at
   CF = 1; with the prior from the KG this gives posterior odds, normalised over the candidates into the
   differential diagnosis.
4. Triage and recommendation rules, which may read the beliefs. The final triage level is the most urgent one
   proposed with enough certainty (safety first): by a red-flag rule, or by the severity schema for every
   hypothesis whose probability reaches the threshold.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import groupby

from . import rulebase as rb
from .certainty import THRESHOLD, cf_from_lr, combine, lr_from_cf
from .kb import SEVERITY_TRIAGE


@dataclass(slots=True, frozen=True)
class Fact:
    """A fact a premise was matched against: kind in evidence | answer | scale | age | sex | concept | score | belief | not."""
    kind: str
    key: object
    value: object = None
    cf: float = 1.0


@dataclass(slots=True)
class Eval:
    status: object               # True | False | None (unknown)
    cf: float = 0.0
    used: tuple = ()
    missing: frozenset = frozenset()   # askable evidences ("AGE", "SEX" for demographics) that would decide it


@dataclass
class Firing:
    step: int
    rule: rb.Rule
    stratum: int
    premise_cf: float
    used: tuple                  # Facts that satisfied the premise
    outputs: tuple               # ((Consequent, contributed CF or points), ...)


@dataclass
class Contribution:
    kind: str                    # "rule" | "kb" (present feature) | "kb-absent" | "covering"
    cf: float
    firing: Firing | None = None
    strength: object = None      # kb.Strength for the knowledge-base evidence schema
    evidence: str | None = None  # for "covering"


@dataclass
class Hypothesis:
    pathology: str
    prior: float
    cf: float = 0.0
    log_lr: float = 0.0          # weight of evidence: sum of the log likelihood ratios of all contributions
    probability: float = 0.0
    rank: int = 0
    excluded: bool = False
    unexplained: tuple = ()
    contributions: list = field(default_factory=list)


@dataclass
class TriageReason:
    level: str
    cf: float
    firing: Firing | None = None
    pathology: str | None = None  # set when the severity schema proposed the level


@dataclass
class Result:
    case: object
    hypotheses: list
    concepts: dict
    concept_firings: dict
    scores: dict
    score_firings: dict
    triage: str
    triage_reasons: list
    triage_candidates: list
    recommendations: list        # (recommendation, cf, Firing)
    trace: list
    rule_evals: dict
    rule_contributions: dict     # pathology -> [Contribution] (also for excluded pathologies)
    settings: dict

    def hypothesis(self, d: str) -> Hypothesis:
        return next(h for h in self.hypotheses if h.pathology == d)

    @property
    def ranking(self) -> list:
        return [h.pathology for h in self.hypotheses if not h.excluded]

    def top(self, k: int = 1) -> list:
        return self.ranking[:k]

    def differential(self, min_probability: float = 0.01) -> list:
        return [h for h in self.hypotheses if not h.excluded and h.probability >= min_probability]

    def fired(self, rule_id: str) -> bool:
        return any(f.rule.id == rule_id for f in self.trace)


class _WorkingMemory:
    def __init__(self, engine, case):
        self.engine, self.case = engine, case
        self.evals = {}
        self.concepts, self.concept_firings = {}, defaultdict(list)
        self.scores, self.score_firings = defaultdict(float), defaultdict(list)
        self.rule_contribs = defaultdict(list)
        self.triage, self.recs, self.trace = [], [], []
        self.prob = {}

    def concept_eval(self, name: str) -> Eval:
        cf = self.concepts.get(name)
        if cf is not None and cf >= THRESHOLD:
            return Eval(True, cf, (Fact("concept", name, True, cf),))
        missing, unknown = set(), False
        for r in self.engine.producers.get(("concept", name), ()):
            ev = self.evals.get(r.id)
            if ev is None or ev.status is None:
                unknown = True
                if ev is not None:
                    missing |= ev.missing
        if unknown:
            return Eval(None, 0.0, (), frozenset(missing))
        return Eval(False, 0.0, (Fact("concept", name, False, 0.0),))

    def score_eval(self, c: rb.ScoreCmp) -> Eval:
        total, pending, missing = self.scores.get(c.score, 0.0), 0.0, set()
        for r in self.engine.producers.get(("score", c.score), ()):
            ev = self.evals.get(r.id)
            if ev is None or ev.status is None:
                pending += sum(k.points for k in r.consequents if k.kind == "score" and k.target == c.score)
                if ev is not None:
                    missing |= ev.missing
        op, t = rb.OPS[c.op], c.threshold
        if c.op in ("ge", "gt"):
            status = True if op(total, t) else (False if not op(total + pending, t) else None)
        elif c.op in ("le", "lt"):
            status = True if op(total + pending, t) else (False if not op(total, t) else None)
        else:
            status = op(total, t) if pending == 0 else None
        if status is None:
            return Eval(None, 0.0, (), frozenset(missing))
        return Eval(status, 1.0 if status else 0.0, (Fact("score", c.score, total),))


class InferenceEngine:
    DEFAULTS = dict(covering="strict", use_rules=True, use_kb_evidence=True, use_absent=True, use_prior=True,
                    antecedent_covering=False, severity_threshold=0.2, triage_cf=0.5, use_triage_rules=True)

    def __init__(self, kb, **settings):
        unknown = set(settings) - set(self.DEFAULTS)
        if unknown:
            raise TypeError(f"unknown settings {unknown}")
        self.kb = kb
        self.settings = {**self.DEFAULTS, **settings}
        self.producers = defaultdict(list)
        for r in kb.rules:
            for c in r.consequents:
                if c.kind in ("concept", "score"):
                    self.producers[(c.kind, c.target)].append(r)
        self.stratum = {}
        for r in kb.rules:
            self._stratum_of(r, set())
        post = [r for r in kb.rules if r.category in ("triage", "recommendation")]
        pre = [r for r in kb.rules if r.category not in ("triage", "recommendation")]
        offset = max((self.stratum[r.id] for r in pre), default=0) + 2   # +1 is the belief-aggregation phase
        for r in post:
            self.stratum[r.id] += offset
        self.aggregation_stratum = offset - 1
        key = lambda r: (self.stratum[r.id], -r.salience, -r.specificity, r.index)  # noqa: E731  conflict resolution
        self.pre_strata = [(s, list(rs)) for s, rs in groupby(sorted(pre, key=key), key=lambda r: self.stratum[r.id])]
        self.post_strata = [(s, list(rs)) for s, rs in groupby(sorted(post, key=key), key=lambda r: self.stratum[r.id])]

    def _stratum_of(self, r: rb.Rule, stack: set) -> int:
        if r.id in self.stratum:
            return self.stratum[r.id]
        if r.id in stack:
            raise ValueError(f"cyclic dependency in the rule base through {r.id}")
        stack.add(r.id)
        deps = []
        for a in rb.atoms(r.premise):
            if isinstance(a, rb.ConceptHolds):
                deps += self.producers.get(("concept", a.concept), [])
            elif isinstance(a, rb.ScoreCmp):
                deps += self.producers.get(("score", a.score), [])
        s = 1 + max((self._stratum_of(d, stack) for d in deps), default=-1)
        stack.discard(r.id)
        self.stratum[r.id] = s
        return s

    # ----------------------------------------------------------------------------- condition evaluation
    def evaluate(self, c, wm: _WorkingMemory) -> Eval:
        case, kb, t = wm.case, self.kb, type(c)
        if t is rb.Has:
            s = case.status(kb, c.evidence)
            if s is None:
                return Eval(None, 0.0, (), frozenset((c.evidence,)))
            return Eval(s, 1.0 if s else 0.0, (Fact("evidence", c.evidence, s),))
        if t is rb.AllOf:
            used, missing, cf, unknown = [], set(), 1.0, False
            for x in c.items:
                r = self.evaluate(x, wm)
                if r.status is False:
                    return Eval(False, 0.0, r.used)
                if r.status is None:
                    unknown = True
                    missing |= r.missing
                else:
                    used.extend(r.used)
                    cf = min(cf, r.cf)
            if unknown:
                return Eval(None, 0.0, tuple(used), frozenset(missing))
            return Eval(True, cf, tuple(used))
        if t is rb.AnyOf:
            best, used, missing, unknown = None, [], set(), False
            for x in c.items:
                r = self.evaluate(x, wm)
                if r.status is True:
                    used.extend(r.used)
                    best = r.cf if best is None else max(best, r.cf)
                elif r.status is None:
                    unknown = True
                    missing |= r.missing
            if best is not None:
                return Eval(True, best, tuple(used))
            if unknown:
                return Eval(None, 0.0, (), frozenset(missing))
            return Eval(False)
        if t is rb.ConceptHolds:
            return wm.concept_eval(c.concept)
        if t is rb.AnswerIn:
            s, hits = case.answer_in(kb, c.evidence, c.values)
            if s is None:
                return Eval(None, 0.0, (), frozenset((c.evidence,)))
            return Eval(s, 1.0 if s else 0.0, (Fact("answer", c.evidence, tuple(sorted(hits))),) if s else ())
        if t is rb.AtLeast:
            trues, missing, n_unknown = [], set(), 0
            for x in c.items:
                r = self.evaluate(x, wm)
                if r.status is True:
                    trues.append(r)
                elif r.status is None:
                    n_unknown += 1
                    missing |= r.missing
            used = tuple(f for r in trues for f in r.used)
            if len(trues) >= c.n:
                return Eval(True, sorted((r.cf for r in trues), reverse=True)[c.n - 1], used)
            if len(trues) + n_unknown >= c.n:
                return Eval(None, 0.0, used, frozenset(missing))
            return Eval(False, 0.0, used)
        if t is rb.Not:
            r = self.evaluate(c.item, wm)
            if r.status is None:
                return Eval(None, 0.0, (), r.missing)
            if r.status is True:
                return Eval(False, 0.0, r.used)
            return Eval(True, 1.0, (Fact("not", c.item),))
        if t is rb.Scale:
            v = case.scale(kb, c.evidence)
            if v is None:
                return Eval(None, 0.0, (), frozenset((c.evidence,)))
            if v is False:
                return Eval(False)
            ok = rb.OPS[c.op](v, c.threshold)
            return Eval(ok, 1.0 if ok else 0.0, (Fact("scale", c.evidence, v),))
        if t is rb.Age:
            if case.age is None:
                return Eval(None, 0.0, (), frozenset(("AGE",)))
            ok = rb.OPS[c.op](case.age, c.threshold)
            return Eval(ok, 1.0 if ok else 0.0, (Fact("age", "age", case.age),))
        if t is rb.Sex:
            if case.sex is None:
                return Eval(None, 0.0, (), frozenset(("SEX",)))
            ok = case.sex == c.sex
            return Eval(ok, 1.0 if ok else 0.0, (Fact("sex", "sex", case.sex),))
        if t is rb.ScoreCmp:
            return wm.score_eval(c)
        if t is rb.BeliefAtLeast:
            p = wm.prob.get(c.pathology, 0.0)
            ok = p >= c.threshold
            return Eval(ok, 1.0 if ok else 0.0, (Fact("belief", c.pathology, p),))
        raise TypeError(c)

    # ----------------------------------------------------------------------------- the recognise-act cycle
    def _forward(self, strata, wm, step: int) -> int:
        for stratum, rules in strata:
            conflict_set = []
            for r in rules:                                   # match
                ev = self.evaluate(r.premise, wm)
                wm.evals[r.id] = ev
                if ev.status is True and ev.cf >= THRESHOLD:
                    conflict_set.append((r, ev))
            for r, ev in conflict_set:                        # select (already in resolution order) and act
                step += 1
                self._fire(r, ev, wm, step, stratum)
        return step

    def _fire(self, r: rb.Rule, ev: Eval, wm: _WorkingMemory, step: int, stratum: int) -> None:
        outputs = tuple((c, c.points) if c.kind == "score" else (c, r.consequent_cf(c) * ev.cf) for c in r.consequents)
        firing = Firing(step, r, stratum, ev.cf, ev.used, outputs)
        wm.trace.append(firing)
        for c, val in outputs:
            if c.kind == "concept":
                wm.concepts[c.target] = combine(wm.concepts.get(c.target, 0.0), val)
                wm.concept_firings[c.target].append(firing)
            elif c.kind == "score":
                wm.scores[c.target] += val
                wm.score_firings[c.target].append(firing)
            elif c.kind == "diagnosis":
                wm.rule_contribs[c.target].append(Contribution("rule", val, firing=firing))
            elif c.kind == "triage":
                wm.triage.append(TriageReason(c.target, val, firing=firing))
            elif c.kind == "recommend":
                wm.recs.append((c.target, val, firing))

    # ----------------------------------------------------------------------------- abduction + aggregation
    def _aggregate(self, wm: _WorkingMemory) -> list:
        kb, case, st = self.kb, wm.case, self.settings
        manifestations = case.present(kb) if st["antecedent_covering"] else case.present_symptoms(kb)
        unexplained = {d: tuple(sorted(manifestations - p.profile)) for d, p in kb.pathologies.items()}
        if st["covering"] == "strict":
            fewest = min(len(u) for u in unexplained.values())
            candidates = [d for d in kb.pathologies if len(unexplained[d]) == fewest]
        else:
            candidates = list(kb.pathologies)
        cand_set = set(candidates)
        features = case.features(kb) if st["use_kb_evidence"] else []
        hyps = {}
        for d, p in kb.pathologies.items():
            h = hyps[d] = Hypothesis(d, p.prior, unexplained=unexplained[d], excluded=d not in cand_set)
            if h.excluded:
                continue
            contribs = list(wm.rule_contribs.get(d, ())) if st["use_rules"] else []
            if features:
                profile = p.profile
                for f, e in features:
                    if e in profile:
                        s = kb.strength(d, f)
                        contribs.append(Contribution("kb", s.cf, strength=s))
                if st["use_absent"]:
                    for e in kb.binary_profile[d]:
                        if case.status(kb, e) is False:
                            s = kb.absent_strength(d, e)
                            contribs.append(Contribution("kb-absent", s.cf, strength=s))
            if st["covering"] == "soft":
                contribs += [Contribution("covering", -0.9, evidence=e) for e in unexplained[d]]
            h.contributions = contribs
            # MYCIN's parallel combination, evaluated in the log-likelihood-ratio domain: under Heckerman's mapping
            # combine(a, b) corresponds exactly to lr_a * lr_b, so summing log lr gives the same result without the
            # saturation of CFs at +-1 that would make strongly supported hypotheses indistinguishable.
            h.log_lr = sum(math.log(c.strength.lr) if c.strength is not None else math.log(lr_from_cf(c.cf)) for c in contribs)
            h.cf = cf_from_lr(math.exp(max(-700.0, min(700.0, h.log_lr))))
        uniform = 1 / len(kb.pathologies)
        log_odds = {}
        for d in candidates:
            prior = kb.pathologies[d].prior if st["use_prior"] else uniform
            log_odds[d] = math.log(prior / (1 - prior)) + hyps[d].log_lr
        top = max(log_odds.values())
        z = sum(math.exp(v - top) for v in log_odds.values())
        for d in candidates:
            hyps[d].probability = math.exp(log_odds[d] - top) / z
        ranked = sorted(candidates, key=lambda d: (-hyps[d].probability, d))
        ranked += sorted((d for d in kb.pathologies if d not in cand_set), key=lambda d: (len(unexplained[d]), d))
        for i, d in enumerate(ranked, 1):
            hyps[d].rank = i
        wm.prob = {d: hyps[d].probability for d in candidates}
        return [hyps[d] for d in ranked]

    # ----------------------------------------------------------------------------- one consultation run
    def run(self, case) -> Result:
        wm = _WorkingMemory(self, case)
        step = self._forward(self.pre_strata, wm, 0)
        hypotheses = self._aggregate(wm)
        self._forward(self.post_strata, wm, step)

        st = self.settings
        candidates = [t for t in wm.triage if t.cf >= st["triage_cf"]] if st["use_triage_rules"] else []
        for h in hypotheses:
            if not h.excluded and h.probability >= st["severity_threshold"]:
                candidates.append(TriageReason(SEVERITY_TRIAGE[self.kb.pathologies[h.pathology].severity], h.probability,
                                               pathology=h.pathology))
        if candidates:
            level = min(candidates, key=lambda t: rb.TRIAGE_RANK[t.level]).level
        else:
            level = "NonUrgent"
        reasons = sorted((t for t in candidates if t.level == level), key=lambda t: -t.cf)

        recs = {}
        for rec, cf, firing in wm.recs:
            if rec not in recs or cf > recs[rec][1]:
                recs[rec] = (rec, cf, firing)
        return Result(case=case, hypotheses=hypotheses, concepts=dict(wm.concepts), concept_firings=dict(wm.concept_firings),
                      scores=dict(wm.scores), score_firings=dict(wm.score_firings), triage=level, triage_reasons=reasons,
                      triage_candidates=sorted(candidates, key=lambda t: (rb.TRIAGE_RANK[t.level], -t.cf)),
                      recommendations=sorted(recs.values(), key=lambda x: -x[1]), trace=wm.trace, rule_evals=wm.evals,
                      rule_contributions=dict(wm.rule_contribs), settings=dict(st))
