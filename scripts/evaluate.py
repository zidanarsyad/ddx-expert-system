"""Step 3: evaluate the expert system and write results/*.json.

Experiments
  main         full test split (134,529 patients): expert system vs naive Bayes vs hybrid       (~12 min)
  ablation     stratified test sample, 100 patients per pathology: which part of the engine does what
  triage       sensitivity of triage to the severity threshold and to the red-flag rules
  rules        empirical validation of every rule on a stratified validation sample (200 per pathology)
  consult      interactive consultations simulated on the 980 demo patients
Run all:   python -I scripts/evaluate.py
Run some:  python -I scripts/evaluate.py ablation triage consult
Quick:     python -I scripts/evaluate.py --quick      (main experiment on the 980 demo patients only)
"""
import json
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from ddxes.consult import simulate  # noqa: E402
from ddxes.engine import InferenceEngine  # noqa: E402
from ddxes.evaluate import evaluate, read_split, validate_rules  # noqa: E402
from ddxes.kb import KnowledgeBase  # noqa: E402
from ddxes.paths import PROCESSED, RESULTS  # noqa: E402

ABLATIONS = [
    ("Guideline rules only", dict(covering="off", use_kb_evidence=False)),
    ("Set covering only", dict(use_rules=False, use_kb_evidence=False)),
    ("Guideline rules + covering", dict(use_kb_evidence=False)),
    ("KB evidence rules only", dict(covering="off", use_rules=False)),
    ("KB evidence + covering", dict(use_rules=False)),
    ("Full, without covering", dict(covering="off")),
    ("Full, covering incl. antecedents", dict(antecedent_covering=True)),
    ("Full, no absent-evidence rules", dict(use_absent=False)),
    ("Full, uniform prior", dict(use_prior=False)),
    ("Full expert system", dict()),
]


