"""Evaluate the optional free-text intake on DDXPlus demo patients (needs language-model keys in .env).

For one demo patient per pathology, a writer model that is not the reader (Gemini by default) writes how the patient
would describe the problem, in English and in Bahasa Indonesia, mentioning the main complaint and up to five other
findings of the record. The intake (Groq by default) then reads each description. Measured:

* extraction: precision of what was read against the patient's record, recall of the findings the description
  mentions, the main complaint, age and sex;
* consultation: it starts from the reading and answers the remaining questions from the record, as
  scripts/evaluate.py does; top-1 accuracy and number of questions, against the consultation that starts from the
  main complaint only, and against an oracle that reads the mentioned findings perfectly.

Results go to results/intake.json. Descriptions and readings are cached in results/intake_cache.json, so a run that
hits a free-tier limit (tokens per minute or per day) can simply be started again.

Usage (from the repository root):
    python -I scripts/evaluate_intake.py
    python -I scripts/evaluate_intake.py --patients 10 --writer gemini --reader-model qwen/qwen3.8-27b --pace 32
"""
import argparse
import json
import pathlib
import random
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from ddxes import intake, llm  # noqa: E402
from ddxes.case import Case  # noqa: E402
from ddxes.consult import Consultation  # noqa: E402
from ddxes.engine import InferenceEngine  # noqa: E402
from ddxes.kb import KnowledgeBase  # noqa: E402
from ddxes.paths import PROCESSED, RESULTS  # noqa: E402

CACHE = RESULTS / "intake_cache.json"
OUT = RESULTS / "intake.json"
LANGS = {"en": "English", "id": "Bahasa Indonesia"}
DETAIL_LIMITS = {"E_55": 2, "E_54": 1, "E_56": 1, "E_152": 2, "E_133": 1}   # follow-up answers a description mentions


# ------------------------------------------------------------------------------------------------ facts
def pick_facts(rec, kb, rng) -> list:
    """The main complaint and up to five other findings of the record, with a few follow-up answers."""
    full = Case.from_ddxplus(rec, kb)
    others = sorted(e for e in full.yes if e != rec["initial"] and not kb.evidences[e].parent)
    if TRAVEL_CODE in full.answers and full.answers[TRAVEL_CODE] != {kb.evidences[TRAVEL_CODE].default}:
        others.append(TRAVEL_CODE)
    chosen = [rec["initial"]] + rng.sample(others, min(5, len(others)))
    facts = []
    for e in chosen:
        if e == TRAVEL_CODE:
            facts.append((e, set(full.answers[e])))
            continue
        facts.append((e, True))
        for key, (child, kind) in intake.DETAILS.get(e, {}).items():
            limit = DETAIL_LIMITS.get(child)
            if not limit:
                continue
            if kind == "scale" and full.scales.get(child, 0) > 0:
                facts.append((child, full.scales[child]))
            elif kind in ("places", "choices") and full.answers.get(child):
                vals = sorted(v for v in full.answers[child] if v != kb.evidences[child].default)
                if vals:
                    facts.append((child, set(rng.sample(vals, min(limit, len(vals))))))
    return facts


TRAVEL_CODE = intake.TRAVEL


def keys(pairs) -> set:
    out = set()
    for e, v in pairs:
        if isinstance(v, bool):
            out.add(e if v else "!" + e)
        elif isinstance(v, int):
            out.add(f"{e}={v}")
        else:
            out |= {f"{e}={x}" for x in v}
    return out


def true_in_record(key: str, full: Case, kb) -> bool:
    if key.startswith("!"):
        return full.status(kb, key[1:]) is False
    e, _, v = key.partition("=")
    if not v:
        return full.status(kb, e) is True
    if kb.evidences[e].is_scale:
        return full.scales.get(e, int(kb.evidences[e].default or 0)) == int(v)
    return v in full.answers.get(e, set())


def plain(kb, e, v) -> str:
    q = kb.evidences[e].question
    if isinstance(v, bool):
        return f"{q} - {'yes' if v else 'no'}"
    if isinstance(v, int):
        return f"{q} - {v} out of 10"
    labels = [re.sub(r"\((R|L)\)$", lambda m: " (right)" if m.group(1) == "R" else " (left)", kb.label("value", x))
              for x in sorted(v)]
    return f"{q} - {', '.join(labels)}"


