"""Evaluation of the expert system on DDXPlus patients.

Metrics
* GTPA@k (top-k accuracy): the ground-truth pathology is among the k most probable entries;
* GTPA, DDR, DDP, DDF1: the DDXPlus differential metrics (Fansi Tchango et al., 2022, appendix H): inclusion of the
  ground truth in the predicted differential, and recall / precision / F1 of the predicted differential against
  the reference differential, both cut at probability > 0.01;
* triage: agreement with the level implied by the DDXPlus severity of the true pathology, under-triage (less
  urgent than that: the unsafe error) and over-triage;
* explanation coverage: how often at least one guideline rule supports the system's top diagnosis.
"""
from __future__ import annotations

import ast
import collections
import csv
import io
import random
import time
import zipfile

from .bayes import NaiveBayes, pool
from .case import Case
from .kb import SEVERITY_TRIAGE
from .paths import RAW
from .rulebase import TRIAGE_LEVELS, TRIAGE_RANK


def read_split(kb, name: str = "test", per_pathology: int | None = None, seed: int = 42, limit: int | None = None) -> list:
    """Patients of a DDXPlus split as records (the format of data/processed/sample_patients.json)."""
    name2id = {p.name: p.id for p in kb.pathologies.values()}
    with zipfile.ZipFile(RAW / f"release_{name}_patients.zip") as z, z.open(z.namelist()[0]) as f:
        rows = list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")))
    idx = list(range(len(rows)))
    if per_pathology:
        rng = random.Random(seed)
        by = collections.defaultdict(list)
        for i, r in enumerate(rows):
            by[r["PATHOLOGY"]].append(i)
        idx = sorted(i for k in sorted(by) for i in rng.sample(by[k], min(per_pathology, len(by[k]))))
    if limit:
        idx = idx[:limit]
    out = []
    for i in idx:
        r = rows[i]
        binary, findings = [], []
        for tok in ast.literal_eval(r["EVIDENCES"]):
            if "_@_" in tok:
                e, v = tok.split("_@_")
                findings.append((e, str(v)))
            else:
                binary.append(tok)
        out.append({"id": f"{name[0].upper()}{i:06d}", "age": int(r["AGE"]), "sex": r["SEX"], "pathology": name2id[r["PATHOLOGY"]],
                    "initial": r["INITIAL_EVIDENCE"], "binary": binary, "findings": findings,
                    "ddx": [(name2id[n], float(p)) for n, p in ast.literal_eval(r["DIFFERENTIAL_DIAGNOSIS"])]})
    return out


def differential_metrics(pred: dict, reference: list, truth: str, cut: float = 0.01) -> dict:
    p = {d for d, x in pred.items() if x > cut}
    g = {d for d, x in reference if x > cut}
    inter = len(p & g)
    ddr = inter / len(g) if g else 0.0
    ddp = inter / len(p) if p else 0.0
    return {"gtpa": truth in p, "ddr": ddr, "ddp": ddp, "ddf1": 2 * ddr * ddp / (ddr + ddp) if ddr + ddp else 0.0}


def triage_truth(kb, d: str) -> str:
    return SEVERITY_TRIAGE[kb.pathologies[d].severity]


def evaluate(engine, records, with_nb: bool = True, hybrid_weight: float = 0.5, progress: bool = False) -> dict:
    kb = engine.kb
    nb = NaiveBayes(kb) if with_nb else None
    rows = []
    t0 = time.time()
    for i, rec in enumerate(records):
        case = Case.from_ddxplus(rec, kb)
        res = engine.run(case)
        p_es = {h.pathology: h.probability for h in res.hypotheses if not h.excluded}
        row = {"id": rec["id"], "truth": rec["pathology"], "es_rank": res.ranking, "es_p": p_es, "triage": res.triage,
               "triage_truth": triage_truth(kb, rec["pathology"]), "rules_fired": len(res.trace),
               "guideline_dx": sorted({c.firing.rule.id for h in res.hypotheses[:1] for c in h.contributions if c.kind == "rule"}),
               "truth_rules": sorted({f.rule.id for f in res.trace for c, v in f.outputs
                                      if c.kind == "diagnosis" and c.target == rec["pathology"] and v > 0}),
               "n_candidates": len(p_es), "ddx": rec["ddx"]}
        if nb:
            p_nb = nb.posterior(case)
            row["nb_p"] = p_nb
            row["nb_rank"] = sorted(p_nb, key=lambda d: -p_nb[d])
            p_hy = pool(p_es, p_nb, hybrid_weight)
            row["hy_rank"] = sorted(p_hy, key=lambda d: -p_hy[d])
            row["hy_p"] = p_hy
        rows.append(row)
        if progress and (i + 1) % 5000 == 0:
            print(f"  {i + 1:,}/{len(records):,} patients, {time.time() - t0:.0f} s", flush=True)
    elapsed = time.time() - t0
    return summarise(kb, rows, elapsed)