def dump(name, obj):
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / name).write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")
    print(f"wrote results/{name}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    quick = "--quick" in sys.argv
    todo = args or ["main", "ablation", "triage", "rules", "consult"]
    t0 = time.time()
    kb = KnowledgeBase.load()
    engine = InferenceEngine(kb)
    sample = json.loads((PROCESSED / "sample_patients.json").read_text(encoding="utf-8"))

    if "main" in todo:
        records = sample if quick else read_split(kb, "test")
        print(f"main: {len(records):,} test patients")
        res = evaluate(engine, records, progress=True)
        res["split"] = "demo sample (980)" if quick else "full test split"
        dump("main_quick.json" if quick else "main.json", res)
        print(json.dumps({k: res[k] for k in ("es", "nb", "hybrid", "triage")}, indent=1, default=str)[:1500])

    strat = None
    if "ablation" in todo or "triage" in todo:
        strat = read_split(kb, "test", per_pathology=100, seed=7)
    if "ablation" in todo:
        rows = []
        for name, settings in ABLATIONS:
            r = evaluate(InferenceEngine(kb, **settings), strat, with_nb=False)
            rows.append({"name": name, "settings": settings, **r["es"], "triage": {k: r["triage"][k] for k in ("exact", "under", "over")},
                         "ms": r["ms_per_patient"], "candidates": r["explanation"]["candidates_after_covering_mean"]})
            print(f"  {name:36s} top1 {r['es']['top1']:.3f} top3 {r['es']['top3']:.3f} ddf1 {r['es']['ddf1']:.3f}")
        dump("ablation.json", {"patients": len(strat), "rows": rows})
    if "triage" in todo:
        rows = []
        for red in (True, False):
            for thr in (0.05, 0.1, 0.2, 0.3, 0.5, 1.01):
                r = evaluate(InferenceEngine(kb, severity_threshold=thr, use_triage_rules=red), strat, with_nb=False)
                rows.append({"red_flag_rules": red, "severity_threshold": thr, **{k: r["triage"][k] for k in
                             ("exact", "under", "over", "urgent_recall", "urgent_specificity", "binary_accuracy", "emergency_recall")}})
                print(f"  red flags {red!s:5s} threshold {thr:4.2f}: exact {r['triage']['exact']:.3f} under {r['triage']['under']:.3f} over {r['triage']['over']:.3f}")
        dump("triage_sensitivity.json", {"patients": len(strat), "rows": rows})
    if "rules" in todo:
        val = read_split(kb, "validate", per_pathology=200, seed=11)
        rows = validate_rules(engine, val)
        dump("rule_validation.json", {"patients": len(val), "rows": rows})
        dx = [r for r in rows if r["category"] == "diagnostic" and r.get("precision") is not None and r["cf"] > 0]
        print(f"  {len(dx)} positive diagnostic rules fired; median precision {statistics.median(r['precision'] for r in dx):.2f}, "
              f"median lift {statistics.median(r['lift'] for r in dx):.1f}; never fired: {sum(1 for r in rows if r['fires'] == 0)}")
    if "consult" in todo:
        from ddxes.evaluate import triage_truth
        from ddxes.rulebase import TRIAGE_RANK
        out = {}
        for name, rule_out in (("without_rule_out", False), ("summary", True)):
            sims = [simulate(engine, rec, rule_out=rule_out) for rec in sample]
            n = len(sims)
            urgent = [s for s in sims if TRIAGE_RANK[triage_truth(kb, s["truth"])] <= 2]
            out[name] = {"patients": n, "questions_mean": sum(s["questions"] for s in sims) / n,
                         "questions_median": statistics.median(s["questions"] for s in sims),
                         "top1": sum(s["top1"] for s in sims) / n, "top3": sum(s["top3"] for s in sims) / n,
                         "top5": sum(s["top5"] for s in sims) / n,
                         "evidence_recall": sum(s["evidence_recall"] for s in sims) / n,
                         "under_triage": sum(TRIAGE_RANK[s["triage"]] > TRIAGE_RANK[triage_truth(kb, s["truth"])] for s in sims) / n,
                         "urgent_missed": sum(TRIAGE_RANK[s["triage"]] > 2 for s in urgent) / len(urgent)}
            if rule_out:
                out["rows"] = sims
            print(f"  {name}: " + json.dumps(out[name]))
        dump("consultation.json", out)
    if "early" in todo:
        early_triage(kb, sample)
    if "curve" in todo:
        rows = []
        for conf in (0.8, 0.9, 0.95, 0.99, 0.999):
            sims = [simulate(engine, rec, confidence=conf) for rec in sample]
            n = len(sims)
            rows.append({"confidence": conf, "questions_mean": sum(s["questions"] for s in sims) / n,
                         "top1": sum(s["top1"] for s in sims) / n, "top3": sum(s["top3"] for s in sims) / n,
                         "evidence_recall": sum(s["evidence_recall"] for s in sims) / n})
            print(f"  stop at P >= {conf}: {rows[-1]['questions_mean']:.2f} questions, top-1 {rows[-1]['top1']:.3f}, "
                  f"top-3 {rows[-1]['top3']:.3f}")
        dump("consultation_curve.json", {"patients": len(sample), "rows": rows})
    print(f"done in {time.time() - t0:.0f} s")


def early_triage(kb, sample):
    """Triage safety during a consultation: under-triage after k questions, with and without red-flag rules."""
    from ddxes.case import Case
    from ddxes.consult import Consultation
    from ddxes.evaluate import triage_truth
    from ddxes.rulebase import TRIAGE_RANK
    checkpoints = (0, 1, 3, 5, 30)
    out = []
    for red in (True, False):
        engine = InferenceEngine(kb, use_triage_rules=red)
        under = {k: 0 for k in checkpoints}
        urgent_missed = {k: 0 for k in checkpoints}
        n_urgent = 0
        for rec in sample:
            full = Case.from_ddxplus(rec, kb)
            truth = TRIAGE_RANK[triage_truth(kb, rec["pathology"])]
            n_urgent += truth <= 2
            cons = Consultation(engine, age=full.age, sex=full.sex, chief_complaint=rec["initial"])
            asked = 0
            for k in checkpoints:
                while asked < k and (q := cons.next_question()) is not None:
                    ev = kb.evidences[q.evidence]
                    ans = (q.evidence in full.yes) if ev.dtype == "B" else full.scales.get(q.evidence, int(ev.default or 0)) \
                        if ev.is_scale else full.answers.get(q.evidence, {ev.default})
                    cons.answer(q.evidence, ans)
                    asked += 1
                level = TRIAGE_RANK[cons.result.triage]
                under[k] += level > truth
                urgent_missed[k] += truth <= 2 and level > 2
        n = len(sample)
        for k in checkpoints:
            out.append({"red_flag_rules": red, "questions": k, "under": under[k] / n,
                        "urgent_missed": urgent_missed[k] / n_urgent})
            print(f"  red flags {red!s:5s} after {k:2d} questions: under-triage {under[k] / n:.3f}, "
                  f"urgent cases missed {urgent_missed[k] / n_urgent:.3f}")
    dump("early_triage.json", {"patients": len(sample), "rows": out})


if __name__ == "__main__":
    main()
