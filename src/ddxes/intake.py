"""Free-text intake: a language model reads the patient's own words, the expert system does the reasoning.

The patient (or a clinician) describes the problem in English or Bahasa Indonesia. One call to a language model
(ddxes.llm: Groq first) maps the description onto the questionnaire of the knowledge base, that is, onto the
questions the description already answers. The model only translates words into evidence codes:

* it may use only the evidence codes, body parts and answer values of the knowledge base, listed in its prompt;
* every finding must quote the words of the description it comes from; a finding whose quote is not in the
  description is dropped, so the model cannot add a symptom the patient did not mention;
* follow-up answers (where the pain is, what it feels like, how strong it is; where the swelling or the rash is;
  where the patient travelled) are turned into answer codes here, in Python, and only under a present parent;
* the findings are shown for confirmation, and the consultation then asks only what the description left open.

Diagnosis, triage and explanation stay with the rules and the knowledge graph. This module is optional: without a
key in .env the rest of DDX-ES works exactly as before.
"""
from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field

from . import llm

MAX_CHARS = 1500
TRAVEL = "E_204"
# parent evidence -> detail key -> (child evidence, kind of answer)
DETAILS = {
    "E_53": {"where": ("E_55", "places"), "radiates_to": ("E_57", "places"), "character": ("E_54", "choices"),
             "intensity": ("E_56", "scale"), "onset": ("E_59", "scale")},
    "E_151": {"where": ("E_152", "places")},
    "E_129": {"where": ("E_133", "places"), "colour": ("E_130", "choice"), "itching": ("E_136", "scale"),
              "pain": ("E_134", "scale"), "swollen": ("E_132", "scale"), "peels": ("E_131", "yes/no"),
              "larger_than_1cm": ("E_135", "yes/no")},
}

INSTRUCTIONS = """You map a patient's own description of a health problem onto the answers of a fixed medical \
questionnaire. The description may be in English, Bahasa Indonesia or another language. Answer with one JSON object.

Rules
1. Use only the codes, body parts and values listed below. Record only what the description states: never add a
   finding because it is likely, and never name a diagnosis.
2. Each finding needs "quote": the exact words of the description that state it, copied verbatim in the original
   language (a short phrase).
3. "present": true when the patient has it; false when the patient says they do not (e.g. "no fever", "tidak demam").
4. History and risk factors (codes marked A) count too: recent surgery, cancer, medicines, smoking, contacts, travel.
5. Pain related to the complaint is E53, with details when stated: "where" and "radiates_to" (lists of
   {"part": a BODY PART, "side": "R", "L", "both" or ""}), "character" (a list of PAIN CHARACTER words),
   "intensity" (0-10) and "onset" (0-10, how suddenly it started; 10 = all at once).
6. Swelling is E151 with "where". Skin lesions or a rash is E129 with, when stated, "where", "colour" (a RASH
   COLOUR), "itching", "pain" and "swollen" (0-10), "peels" and "larger_than_1cm" (true or false).
   Give a detail only when the description states it; leave it out otherwise (never guess a colour or a number).
7. Travel abroad in the last 4 weeks is E204 with "region" (a TRAVEL REGION); present false if the patient has not
   travelled.
8. "chief_complaint": the code of the main symptom the patient consults for (a present code marked S), or null.
9. "age" in years and "sex" ("F" or "M") when stated or clear (e.g. ibu, wanita: F; bapak, pria: M), else null.

Output format:
{"age": null, "sex": null, "chief_complaint": "E..",
 "findings": [{"code": "E..", "present": true, "quote": "..."},
              {"code": "E..", "present": false, "quote": "..."},
              {"code": "E151", "present": true, "quote": "...", "where": [{"part": "...", "side": "R"}]}]}"""


@dataclass
class Finding:
    evidence: str
    value: object               # True / False, a set of value codes, or an int (0-10 scale)
    quote: str                  # the words of the description it comes from
    question: str = ""
    answer: str = ""
    parent: str | None = None   # for a follow-up answer: the finding it details


@dataclass
class Dropped:
    what: str
    quote: str
    reason: str


@dataclass
class Intake:
    text: str
    age: int | None = None
    sex: str | None = None
    chief_complaint: str | None = None
    findings: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    provider: str = ""
    model: str = ""
    seconds: float = 0.0

    def prefill(self) -> list:
        """(evidence, value) for Consultation(prefilled=...): every finding but the chief complaint, parents first."""
        return [(f.evidence, f.value) for f in self.findings if f.evidence != self.chief_complaint]


# ------------------------------------------------------------------------------------------------ vocabulary
def _key(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s)).lower()
    s = re.sub(r"\b(the|a|an)\b", " ", s)
    return " ".join(re.findall(r"[^\W_]+", s))


# words of the questionnaire's phrasing that carry no meaning for the mapping (they cost tokens on every call)
_FILLER = {"your", "you", "are", "do", "does", "did", "any", "that", "which", "who", "a", "an", "the", "is", "been",
           "there", "have", "has", "currently", "experiencing"}