# ------------------------------------------------------------------------------------------------ models
def write(kb, rec, facts, writer) -> dict:
    sex = "woman" if rec["sex"] == "F" else "man"
    lines = "\n".join(f"- {plain(kb, e, v)}" for e, v in facts[1:]) or "- (none)"
    msgs = [{"role": "system", "content": "You write realistic patient descriptions to test a medical intake form. "
                                          'Answer with JSON: {"en": "...", "id": "..."}.'},
            {"role": "user", "content":
                "Write how this patient would describe the problem to a doctor: first person, everyday words, 2 to 4 "
                "sentences. Once in English (en) and once in natural Bahasa Indonesia (id, not a word-for-word "
                "translation). Mention the age and sex naturally, start with the main complaint, and mention every "
                "fact below and nothing else. Do not name a diagnosis and do not copy the questionnaire's wording.\n"
                f"Patient: {rec['age']}-year-old {sex}.\nMain complaint: {plain(kb, *facts[0])}\nOther facts:\n{lines}"}]
    last = None
    for provider in dict.fromkeys([writer, "openrouter", "gemini"]):   # free tiers are busy at times: retry, then fall back
        for attempt in range(3):
            try:
                reply = llm.chat_json(msgs, providers=[provider], max_tokens=900, temperature=0.4)
            except llm.LLMError as e:
                last = e
                time.sleep(15 * (attempt + 1))
                continue
            texts = {k: str(reply.data.get(k) or "").strip() for k in LANGS}
            if all(texts.values()):
                return {"texts": texts, "writer": f"{reply.provider} ({reply.model})"}
    raise llm.LLMError(f"no writer produced a description for {rec['id']} ({last})", [])


def read(kb, text, reader_model, pace, state) -> dict:
    for attempt in range(8):
        wait = state.get("last", 0) + pace - time.time()
        if wait > 0:
            time.sleep(wait)
        state["last"] = time.time()
        try:
            res = intake.read(kb, text, providers=["groq"], models=[reader_model])
        except llm.LLMError as e:
            if "429" in str(e) and "per day" not in str(e) and "TPD" not in str(e):
                print(f"    rate limit, waiting ({attempt + 1})", flush=True)
                time.sleep(30)
                continue
            raise
        enc = lambda v: v if isinstance(v, (bool, int)) else sorted(v)  # noqa: E731
        return {"age": res.age, "sex": res.sex, "cc": res.chief_complaint, "seconds": round(res.seconds, 2),
                "findings": [[f.evidence, enc(f.value)] for f in res.findings], "dropped": len(res.dropped),
                "reader": f"{res.provider} ({res.model})"}
    raise llm.LLMError("still rate-limited after several waits", [])


def decode(pairs) -> list:
    return [(e, v if isinstance(v, (bool, int)) else set(v)) for e, v in pairs]


# ------------------------------------------------------------------------------------------------ consultations
def consult(engine, rec, age, sex, cc, prefilled) -> dict:
    kb = engine.kb
    full = Case.from_ddxplus(rec, kb)
    cons = Consultation(engine, age=age, sex=sex, chief_complaint=cc, prefilled=prefilled)
    while (q := cons.next_question()) is not None:
        ev = kb.evidences[q.evidence]
        if ev.dtype == "B":
            ans = q.evidence in full.yes
        elif ev.is_scale:
            ans = full.scales.get(q.evidence, int(ev.default or 0))
        else:
            ans = full.answers.get(q.evidence, {ev.default})
        cons.answer(q.evidence, ans)
    ranking = cons.result.ranking
    return {"top1": ranking[:1] == [rec["pathology"]], "top3": rec["pathology"] in ranking[:3],
            "questions": len(cons.asked), "triage": cons.result.triage}


