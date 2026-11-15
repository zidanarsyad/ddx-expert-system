"""Step 1 of the pipeline: DDXPlus raw files -> data/processed/*.json.

Source: Fansi Tchango, A. et al. (2022) DDXPlus Dataset, figshare, doi:10.6084/m9.figshare.20043374 (CC BY 4.0)

What it does
  1. aligns the French-coded release_conditions.json with the English evidence codes (E_xx) of the English
     release, through the French question text that both releases share (223/223 codes match);
  2. keeps the expert knowledge base: 49 pathologies, their ICD-10 chapter, severity, and the
     pathology -> symptom / antecedent links that the DDXPlus physicians curated;
  3. samples PER_PATHOLOGY test patients per pathology (fixed seed) as the demo and quick-evaluation case base;
  4. counts, on the whole validation split, how often each feature occurs with each pathology. A feature is a
     yes/no evidence (E_91) or one answer of a categorical / multi-choice evidence (E_55=V_29). These counts
     calibrate the certainty factors of the knowledge-base rules (see src/ddxes/kb.py);
  5. checks the result and writes data/processed/*.json and validation_report.md.

The test split is never used to build anything: it is only sampled for demo cases and read by the evaluation.

Usage (from the repository root):  python -I scripts/prepare_data.py      (about 2 minutes)
"""
import ast
import collections
import csv
import io
import json
import pathlib
import random
import re
import unicodedata
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW, OUT = ROOT / "data" / "raw", ROOT / "data" / "processed"
SEED, PER_PATHOLOGY = 42, 20

# WHO ICD-10 (2019) chapters: (first code, last code, number, title)
ICD10_CHAPTERS = [
    ("A00", "B99", "I", "Certain infectious and parasitic diseases"),
    ("C00", "D48", "II", "Neoplasms"),
    ("D50", "D89", "III", "Diseases of the blood and immune mechanism"),
    ("E00", "E90", "IV", "Endocrine, nutritional and metabolic diseases"),
    ("F00", "F99", "V", "Mental and behavioural disorders"),
    ("G00", "G99", "VI", "Diseases of the nervous system"),
    ("H00", "H59", "VII", "Diseases of the eye and adnexa"),
    ("H60", "H95", "VIII", "Diseases of the ear and mastoid process"),
    ("I00", "I99", "IX", "Diseases of the circulatory system"),
    ("J00", "J99", "X", "Diseases of the respiratory system"),
    ("K00", "K93", "XI", "Diseases of the digestive system"),
    ("L00", "L99", "XII", "Diseases of the skin and subcutaneous tissue"),
    ("M00", "M99", "XIII", "Diseases of the musculoskeletal system"),
    ("N00", "N99", "XIV", "Diseases of the genitourinary system"),
    ("O00", "O99", "XV", "Pregnancy, childbirth and the puerperium"),
    ("P00", "P96", "XVI", "Conditions originating in the perinatal period"),
    ("Q00", "Q99", "XVII", "Congenital malformations"),
    ("R00", "R99", "XVIII", "Symptoms and signs not elsewhere classified"),
    ("S00", "T98", "XIX", "Injury, poisoning and other external causes"),
    ("V01", "Y98", "XX", "External causes of morbidity and mortality"),
    ("Z00", "Z99", "XXI", "Factors influencing health status"),
    ("U00", "U99", "XXII", "Codes for special purposes"),
]
QUESTION_PREFIXES = sorted([
    "Have you ever been diagnosed with ", "Have you been diagnosed with ", "Were you diagnosed with ",
    "Have you ever had ", "Have you recently had ", "Have you recently taken ", "Have you had ",
    "Have you noticed that ", "Have you noticed ", "Have you been ", "Have you ever ", "Have you ",
    "Do you feel like you are ", "Do you feel that ", "Do you feel your ", "Do you feel ", "Do you have ",
    "Do you suffer from ", "Do you currently ", "Do you regularly ", "Do you ", "Did you ", "Did your ",
    "Do your ", "Are you currently ", "Are you ", "Are your ", "Is your ", "Does your ", "Does the person have ",
    "Were you ",
], key=len, reverse=True)


def slug(name):
    """'Guillain-Barré syndrome' -> 'GuillainBarreSyndrome', 'Possible NSTEMI / STEMI' -> 'PossibleNSTEMISTEMI'"""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = re.findall(r"[A-Za-z0-9]+", ascii_name)
    return "".join(w if w.isupper() and len(w) > 1 else w.capitalize() for w in words)