def compact(label: str) -> str:
    """'Experiencing shortness of breath or difficulty breathing in a significant way' ->
    'shortness of breath or difficulty breathing in significant way'"""
    words = [w for w in label.split() if w.lower().strip(",()") not in _FILLER]
    while words and words[0].lower() in ("of", "or", "in"):
        words = words[1:]
    return " ".join(words) or label


class _Vocabulary:
    """The prompt's code list and the lookup tables that turn the model's words back into answer codes."""

    def __init__(self, kb):
        evs = sorted((e for e in kb.evidences.values() if not e.parent), key=lambda e: int(e.code[2:]))
        lines = []
        for e in evs:
            label = e.label if not e.label.startswith(("Or ", "(")) else e.question.rstrip("?")
            lines.append(f"E{e.code[2:]} {'S' if e.kind == 'symptom' else 'A'} {compact(label)}")
        self.places, self.parts = {}, []
        loc = kb.evidences["E_55"]
        for v in loc.values:
            if v == loc.default:
                continue
            label = kb.label("value", v)
            m = re.match(r"^(.*?)\s*\((R|L)\)$", label)
            part, side = (m.group(1), m.group(2)) if m else (label, "")
            k = _key(part)
            if k not in self.places:
                self.places[k] = {}
                self.parts.append(part)
            self.places[k][side] = v
        self.choices = {}
        for code in ("E_54", "E_130", TRAVEL):
            ev = kb.evidences[code]
            self.choices[code] = {_key(kb.label("value", v)): v for v in ev.values
                                  if v != ev.default and kb.label("value", v) not in ("NA", "N")}
        self.yes_no = {}
        for code in ("E_131", "E_135"):
            ev = kb.evidences[code]
            self.yes_no[code] = {kb.label("value", v): v for v in ev.values}
        words = lambda code: ", ".join(kb.label("value", v) for v in self.choices[code].values())  # noqa: E731
        self.prompt = (INSTRUCTIONS
                       + "\n\nQUESTIONNAIRE (code, S = symptom or A = history and risk factors, meaning):\n"
                       + "\n".join(lines)
                       + "\n\nBODY PARTS: " + ", ".join(sorted(self.parts))
                       + "\nPAIN CHARACTER: " + words("E_54")
                       + "\nRASH COLOUR: " + words("E_130")
                       + "\nTRAVEL REGIONS: " + words(TRAVEL))

    def place(self, part: str, side: str) -> set | None:
        k = _key(part).replace("lower lip", "bottom lip")
        sides = self.places.get(k) or self.places.get(k[:-1] if k.endswith("s") else k)
        if not sides:                           # 'lips' -> upper lip + bottom lip: every part named by these words
            words = set((k[:-1] if k.endswith("s") else k).split())
            hits = [p for p in self.places if words and words <= set(p.split())]
            if not 0 < len(hits) <= 3:
                return None
            return set().union(*(self.place(h, side) for h in hits))
        side = str(side or "").strip().upper()[:1] if str(side or "").lower() != "both" else "B"
        if side in ("R", "L"):
            return {sides.get(side) or sides.get("") or next(iter(sides.values()))}
        if "" in sides:
            return {sides[""]}
        return set(sides.values())               # "both", or a side the description does not give

    def choice(self, code: str, word) -> str | None:
        table, k = self.choices[code], _key(word)
        if k in table:
            return table[k]
        hits = {v for label, v in table.items() if k and (k in label or label in k)}
        return hits.pop() if len(hits) == 1 else None


_VOCAB = {}


def vocabulary(kb) -> _Vocabulary:
    if id(kb) not in _VOCAB:
        _VOCAB[id(kb)] = _Vocabulary(kb)
    return _VOCAB[id(kb)]


def messages(kb, text: str) -> list:
    return [{"role": "system", "content": vocabulary(kb).prompt},
            {"role": "user", "content": "Description:\n" + text}]


# ------------------------------------------------------------------------------------------------ validation
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s)).lower()
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(s.split())


def _words(s: str) -> list:
    return re.findall(r"[^\W_]+", s)


def _grounded(quote: str, text: str, words: set) -> bool:
    """The quote is in the description, verbatim or word for word (words of three letters or more)."""
    q = _norm(quote).strip(" .,;:!?\"'()")
    if len(q) < 2:
        return False
    if q in text:
        return True
    ws = [w for w in _words(q) if len(w) >= 3]
    return bool(ws) and all(w in words for w in ws)


def _code(x) -> str:
    m = re.match(r"^\s*[Ee]_?(\d+)\s*$", str(x or ""))
    return f"E_{m.group(1)}" if m else str(x or "").strip()


def _bool(x):
    if isinstance(x, bool):
        return x
    s = str(x).strip().lower()
    return True if s in ("true", "yes", "1", "y") else False if s in ("false", "no", "0", "n") else None


def _scale(x):
    try:
        v = round(float(x))
    except (TypeError, ValueError):
        return None
    return v if 0 <= v <= 10 else None


def _answer_text(kb, evidence: str, value) -> str:
    ev = kb.evidences[evidence]
    if ev.dtype == "B":
        return "Yes" if value else "No"
    if ev.is_scale:
        return f"{value} of 10"
    return ", ".join(kb.label("value", v) for v in sorted(value))