def mean(xs):
    xs = list(xs)
    return round(sum(xs) / len(xs), 4) if xs else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--patients", type=int, default=49, help="pathologies (one demo patient each)")
    ap.add_argument("--writer", default="gemini", choices=["gemini", "openrouter", "groq"])
    ap.add_argument("--reader-model", default="qwen/qwen3.8-27b")
    ap.add_argument("--pace", type=float, default=32.0, help="seconds between readings (Groq free tier: 8k tokens/min)")
    ap.add_argument("--from-cache", action="store_true",
                    help="only summarise the patients already in results/intake_cache.json (no model is called)")
    a = ap.parse_args()

    kb = KnowledgeBase.load()
    engine = InferenceEngine(kb)
    sample = json.loads((PROCESSED / "sample_patients.json").read_text(encoding="utf-8"))
    by_path = {}
    for rec in sorted(sample, key=lambda r: r["id"]):
        by_path.setdefault(rec["pathology"], rec)
    patients = [by_path[d] for d in sorted(by_path, key=lambda d: kb.pathologies[d].name)][:a.patients]
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    state = {}
    cases = []
    try:
        for i, rec in enumerate(patients, 1):
            pid = rec["id"]
            if a.from_cache and not all(lang in cache.get(pid, {}).get("readings", {}) for lang in LANGS):
                continue
            entry = cache.setdefault(pid, {})
            facts = pick_facts(rec, kb, random.Random(pid))
            if "texts" not in entry:
                entry.update(write(kb, rec, facts, a.writer))
                CACHE.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
            readings = entry.setdefault("readings", {})
            for lang in LANGS:
                if a.from_cache:
                    continue
                if lang not in readings or readings[lang].get("reader") != f"groq ({a.reader_model})":
                    readings[lang] = read(kb, entry["texts"][lang], a.reader_model, a.pace, state)
                    CACHE.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
            cases.append((rec, facts, entry))
            print(f"[{i}/{len(patients)}] {pid} {kb.pathologies[rec['pathology']].name}: "
                  + ", ".join(f"{lang} {len(entry['readings'][lang]['findings'])} findings" for lang in LANGS), flush=True)
    except llm.LLMError as e:
        print(f"stopped early ({e}); {len(cases)} patients done. Run the script again to continue.", flush=True)
    if not cases:
        return

    rows = []
    for rec, facts, entry in cases:
        full = Case.from_ddxplus(rec, kb)
        mentioned = keys(facts)
        base = consult(engine, rec, full.age, full.sex, rec["initial"], [])
        oracle = consult(engine, rec, full.age, full.sex, rec["initial"], [f for f in facts if f[0] != rec["initial"]])
        row = {"patient": rec["id"], "pathology": kb.pathologies[rec["pathology"]].name, "mentioned": sorted(mentioned),
               "texts": entry["texts"], "writer": entry["writer"], "baseline": base, "oracle": oracle}
        for lang in LANGS:
            r = entry["readings"][lang]
            read_pairs = decode(r["findings"])
            got = keys(read_pairs)
            cc = r["cc"] or rec["initial"]
            prefill = [(e, v) for e, v in read_pairs if e != cc]
            run = consult(engine, rec, r["age"] if r["age"] is not None else full.age, r["sex"] or full.sex, cc, prefill)
            row[lang] = {"read": sorted(got), "correct": sorted(k for k in got if true_in_record(k, full, kb)),
                         "found": sorted(got & mentioned), "cc": r["cc"] == rec["initial"],
                         "age": r["age"] == full.age, "sex": r["sex"] == full.sex, "seconds": r["seconds"],
                         "dropped": r["dropped"], "consultation": run, "reader": r["reader"]}
        rows.append(row)

    def summary(lang):
        rs = [row[lang] for row in rows]
        n_read, n_correct = sum(len(r["read"]) for r in rs), sum(len(r["correct"]) for r in rs)
        n_mentioned, n_found = sum(len(row["mentioned"]) for row in rows), sum(len(r["found"]) for r in rs)
        p = n_correct / n_read if n_read else None
        rc = n_found / n_mentioned if n_mentioned else None
        return {"descriptions": len(rs), "findings_mentioned": n_mentioned, "findings_read": n_read,
                "precision_vs_record": round(p, 4) if p is not None else None,
                "recall_of_mentioned": round(rc, 4) if rc is not None else None,
                "f1": round(2 * p * rc / (p + rc), 4) if p and rc else None,
                "main_complaint": mean(r["cc"] for r in rs), "age": mean(r["age"] for r in rs),
                "sex": mean(r["sex"] for r in rs), "seconds_per_reading": mean(r["seconds"] for r in rs),
                "dropped_per_reading": mean(r["dropped"] for r in rs),
                "top1": mean(r["consultation"]["top1"] for r in rs), "top3": mean(r["consultation"]["top3"] for r in rs),
                "questions": mean(r["consultation"]["questions"] for r in rs)}

    out = {
        "description": "Free-text intake on one DDXPlus demo patient per pathology: a writer model describes the "
                       "patient (main complaint + up to 5 findings), the intake reads it, the consultation continues "
                       "from the reading and answers the rest from the record.",
        "writer": sorted({row["writer"] for row in rows}), "reader": f"groq ({a.reader_model})",
        "patients": len(rows), "languages": {lang: summary(lang) for lang in LANGS},
        "baseline": {"top1": mean(r["baseline"]["top1"] for r in rows), "top3": mean(r["baseline"]["top3"] for r in rows),
                     "questions": mean(r["baseline"]["questions"] for r in rows)},
        "oracle": {"top1": mean(r["oracle"]["top1"] for r in rows), "top3": mean(r["oracle"]["top3"] for r in rows),
                   "questions": mean(r["oracle"]["questions"] for r in rows)},
        "cases": rows,
    }
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("writer", "reader", "patients", "languages", "baseline", "oracle")}, indent=1))


if __name__ == "__main__":
    main()
