"""Interactive consultation: goal-driven questioning with a WHY facility.

The consultation starts, like the DDXPlus platform, from age, sex and the chief complaint, and then asks one
question at a time. The question is chosen by backward chaining from the current goals:

* goals = the leading hypotheses of the differential, plus every severe (severity 1-2) hypothesis that is still
  plausible -- the "rule out the dangerous diagnoses" phase of systems such as INTERNIST-I (Miller et al., 1982)
  and of the DXA engine that generated DDXPlus;
* for each goal, the engine looks at the rules that conclude it: guideline rules whose premise is still UNKNOWN
  (their missing conditions) and the knowledge-base links of the goal that have not been asked;
* each candidate question is scored by the expected information gain over the differential, weighted up when a
  guideline rule needs it or when it serves a severe goal. A sub-question (e.g. pain location) is replaced by
  its parent question (pain?) until the parent is answered;
* once the leading hypothesis is confident enough, a rule-out phase asks, for every severe (severity 1-2)
  hypothesis that is still possible, its most specific unasked question (highest likelihood ratio in the expert
  profile) before the consultation may end.

After every answer the whole inference runs again (forward chaining), so beliefs, triage and red flags are
always current. `prefilled` takes answers known before the first question (the free-text intake of ddxes.intake);
they are never asked again. `simulate()` replays a DDXPlus patient by answering from his record.

The consultation is deterministic: the knowledge base keeps evidences and profiles in sets, so every choice here
walks them in sorted order and breaks ties explicitly. The same answers therefore give the same questions, and the
same WHY text, in every run (Python randomises the iteration order of string sets from one process to the next).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import rulebase as rb
from .case import Case


@dataclass
class Question:
    evidence: str
    text: str
    dtype: str                    # "B", "C", "M" or "scale"
    options: list                 # [(value code, label)] for categorical / multi-choice questions
    goals: list                   # pathologies whose rules need the answer
    rules: list                   # guideline rule ids that need it
    gain: float
    why: str
    parent_of: str | None = None  # asked first because this sub-question needs it
    phase: str = "discriminate"   # "discriminate" (information gain) or "rule-out" (severe hypothesis)


@dataclass
class Consultation:
    engine: object
    age: int | None = None
    sex: str | None = None
    chief_complaint: str | None = None
    max_questions: int = 30
    confidence: float = 0.95
    min_gain: float = 0.005
    rule_out: bool = True             # run the rule-out phase for severe hypotheses before stopping
    rule_out_min: float = 1e-4        # a severe hypothesis below this probability counts as excluded
    rule_out_per_pathology: int = 2
    prefilled: list = field(default_factory=list)   # [(evidence, value)] already known, e.g. from ddxes.intake
    case: Case = field(init=False)
    asked: list = field(default_factory=list)
    result: object = field(init=False)
    _rule_out_asked: dict = field(default_factory=dict)

    def __post_init__(self):
        kb = self.engine.kb
        self.case = Case(id="consultation", age=self.age, sex=self.sex, closed_world=False, initial=self.chief_complaint)
        if self.chief_complaint:
            self.case.answer(kb, self.chief_complaint, True)
        for e, v in self.prefilled:                  # answers given before the first question: never asked again
            self.case.answer(kb, e, v)
        self.result = self.engine.run(self.case)

    # ----------------------------------------------------------------------------- answering
    def answer(self, evidence: str, value) -> None:
        self.case.answer(self.engine.kb, evidence, value)
        self.asked.append((evidence, value))
        self.result = self.engine.run(self.case)

    def confident(self) -> bool:
        diff = self.result.differential()
        return bool(diff) and diff[0].probability >= self.confidence

    def done(self) -> bool:
        return self.next_question() is None

    # ----------------------------------------------------------------------------- rule-out phase
    def _diagnostic_power(self, d: str, e: str) -> float:
        """Largest likelihood ratio any answer to evidence e has for pathology d."""
        kb = self.engine.kb
        if kb.evidences[e].dtype == "B":
            return kb.strength(d, e).lr
        values = kb.answer_distribution(d, e)
        return max((kb.strength(d, f"{e}={v}").lr for v in values), default=1.0)

    def _rule_out_question(self) -> Question | None:
        """Before stopping, ask the most specific unasked question of every plausible severe hypothesis.

        The phase that INTERNIST-I and the DXA engine behind DDXPlus run on high-risk diagnoses: a consultation must
        not end while a can't-miss diagnosis is still possible and one answer could confirm or exclude it.
        """
        kb, case = self.engine.kb, self.case
        diff = self.result.differential(0.0)
        top = diff[0].pathology if diff else None
        for h in diff:
            p = kb.pathologies[h.pathology]
            if h.pathology == top or p.severity > 2 or h.probability < self.rule_out_min:
                continue
            if self._rule_out_asked.get(h.pathology, 0) >= self.rule_out_per_pathology:
                continue
            best = None
            for e in sorted(p.profile):                       # sorted: equal powers resolve the same way every run
                if case.asked(e) or case.status(kb, e) is not None:
                    continue
                ask = kb.evidences[e].parent if kb.evidences[e].parent and not case.asked(kb.evidences[e].parent) else e
                power = self._diagnostic_power(h.pathology, e)
                if best is None or power > best[1]:
                    best = (ask, power, e)
            if best is None:
                continue
            ask, power, target = best
            self._rule_out_asked[h.pathology] = self._rule_out_asked.get(h.pathology, 0) + 1
            q = self._make_question(ask, {"goals": {h.pathology}, "rules": set(), "pairs": set(),
                                          "children": {target} if ask != target else set()}, 0.0)
            lr = ">1000" if power >= 1000 else f"{power:.0f}"
            q.why = (f"Before concluding {kb.pathologies[top].name} (P = {diff[0].probability:.2f}) I must rule out "
                     f"{p.name}, a severity-{p.severity} condition that is still possible (P = {h.probability:.3f}). "
                     f"In the expert knowledge base, '{kb.label('evidence', target)}' is its most specific sign "
                     f"(likelihood ratio {lr}).")
            q.phase = "rule-out"
            return q
        return None

    # ----------------------------------------------------------------------------- question selection
    def _goals(self, k: int = 5) -> list:
        kb = self.engine.kb
        cands = [h for h in self.result.hypotheses if not h.excluded]
        goals = cands[:k]
        goals += [h for h in cands[k:] if kb.pathologies[h.pathology].severity <= 2 and h.probability >= 0.02]
        return goals

    def _candidates(self, goals) -> dict:
        """evidence -> {"goals": set, "rules": set}: backward chaining from the goals to askable evidences.

        The evidences are added in a fixed order (rules in rule-base order, then each goal's profile, both sorted),
        and the first of equally good candidates wins in _discriminating_question.
        """
        kb, case, res = self.engine.kb, self.case, self.result
        out = {}

        def add(e, goal=None, rule=None):
            if e in ("AGE", "SEX") or e not in kb.evidences:
                return
            ev = kb.evidences[e]
            child = None
            if ev.parent and not case.asked(ev.parent):          # ask the parent question first
                child, e = e, ev.parent
            elif case.asked(e) or case.status(kb, e) is not None:
                return
            slot = out.setdefault(e, {"goals": set(), "rules": set(), "pairs": set(), "children": set()})
            if child:
                slot["children"].add(child)
            if goal:
                slot["goals"].add(goal)
            if rule:
                slot["rules"].add(rule)
                slot["pairs"].add((rule, goal))

        order = [h.pathology for h in goals]                 # most probable first
        for r in kb.rules:
            ev = res.rule_evals.get(r.id)
            if ev is None or ev.status is not None:
                continue
            targets = {c.target for c in r.consequents if c.kind == "diagnosis" and r.consequent_cf(c) > 0}
            red_flag = r.category == "triage" and not any(isinstance(a, rb.BeliefAtLeast) for a in rb.atoms(r.premise))
            goal = next((d for d in order if d in targets), None)   # a rule with several targets serves the likeliest
            if goal or red_flag:
                for e in sorted(ev.missing):
                    add(e, goal=goal, rule=r.id)
        for h in goals:
            for e in sorted(kb.pathologies[h.pathology].profile):
                add(e, goal=h.pathology)
        return out

    def _answer_model(self, d: str, e: str) -> dict:
        """Distribution of the answer to evidence e for a patient with pathology d."""
        kb = self.engine.kb
        ev = kb.evidences[e]
        if ev.dtype == "B":
            p = kb.p_present(d, e)
            return {True: p, False: 1 - p}
        if e not in kb.pathologies[d].profile:
            return {ev.default: 1.0}
        dist = kb.answer_distribution(d, e)
        total = sum(dist.values())
        if total <= 0:
            return {ev.default: 1.0}
        return {v: p / total for v, p in dist.items()}

    def _gain(self, e: str, hyps) -> float:
        probs = [h.probability for h in hyps]
        z = sum(probs)
        if z <= 0:
            return 0.0
        probs = [p / z for p in probs]
        h0 = -sum(p * math.log2(p) for p in probs if p > 0)
        joint = {}
        for h, p in zip(hyps, probs):
            for a, q in self._answer_model(h.pathology, e).items():
                joint.setdefault(a, []).append(p * q)
        h1 = 0.0
        for parts in joint.values():
            pa = sum(parts)
            if pa > 0:
                h1 += -sum(x * math.log2(x / pa) for x in parts if x > 0)
        return max(0.0, h0 - h1)

    def next_question(self) -> Question | None:
        if len(self.asked) >= self.max_questions:
            return None
        if not self.confident():
            q = self._discriminating_question()
            if q is not None:
                return q
        return self._rule_out_question() if self.rule_out else None

    def _discriminating_question(self) -> Question | None:
        kb = self.engine.kb
        goals = self._goals()
        hyps = [h for h in self.result.hypotheses if not h.excluded][:12]
        best, best_score = None, 0.0
        for e, slot in self._candidates(goals).items():
            gain = self._gain(e, hyps)
            severe = any(kb.pathologies[g].severity <= 2 for g in slot["goals"])
            score = gain * (1.25 if slot["rules"] else 1.0) * (1.25 if severe else 1.0)
            if score > best_score:
                best, best_score = (e, slot, gain), score
        if best is None or best[2] < self.min_gain:
            return None
        e, slot, gain = best
        return self._make_question(e, slot, gain)

    def _make_question(self, e: str, slot: dict, gain: float) -> Question:
        kb = self.engine.kb
        ev = kb.evidences[e]
        dtype = "scale" if ev.is_scale else ev.dtype
        options = [] if dtype in ("B", "scale") else [(v, kb.label("value", v)) for v in ev.values]
        prob = {h.pathology: h.probability for h in self.result.hypotheses if not h.excluded}
        goals = sorted(slot["goals"], key=lambda d: (-prob.get(d, 0), d))

        def strength(rid, goal):                    # the certainty factor the rule gives the goal
            r = kb.rule_by_id[rid]
            return max((r.consequent_cf(c) for c in r.consequents if c.target == goal), default=0.0)

        parts = []
        # the rule named is the one serving the most probable goal; among rules for the same goal (D-PE-1 and D-PE-2
        # both read the Wells score), the one with the larger certainty factor, then the lower identifier
        pairs = sorted(slot["pairs"], key=lambda rg: (-prob.get(rg[1], -1) if rg[1] else 1, -strength(*rg), rg[0]))
        if pairs:                                   # a guideline rule needs the answer
            rid, goal = pairs[0]
            r = kb.rule_by_id[rid]
            if goal:
                p = kb.pathologies[goal]
                parts.append(f"I am trying to confirm or rule out {p.name} (P = {prob.get(goal, 0):.2f}, severity {p.severity}).")
            else:
                parts.append("I am checking for a red flag.")
            parts.append(f"Rule {r.id} ({kb.short_citation(r.source)}) needs this answer: {r.name}.")
        elif goals:                                 # a knowledge-base link of a goal needs it
            g0 = goals[0]
            p = kb.pathologies[g0]
            parts.append(f"I am trying to confirm or rule out {p.name} (P = {prob.get(g0, 0):.2f}, severity {p.severity}).")
            if ev.dtype == "B":
                parts.append(f"In the expert knowledge base this question belongs to the profile of {p.name} "
                             f"(present in {kb.p_present(g0, e):.0%} of its patients).")
            else:
                parts.append(f"In the expert knowledge base this question belongs to the profile of {p.name}.")
        if slot.get("children"):
            parts.append("It opens the follow-up questions " + ", ".join(kb.label("evidence", c) for c in sorted(slot["children"])) + ".")
        parts.append(f"Expected information gain: {gain:.2f} bits.")
        return Question(evidence=e, text=ev.question, dtype=dtype, options=options, goals=goals, rules=sorted(slot["rules"]),
                        gain=gain, why=" ".join(parts))


def simulate(engine, record: dict, **kwargs) -> dict:
    """Run a consultation on a DDXPlus patient, answering each question from the patient's record."""
    kb = engine.kb
    full = Case.from_ddxplus(record, kb)
    cons = Consultation(engine, age=full.age, sex=full.sex, chief_complaint=record.get("initial"), **kwargs)
    while (q := cons.next_question()) is not None:
        ev = kb.evidences[q.evidence]
        if ev.dtype == "B":
            ans = q.evidence in full.yes
        elif ev.is_scale:
            ans = full.scales.get(q.evidence, int(ev.default or 0))
        else:
            ans = full.answers.get(q.evidence, {ev.default})
        cons.answer(q.evidence, ans)
    res = cons.result
    ranking = res.ranking
    positives_total = len(full.present(kb))
    positives_found = len(cons.case.present(kb))
    return {"id": record["id"], "truth": record["pathology"], "questions": len(cons.asked), "ranking": ranking[:5],
            "top1": ranking[:1] == [record["pathology"]], "top3": record["pathology"] in ranking[:3],
            "top5": record["pathology"] in ranking[:5], "triage": res.triage,
            "probability_top": res.differential()[0].probability if res.differential() else 0.0,
            "evidence_recall": positives_found / positives_total if positives_total else 1.0}