def summarise(kb, rows, elapsed: float) -> dict:
    n = len(rows)
    out = {"patients": n, "ms_per_patient": 1000 * elapsed / n if n else 0}
    systems = [("es", "es_rank", "es_p")] + ([("nb", "nb_rank", "nb_p"), ("hybrid", "hy_rank", "hy_p")] if rows and "nb_rank" in rows[0] else [])
    for name, rk, pk in systems:
        m = collections.Counter()
        for r in rows:
            for k in (1, 3, 5):
                m[f"top{k}"] += r["truth"] in r[rk][:k]
            dm = differential_metrics(r[pk], r["ddx"], r["truth"])
            for key, v in dm.items():
                m[key] += v
        out[name] = {k: v / n for k, v in m.items()}
    # triage
    tri = collections.Counter()
    matrix = {t: {u: 0 for u in TRIAGE_LEVELS} for t in TRIAGE_LEVELS}
    for r in rows:
        a, b = TRIAGE_RANK[r["triage"]], TRIAGE_RANK[r["triage_truth"]]
        tri["exact"] += a == b
        tri["under"] += a > b
        tri["over"] += a < b
        matrix[r["triage_truth"]][r["triage"]] += 1
        if b <= 2:
            tri["urgent_cases"] += 1
            tri["urgent_detected"] += a <= 2
        else:
            tri["nonurgent_cases"] += 1
            tri["nonurgent_kept"] += a >= 3
        if b == 1:
            tri["emergency_cases"] += 1
            tri["emergency_detected"] += a == 1
    out["triage"] = {"exact": tri["exact"] / n, "under": tri["under"] / n, "over": tri["over"] / n,
                     "urgent_recall": tri["urgent_detected"] / tri["urgent_cases"] if tri["urgent_cases"] else None,
                     "urgent_specificity": tri["nonurgent_kept"] / tri["nonurgent_cases"] if tri["nonurgent_cases"] else None,
                     "binary_accuracy": (tri["urgent_detected"] + tri["nonurgent_kept"]) / n,
                     "emergency_recall": tri["emergency_detected"] / tri["emergency_cases"] if tri["emergency_cases"] else None,
                     "matrix": matrix}
    # explanations
    top_ok = [r for r in rows if r["es_rank"][:1] == [r["truth"]]]
    out["explanation"] = {
        "rules_fired_mean": sum(r["rules_fired"] for r in rows) / n,
        "top1_supported_by_guideline_rule": sum(bool(r["guideline_dx"]) for r in rows) / n,
        "truth_supported_by_guideline_rule": sum(bool(r["truth_rules"]) for r in rows) / n,
        "candidates_after_covering_mean": sum(r["n_candidates"] for r in rows) / n,
        "single_candidate_share": sum(r["n_candidates"] == 1 for r in rows) / n,
        "correct_top1_with_guideline_support": sum(bool(r["guideline_dx"]) for r in top_ok) / len(top_ok) if top_ok else 0}
    # per pathology and confusions
    per = collections.defaultdict(lambda: [0, 0, 0])
    conf = collections.Counter()
    for r in rows:
        per[r["truth"]][1] += 1
        if r["es_rank"][:1] == [r["truth"]]:
            per[r["truth"]][0] += 1
        elif r["es_rank"]:
            conf[(r["truth"], r["es_rank"][0])] += 1
        if r["truth_rules"]:
            per[r["truth"]][2] += 1
    out["per_pathology"] = {d: {"n": v[1], "top1": v[0] / v[1], "guideline_support": v[2] / v[1]} for d, v in sorted(per.items())}
    out["confusions"] = [{"truth": a, "predicted": b, "patients": c} for (a, b), c in conf.most_common(15)]
    return out


def validate_rules(engine, records) -> list:
    """Empirical validation of every rule: how often it fires and, for diagnostic and triage rules, its precision."""
    kb = engine.kb
    n_by = collections.Counter(r["pathology"] for r in records)
    fires = collections.Counter()
    hits = collections.Counter()
    for rec in records:
        case = Case.from_ddxplus(rec, kb)
        res = engine.run(case)
        truth = rec["pathology"]
        truth_level = TRIAGE_RANK[triage_truth(kb, truth)]
        for f in res.trace:
            fires[f.rule.id] += 1
            for c, v in f.outputs:
                if c.kind == "diagnosis":
                    hits[f.rule.id] += (truth == c.target) if v > 0 else (truth != c.target)
                elif c.kind == "triage":
                    hits[f.rule.id] += truth_level <= TRIAGE_RANK[c.target]
    n = len(records)
    out = []
    for r in kb.rules:
        row = {"id": r.id, "name": r.name, "category": r.category, "cf": r.cf, "source": r.source, "fidelity": r.fidelity,
               "fires": fires[r.id], "support": fires[r.id] / n}
        if r.category == "diagnostic" and fires[r.id]:
            target = next(c.target for c in r.consequents if c.kind == "diagnosis")
            prior = n_by[target] / n
            prec = hits[r.id] / fires[r.id]
            row.update(target=target, precision=prec, recall=(hits[r.id] / n_by[target]) if r.cf > 0 and n_by[target] else None,
                       lift=(prec / prior) if r.cf > 0 and prior else None)
        elif r.category == "triage" and fires[r.id]:
            row.update(precision=hits[r.id] / fires[r.id])
        out.append(row)
    return out