def short_label(question):
    """'Do you have a fever (either felt or ...)?' -> 'Fever (either felt or ...)'"""
    q = question.strip().rstrip("?:").strip()
    for p in QUESTION_PREFIXES:
        if q.startswith(p):
            q = q[len(p):]
            break
    q = re.sub(r"^(a|an|the|any)\s+", "", q, flags=re.I)
    return q[:1].upper() + q[1:]


def icd10_chapter(code):
    key = code[:3].upper()
    for first, last, num, title in ICD10_CHAPTERS:
        if first <= key <= last:
            return num, title, f"{first}–{last}"
    raise ValueError(code)


def read_split(name):
    with zipfile.ZipFile(RAW / f"release_{name}_patients.zip") as z, z.open(z.namelist()[0]) as f:
        return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")))


def parse_patient(row):
    """One CSV row -> (yes/no evidences, [(evidence, answer)], [(pathology name, probability)])."""
    binary, findings = [], []
    for tok in ast.literal_eval(row["EVIDENCES"]):
        if "_@_" in tok:
            ev, val = tok.split("_@_")
            findings.append((ev, str(val)))
        else:
            binary.append(tok)
    ddx = [(name, float(p)) for name, p in ast.literal_eval(row["DIFFERENTIAL_DIAGNOSIS"])]
    return binary, findings, ddx


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    en = json.loads((RAW / "release_evidences.json").read_text(encoding="utf-8"))
    fr = json.loads((RAW / "french_v14" / "release_evidences.json").read_text(encoding="utf-8"))
    cond = json.loads((RAW / "release_conditions.json").read_text(encoding="utf-8"))
    checks = []

    def check(name, ok, detail):
        checks.append((name, ok, detail))

    # 1. French evidence code -> English E_xx code, through the shared French question text
    key = lambda e: (e["question_fr"].strip(), e["is_antecedent"], e["data_type"])  # noqa: E731
    en_by_key = {key(e): code for code, e in en.items()}
    fr2en = {fcode: en_by_key[key(e)] for fcode, e in fr.items() if key(e) in en_by_key}
    check("French evidence codes aligned to English E_xx codes", len(fr2en) == len(fr) == len(en),
          f"{len(fr2en)}/{len(fr)} matched on (question_fr, is_antecedent, data_type); 0 ambiguous")

    # 2. Pathologies and their expert knowledge-base links
    pathologies, kb_links, name2id = [], [], {}
    for c in cond.values():
        pid = slug(c["cond-name-eng"])
        assert pid not in name2id.values(), pid
        name2id[c["cond-name-eng"]] = pid
        icd = c["icd10-id"].strip().upper()
        num, title, rng = icd10_chapter(icd)
        pathologies.append({"id": pid, "name_en": c["cond-name-eng"], "name_fr": c["cond-name-fr"],
                            "icd10": icd, "chapter": num, "chapter_title": title, "chapter_range": rng,
                            "severity": c["severity"]})
        for kind in ("symptoms", "antecedents"):
            for fcode in c[kind]:
                kb_links.append({"pathology": pid, "evidence": fr2en[fcode], "kind": kind[:-1]})
    check("49 pathologies, each with an ICD-10 chapter and a severity", len(pathologies) == 49,
          f"{len(pathologies)} pathologies in {len({p['chapter'] for p in pathologies})} ICD-10 chapters; "
          f"severity levels {sorted({p['severity'] for p in pathologies})}")
    check("Every knowledge-base symptom/antecedent resolves to an evidence",
          all(l["evidence"] in en for l in kb_links), f"{len(kb_links)} pathology–evidence links")

    # 3. Evidences and answer values
    evidences, values = [], {}
    dangling = sorted({e["code_question"] for e in en.values() if e["code_question"] not in en})
    check("Parent questions (code_question) exist in the evidence file", not dangling,
          f"{len(dangling)} parent codes are missing from the release ({', '.join(dangling)}); "
          "sub-question links are kept only where the parent exists")
    kind_of = {code: e["is_antecedent"] for code, e in en.items()}
    odd = sorted({(l["evidence"], l["kind"]) for l in kb_links if (l["kind"] == "antecedent") != kind_of[l["evidence"]]})
    check("Knowledge-base kind (symptom/antecedent) matches the evidence flag", not odd,
          f"{len(odd)} links disagree: " + ", ".join(f"{e} listed as {k}" for e, k in odd))
    for code, e in en.items():
        parent = e["code_question"] if e["code_question"] != code and e["code_question"] in en else ""
        possible = [str(v) for v in (e.get("possible-values") or [])]
        evidences.append({"code": code, "question_en": e["question_en"], "question_fr": e["question_fr"],
                          "label": short_label(e["question_en"]), "antecedent": e["is_antecedent"],
                          "data_type": e["data_type"], "parent": parent, "default": str(e["default_value"]),
                          "values": possible})
        for v, meaning in (e.get("value_meaning") or {}).items():
            values[v] = {"code": v, "en": meaning["en"], "fr": meaning["fr"]}

    # 4. Patients: test split -> stratified demo sample, validation split -> feature counts
    test, val = read_split("test"), read_split("validate")
    check("Pathology names in patient files match the knowledge base",
          {r["PATHOLOGY"] for r in test} | {r["PATHOLOGY"] for r in val} <= set(name2id),
          f"test {len(test):,} patients, validation {len(val):,} patients, 49/49 names resolve")

    rng = random.Random(SEED)
    by_path = collections.defaultdict(list)
    for i, r in enumerate(test):
        by_path[r["PATHOLOGY"]].append(i)
    sample = []
    for name in sorted(by_path):
        for i in sorted(rng.sample(by_path[name], min(PER_PATHOLOGY, len(by_path[name])))):
            r = test[i]
            binary, findings, ddx = parse_patient(r)
            sample.append({"id": f"T{i:06d}", "row": i, "age": int(r["AGE"]), "sex": r["SEX"],
                           "pathology": name2id[r["PATHOLOGY"]], "initial": r["INITIAL_EVIDENCE"],
                           "binary": binary, "findings": findings, "ddx": [(name2id[n], p) for n, p in ddx]})
    check("Stratified test sample", len(sample) == 49 * PER_PATHOLOGY,
          f"{len(sample)} patients = {PER_PATHOLOGY} per pathology (seed {SEED})")

    default = {code: str(e["default_value"]) for code, e in en.items()}
    n_d, n_fd, n_ed = collections.Counter(), collections.Counter(), collections.Counter()
    sym_out, sym_total, ante_out, ante_total = 0, 0, 0, 0
    kb_sets = collections.defaultdict(set)
    for l in kb_links:
        kb_sets[l["pathology"]].add(l["evidence"])
    for r in val:
        d = name2id[r["PATHOLOGY"]]
        binary, findings, _ = parse_patient(r)
        n_d[d] += 1
        for f in set(binary) | {f"{e}={v}" for e, v in findings}:
            n_fd[(d, f)] += 1
        # an evidence is present unless every answer is its default ("nowhere", "N", 0, ...)
        present = set(binary) | {e for e, v in findings if v != default[e]}
        for e in present:
            n_ed[(d, e)] += 1
        sym = {e for e in present if not en[e]["is_antecedent"]}
        sym_total += len(sym)
        sym_out += len(sym - kb_sets[d])
        ante_total += len(present - sym)
        ante_out += len((present - sym) - kb_sets[d])
    check("Every symptom a patient reports is in the knowledge-base profile of his pathology", sym_out == 0,
          f"{sym_total - sym_out:,} of {sym_total:,} symptom observations are covered (basis of the set-covering step)")
    check("Every antecedent a patient reports is in the knowledge-base profile of his pathology", ante_out == 0,
          f"{ante_total - ante_out:,} of {ante_total:,} antecedent observations are covered")

    stats_path = [{"pathology": d, "patients": n} for d, n in sorted(n_d.items())]
    stats_feat = [{"pathology": d, "feature": f, "count": c} for (d, f), c in sorted(n_fd.items())]
    stats_ev = [{"pathology": d, "evidence": e, "count": c} for (d, e), c in sorted(n_ed.items())]

    dump = lambda name, obj: (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")  # noqa: E731
    dump("pathologies.json", pathologies)
    dump("evidences.json", evidences)
    dump("values.json", sorted(values.values(), key=lambda v: int(v["code"][2:])))
    dump("kb_links.json", kb_links)
    dump("fr2en.json", fr2en)
    dump("sample_patients.json", sample)
    dump("stats_pathology.json", stats_path)
    dump("stats_feature.json", stats_feat)
    dump("stats_evidence.json", stats_ev)
    dump("summary.json", {"test_patients": len(test), "validation_patients": len(val), "sample": len(sample),
                          "kb_links": len(kb_links), "values": len(values), "feature_pairs": len(stats_feat)})
    lines = ["# Data preparation report", "",
             "Source: DDXPlus (Fansi Tchango et al., 2022), figshare doi:10.6084/m9.figshare.20043374, CC BY 4.0", "",
             "| # | Check | Result | Detail |", "|---|---|---|---|"]
    lines += [f"| {i} | {n} | {'PASS' if ok else 'NOTE'} | {d} |" for i, (n, ok, d) in enumerate(checks, 1)]
    (OUT / "validation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
