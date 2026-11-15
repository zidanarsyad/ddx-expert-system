"""A case: the facts about one patient that the inference engine reasons over (its working-memory input).

Two reading modes, which the report contrasts with the open-world semantics of RDF/OWL:
* closed world (a complete DDXPlus record): every question was asked, so an evidence that is not listed is
  false (negation as failure);
* open world (an interactive consultation): an evidence that has not been asked yet is unknown. Conditions on
  it evaluate to UNKNOWN, and the engine collects them as candidate questions (backward chaining).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Case:
    id: str = "case"
    age: int | None = None
    sex: str | None = None                       # "F" | "M"
    yes: set = field(default_factory=set)        # yes/no evidences answered yes
    no: set = field(default_factory=set)         # yes/no evidences answered no
    answers: dict = field(default_factory=dict)  # categorical / multi-choice evidence -> set of value codes
    scales: dict = field(default_factory=dict)   # 0-10 scale evidence -> int
    closed_world: bool = False
    initial: str | None = None                   # chief complaint (DDXPlus initial evidence)
    truth: str | None = None                     # ground-truth pathology, for evaluation only
    ddx: list = field(default_factory=list)      # DDXPlus reference differential [(pathology, probability)]

    # ----------------------------------------------------------------------------- construction
    @classmethod
    def from_ddxplus(cls, rec: dict, kb) -> "Case":
        """rec: one entry of data/processed/sample_patients.json (or a parsed test-split row)."""
        case = cls(id=rec["id"], age=int(rec["age"]), sex=rec["sex"], yes=set(rec["binary"]), closed_world=True,
                   initial=rec.get("initial"), truth=rec.get("pathology"), ddx=[tuple(x) for x in rec.get("ddx", [])])
        for e, v in rec["findings"]:
            v = str(v)
            if kb.evidences[e].is_scale:
                case.scales[e] = int(v)
            else:
                case.answers.setdefault(e, set()).add(v)
        return case

    def copy(self) -> "Case":
        return Case(id=self.id, age=self.age, sex=self.sex, yes=set(self.yes), no=set(self.no),
                    answers={k: set(v) for k, v in self.answers.items()}, scales=dict(self.scales),
                    closed_world=self.closed_world, initial=self.initial, truth=self.truth, ddx=list(self.ddx))

    def to_record(self) -> dict:
        findings = [(e, v) for e, vs in sorted(self.answers.items()) for v in sorted(vs)]
        findings += [(e, str(v)) for e, v in sorted(self.scales.items())]
        return {"id": self.id, "age": self.age, "sex": self.sex, "binary": sorted(self.yes), "findings": findings,
                "initial": self.initial, "pathology": self.truth, "ddx": self.ddx}

    # ----------------------------------------------------------------------------- answering (consultation)
    def answer(self, kb, evidence: str, value) -> None:
        """Record an answer: True/False for a yes/no question, a set of value codes, or an int for a scale."""
        ev = kb.evidences[evidence]
        if ev.dtype == "B":
            (self.yes if value else self.no).add(evidence)
            (self.no if value else self.yes).discard(evidence)
        elif ev.is_scale:
            self.scales[evidence] = int(value)
        else:
            vals = {value} if isinstance(value, str) else set(value)
            self.answers[evidence] = vals or {ev.default}

    def asked(self, evidence: str) -> bool:
        return evidence in self.yes or evidence in self.no or evidence in self.answers or evidence in self.scales

    # ----------------------------------------------------------------------------- three-valued status
    def _parent_false(self, kb, evidence: str) -> bool:
        parent = kb.evidences[evidence].parent
        return bool(parent) and self.status(kb, parent) is False

    def status(self, kb, evidence: str):
        """True / False / None (unknown) for 'the evidence is present'."""
        ev = kb.evidences[evidence]
        if ev.dtype == "B":
            if evidence in self.yes:
                return True
            if evidence in self.no:
                return False
        elif ev.is_scale:
            if evidence in self.scales:
                return self.scales[evidence] != int(ev.default or 0)
        elif evidence in self.answers:
            return any(v != ev.default for v in self.answers[evidence])
        if self.closed_world or self._parent_false(kb, evidence):
            return False
        return None

    def answer_in(self, kb, evidence: str, values: frozenset):
        """(status, matching values) for 'one of the answers lies in values'."""
        if evidence in self.answers:
            hits = self.answers[evidence] & values
            return bool(hits), hits
        if self.closed_world or self._parent_false(kb, evidence):
            return False, set()
        return None, set()

    def scale(self, kb, evidence: str):
        """The scale answer, False if it can no longer be given (closed world / parent false), None if unknown."""
        if evidence in self.scales:
            return self.scales[evidence]
        if self.closed_world or self._parent_false(kb, evidence):
            return False
        return None

    # ----------------------------------------------------------------------------- views used by the engine
    def features(self, kb) -> list:
        """(feature, evidence) pairs: 'E_91' for a yes answer, 'E_55=V_29' / 'E_56=7' for other answers."""
        out = [(e, e) for e in self.yes]
        out += [(f"{e}={v}", e) for e, vs in self.answers.items() for v in vs]
        out += [(f"{e}={v}", e) for e, v in self.scales.items()]
        return out

    def present(self, kb) -> set:
        return {e for e in set(self.yes) | set(self.answers) | set(self.scales) if self.status(kb, e)}

    def present_symptoms(self, kb) -> set:
        return {e for e in self.present(kb) if kb.evidences[e].kind == "symptom"}