def parse(kb, text: str, data: dict) -> Intake:
    """Validate the model's JSON against the knowledge base and the description; keep only grounded findings."""
    voc = vocabulary(kb)
    norm = _norm(text)
    words = set(_words(norm))
    out = Intake(text=text)
    found, seen, contradictory = {}, {}, set()

    def add(evidence, value, quote, parent=None):
        found[evidence] = Finding(evidence, value, quote, kb.evidences[evidence].question,
                                  _answer_text(kb, evidence, value), parent)

    for item in data.get("findings") or []:
        if not isinstance(item, dict):
            continue
        code, quote = _code(item.get("code")), str(item.get("quote") or "").strip()
        ev = kb.evidences.get(code)
        if ev is None:
            out.dropped.append(Dropped(str(item.get("code")), quote, "not a question of the questionnaire"))
            continue
        if ev.parent:
            out.dropped.append(Dropped(code, quote, "a follow-up question, answered through its parent"))
            continue
        if not _grounded(quote, norm, words):
            out.dropped.append(Dropped(code, quote, "these words are not in the description"))
            continue
        present = _bool(item.get("present", True))
        if present is None:
            out.dropped.append(Dropped(code, quote, "present must be true or false"))
            continue
        if code in seen:
            if seen[code] != present:
                contradictory.add(code)
            continue
        seen[code] = present
        if code == TRAVEL:
            if not present:
                add(code, {ev.default}, quote)
            elif (region := voc.choice(TRAVEL, item.get("region") or "")) is not None:
                add(code, {region}, quote)
            else:
                out.dropped.append(Dropped(code, quote, "travel without a region the questionnaire knows"))
            continue
        if ev.dtype != "B":
            out.dropped.append(Dropped(code, quote, "not a yes/no question"))
            continue
        add(code, present, quote)
        if not present:
            continue
        for key, (child, kind) in DETAILS.get(code, {}).items():
            raw = item.get(key)
            if raw in (None, "", []) or child not in kb.evidences:
                continue
            value = None
            if kind == "places":
                codes = set()
                for p in raw if isinstance(raw, list) else [raw]:
                    part, side = (p.get("part"), p.get("side")) if isinstance(p, dict) else (p, "")
                    hit = voc.place(str(part or ""), side)
                    if hit:
                        codes |= hit
                    else:
                        out.dropped.append(Dropped(f"{code} {key}", str(part), "not a body part of the questionnaire"))
                value = codes or None
            elif kind == "choices":
                codes = {voc.choice(child, w) for w in (raw if isinstance(raw, list) else [raw])}
                value = {c for c in codes if c} or None
            elif kind == "choice":
                c = voc.choice(child, raw)
                value = {c} if c else None
            elif kind == "scale":                   # a number must be in the description, not guessed from "itchy"
                value = _scale(raw)
                if value is not None and not re.search(rf"(?<!\d)(?<!\d[.,]){value}(?!\d)(?![.,]\d)", text):
                    out.dropped.append(Dropped(f"{code} {key}", str(raw), "the description gives no such number"))
                    value = None
            elif kind == "yes/no":
                b = _bool(raw)
                value = None if b is None else {voc.yes_no[child]["Y" if b else "N"]}
            if value is not None:
                add(child, value, quote, parent=code)

    for code in contradictory:                       # said both ways: leave the question to the consultation
        found.pop(code, None)
        for child in [c for c, f in found.items() if f.parent == code]:
            found.pop(child)
        out.dropped.append(Dropped(code, "", "the description says it both ways"))
    out.findings = list(found.values())

    try:
        age = round(float(data.get("age")))
        out.age = age if 0 <= age <= 110 else None
    except (TypeError, ValueError):
        out.age = None
    sex = str(data.get("sex") or "").strip().lower()
    out.sex = "F" if sex in ("f", "female", "woman") else "M" if sex in ("m", "male", "man") else None

    def is_complaint(c):
        f, ev = found.get(c), kb.evidences.get(c)
        return f is not None and f.value is True and ev.dtype == "B" and ev.kind == "symptom"
    cc = _code(data.get("chief_complaint"))
    if not is_complaint(cc):                         # else the first present symptom of the description
        firsts = sorted((norm.find(_norm(f.quote)) % (len(norm) + 1), i, f.evidence)
                        for i, f in enumerate(out.findings) if is_complaint(f.evidence))
        cc = firsts[0][2] if firsts else None
    out.chief_complaint = cc
    return out


def read(kb, text: str, *, providers=None, models=None, chat=None) -> Intake:
    """Ask the language model to read a description; raises llm.LLMError when no provider answers."""
    text = " ".join(str(text or "").split())[:MAX_CHARS]
    if not text:
        raise ValueError("the description is empty")
    t0 = time.perf_counter()
    kw = {"providers": providers, "max_tokens": 1000}
    if models:
        kw["models"] = models
    reply = (chat or llm.chat_json)(messages(kb, text), **kw)
    out = parse(kb, text, reply.data)
    out.provider, out.model, out.seconds = reply.provider, reply.model, time.perf_counter() - t0
    return out
