"""Build the report PDF: docs/report.md + results/*.json -> docs/report.pdf (pandoc + XeLaTeX, citeproc).

Every number in the report is a placeholder <<name>> filled from the evaluation results, the knowledge-graph
statistics and the rule base, so the text cannot drift from the results.

Usage (from the repository root):  python -I scripts/build_report.py [--out PATH]
Needs pandoc >= 3 and a TeX distribution with xelatex (MiKTeX or TeX Live).
"""
import csv
import json
import pathlib
import re
import statistics
import subprocess
import sys
from collections import Counter

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS, RES = ROOT / "docs", ROOT / "results"
sys.path.insert(0, str(ROOT / "src"))
from ddxes import rulebase as rb  # noqa: E402
from ddxes.kgstats import file_counts  # noqa: E402

J = lambda name: json.loads((RES / name).read_text(encoding="utf-8"))  # noqa: E731
pct = lambda x, d=1: f"{100 * x:.{d}f}%"  # noqa: E731
num = lambda x: f"{x:,}"  # noqa: E731


def table(header, rows, align=None, widths=None):
    """A pandoc pipe table; widths are relative dash counts (pandoc turns them into column widths)."""
    widths = widths or [max(3, len(h)) for h in header]
    align = align or ["l"] * len(header)
    sep = []
    for w, a in zip(widths, align):
        d = "-" * max(3, w)
        sep.append(":" + d[1:] if a == "l" else d[:-1] + ":" if a == "r" else ":" + d[2:] + ":")
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(sep) + "|"]
    lines += ["| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |" for r in rows]
    return "\n".join(lines)


def _group_end(s, i):
    """Index just after the brace group that opens at s[i] == '{'."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] in "{}" and s[j - 1] != "\\":
            depth += 1 if s[j] == "{" else -1
            if depth == 0:
                return j + 1
    raise ValueError("unbalanced braces in a table")


def _float_table(block):
    """Pandoc longtable -> floating table with a tabular, so that a short table is never split and the text can fill
    the page before it instead of leaving a gap."""
    i = block.index("{", len("\\begin{longtable}[]"))
    j = _group_end(block, i)
    colspec, rest = block[i + 1: j - 1], block[j:]
    c = rest.index("\\caption")
    end_cap = rest.index("\\tabularnewline", _group_end(rest, rest.index("{", c)))
    caption, rest = rest[c:end_cap], rest[end_cap + len("\\tabularnewline"):]
    head = rest[: rest.index("\\endfirsthead")]
    foot = rest[rest.index("\\endhead") + len("\\endhead"): rest.index("\\endlastfoot")]
    body = rest[rest.index("\\endlastfoot") + len("\\endlastfoot"): rest.rindex("\\end{longtable}")]
    return ("\\begin{table}[htbp]\n\\centering\\singlespacing\\tablefont\n" + caption.strip() + "\n"
            "\\begin{tabular}{" + colspec + "}\n" + head.strip() + "\n" + body.strip() + "\n" + foot.strip() + "\n"
            "\\end{tabular}\n\\end{table}")


def polish_tex(tex):
    """Layout rules of the FMIPA guide that pandoc cannot express.

    E.1: a table is not split if it fits on a page: a short table becomes a float that LaTeX places here or at the top
    of the next page; a long table (the rule base) runs over pages and repeats its number with '(continued)'.
    Long acronyms joined by slashes in the reference list (AHA/ACC/ASE/...) may break after a slash.
    """
    def fix_table(m):
        block = m.group(0)
        head, sep, body = block.partition("\\endlastfoot\n")
        if not sep or "\\caption" not in head:
            return block
        if len(re.findall(r"\\\\\n", body)) <= 30:
            return _float_table(block)
        ncols = len(re.findall(r"(?<!\\)&", body[: re.search(r"\\\\\n", body).start()])) + 1
        label = f"\\multicolumn{{{ncols}}}{{c}}{{\\normalsize\\bfseries Table \\thetable\\ (continued)}}\\\\[6pt]\n"
        return head.replace("\\endfirsthead\n", "\\endfirsthead\n" + label, 1) + sep + body

    tex = re.sub(r"\\begin\{longtable\}.*?\\end\{longtable\}", fix_table, tex, flags=re.S)
    a, b = tex.find("\\begin{CSLReferences}"), tex.find("\\end{CSLReferences}")
    if a >= 0:
        refs = re.sub(r"\b[A-Z]{2,}(?:/[A-Z]{2,}){2,}\b", lambda m: m.group(0).replace("/", "/\\allowbreak{}"), tex[a:b])
        # a URL may break anywhere (xurl) except inside its scheme: no 'ht' / 'tps://' at a line end
        refs = re.sub(r"\\url\{https://([^{}]*)\}", lambda m: "\\href{https://%s}{\\mbox{https://}\\nolinkurl{%s}}"
                      % (m.group(1), m.group(1)), refs)
        tex = tex[:a] + refs + tex[b:]
    return tex


def values():
    """Every placeholder of the report (and of the paper, scripts/build_paper.py): name -> text, computed from the
    evaluation results, the knowledge-graph statistics and the rule base."""
    doc = yaml.safe_load(open(ROOT / "knowledge" / "rules.yaml", encoding="utf-8"))
    rules, knowledge = rb.load_yaml(ROOT / "knowledge" / "rules.yaml")
    sources = doc["sources"]
    kg = json.loads((ROOT / "kg" / "stats.json").read_text(encoding="utf-8"))
    main_r, abl, sens = J("main.json"), J("ablation.json"), J("triage_sensitivity.json")
    rv, cons, curve, early = J("rule_validation.json"), J("consultation.json"), J("consultation_curve.json"), J("early_triage.json")
    summary = json.loads((ROOT / "data" / "processed" / "summary.json").read_text(encoding="utf-8"))

    V = {"n_rules": str(len(rules)), "n_sources": str(len({r.source for r in rules})),
         "n_test": num(main_r["patients"]), "n_val": num(summary["validation_patients"]), "ms": f"{main_r['ms_per_patient']:.1f}"}
    for s, p in (("es", "es"), ("nb", "nb"), ("hybrid", "hy")):
        for k in ("top1", "top3", "top5", "gtpa", "ddr", "ddp", "ddf1"):
            V[f"{p}_{k}"] = pct(main_r[s][k], 2)
        V[f"{p}_top3_miss"] = num(round((1 - main_r[s]["top3"]) * main_r["patients"]))
    t = main_r["triage"]
    V.update(tri_exact=pct(t["exact"]), tri_under=pct(t["under"], 2), tri_over=pct(t["over"]), tri_spec=pct(t["urgent_specificity"]),
             tri_urgent_recall=pct(t["urgent_recall"]), tri_emergency_recall=pct(t["emergency_recall"]))
    e = main_r["explanation"]
    V.update(exp_top1_support=pct(e["top1_supported_by_guideline_rule"]), exp_rules_mean=f"{e['rules_fired_mean']:.1f}",
             exp_cands=f"{e['candidates_after_covering_mean']:.2f}", exp_single=pct(e["single_candidate_share"]))
    V.update(kg_ontology=num(kg["ontology"]), kg_kb=num(kg["kb"]), kg_rules=num(kg["rules"]), kg_stats=num(kg["stats"]),
             kg_cases=num(kg["cases"]), kg_inferred=num(kg["inferred"]), kg_reason_s=f"{kg['reasoning_seconds']:.0f}",
             kg_total=num(sum(kg[k] for k in ("ontology", "kb", "rules", "stats", "cases"))))
    inf = kg["inferred_by_predicate"]
    V.update(n_pairs=num(summary["feature_pairs"]), n_values=num(summary["values"]), n_kb_links=num(summary["kb_links"]),
             inf_charby=num(inf["characterizedBy"]), inf_indicates=num(inf["indicates"]), inf_symptomof=num(inf["symptomOf"]))
    # what each Turtle file holds: <<kb_t_Symptom>> (named resources of a class), <<rules_b_AllOf>> (blank nodes of a
    # class), <<cases_p_hasEvidence>> (triples of a predicate)
    fc = file_counts()
    for f, c in fc.items():
        for kind, key in (("t", "types"), ("b", "blank_types"), ("p", "predicates")):
            for k, n in c[key].items():
                V[f"{f}_{kind}_{k}"] = num(n)
    V.update(st_absent=num(fc["stats"]["types"]["EvidenceStrength"] - summary["feature_pairs"]),
             ru_concepts=num(fc["rules"]["types"]["ClinicalConcept"] + fc["rules"]["types"]["RedFlag"]))
    rc = Counter(r.category for r in rules)
    for c in ("abstraction", "scoring", "diagnostic", "triage", "recommendation"):
        V[f"rc_{c}"] = str(rc[c])
    fid = Counter(r.fidelity for r in rules)
    V.update(fid_faithful=str(fid["faithful"]), fid_partial=str(fid["partial"]), fid_proxy=str(fid["proxy"]),
             fid_partial_proxy=str(fid["partial"] + fid["proxy"]))

    # sources by type
    by_type = Counter(sources[k]["type"] for k in {r.source for r in rules})   # the sources the rules cite
    rules_by_type = Counter(sources[r.source]["type"] for r in rules)
    names = {"ClinicalGuideline": "Clinical practice guideline", "DiagnosticCriteria": "Diagnostic or classification criteria",
             "ClinicalDecisionRule": "Validated clinical decision rule", "CaseDefinition": "Surveillance case definition",
             "ReviewArticle": "Review article", "ClinicalStudy": "Clinical study", "ExpertKnowledgeBase": "Expert knowledge base (DDXPlus)"}
    V["source_type_table"] = table(["Source type", "Sources", "Rules citing them"],
                                   [(names[k], by_type[k], rules_by_type[k]) for k, _ in by_type.most_common()],
                                   ["l", "r", "r"], [40, 8, 16])

    # queries with and without the OWL closure
    why = {"q01-inventory": "entailed classes (Rule, UrgentPathology, RedFlagRule ...) are empty without the closure",
           "q02-rules-per-source": "rules are typed with subclasses only; `a :Rule` is entailed by rdfs:subClassOf",
           "q05-red-flag-rules": ":RedFlagRule is never asserted, only entailed from the rule structure",
           "q06-entailed-pathology-classes": "families and urgency classes come from owl:hasValue restrictions",
           "q09-partial-fidelity": "as q02: needs the entailed type :Rule",
           "q11-set-covering": ":characterizedBy is entailed from :hasSymptom / :hasAntecedent"}
    rows = []
    for q in sorted((ROOT / "queries").glob("q*.rq")):
        def count(folder):
            with open(ROOT / "queries" / folder / (q.stem + ".csv"), encoding="utf-8") as f:
                data = list(csv.reader(f))
            return data[1][0] if data[0] == ["ask"] else str(len(data) - 1)
        a, b = count("results"), count("results-no-inference")
        rows.append((q.stem, a, b, why.get(q.stem, "same answer: uses asserted triples only")))
    V["query_table"] = table(["Query", "With closure", "Without", "Reason"], rows, ["l", "r", "r", "l"], [27, 10, 10, 53])

    # representative diagnostic rules
    pick = ["D-ANA-1", "D-PE-1", "D-EBO-2", "D-FLU-2", "D-PERT-2", "D-PAN-1", "D-CLU-1", "D-LCA-1", "D-SLE-1", "D-HER-1", "D-AF-1", "D-COPD-1"]
    by_id = {r.id: r for r in rules}
    V["rule_examples_table"] = table(["Rule", "Encodes", "CF", "Fidelity", "Source"],
                                     [(i, by_id[i].name, f"{by_id[i].cf:.2f}", by_id[i].fidelity, f"[@{by_id[i].source}]") for i in pick],
                                     ["l", "l", "r", "l", "l"], [16, 42, 6, 10, 26])

    # rule validation
    pos = [r for r in rv["rows"] if r["category"] == "diagnostic" and r.get("precision") is not None and r["cf"] > 0]
    V.update(rv_n=num(rv["patients"]), rv_fired=str(len(pos)), rv_median_prec=pct(statistics.median(r["precision"] for r in pos), 0),
             rv_median_lift=f"{statistics.median(r['lift'] for r in pos):.1f}")
    low = sorted(pos, key=lambda r: r["precision"])[:8]
    V["rv_low_table"] = table(["Rule", "Encodes", "CF", "Fires", "Precision", "Lift"],
                              [(r["id"], r["name"], f"{r['cf']:.2f}", r["fires"], pct(r["precision"], 0), f"{r['lift']:.1f}") for r in low],
                              ["l", "l", "r", "r", "r", "r"], [16, 45, 7, 8, 13, 7])

    # ablation
    ab = {r["name"]: r for r in abl["rows"]}
    V["ablation_table"] = table(["Configuration", "Top-1", "Top-3", "DDF1", "ms / patient"],
                                [(r["name"], pct(r["top1"]), pct(r["top3"]), pct(r["ddf1"]), f"{r['ms']:.1f}")
                                 for r in sorted(abl["rows"], key=lambda r: -r["top1"])], ["l", "r", "r", "r", "r"], [40, 8, 8, 8, 10])
    V["abl_n"] = num(abl["patients"])
    V.update(abl_rules=pct(ab["Guideline rules only"]["top1"]), abl_cover=pct(ab["Set covering only"]["top1"]),
             abl_rules_cover=pct(ab["Guideline rules + covering"]["top1"]), abl_kb_cover=pct(ab["KB evidence + covering"]["top1"]),
             abl_full=pct(ab["Full expert system"]["top1"]), abl_no_absent=pct(ab["Full, no absent-evidence rules"]["top1"]),
             abl_ante=pct(ab["Full, covering incl. antecedents"]["top1"]))

    # triage sensitivity
    srows = []
    for r in sens["rows"]:
        if r["severity_threshold"] in (0.1, 0.2, 0.5, 1.01):
            thr = "off" if r["severity_threshold"] > 1 else f"{r['severity_threshold']:.1f}"
            srows.append(("yes" if r["red_flag_rules"] else "no", thr, pct(r["exact"]), pct(r["under"]), pct(r["over"]),
                          pct(r["urgent_recall"])))
    V["triage_sens_table"] = table(["Red-flag rules", "Severity schema threshold", "Exact", "Under-triage", "Over-triage", "Urgent recall"],
                                   srows, ["l", "r", "r", "r", "r", "r"], [12, 22, 8, 12, 12, 12])
    off = {r["red_flag_rules"]: r for r in sens["rows"] if r["severity_threshold"] > 1}
    V.update(sens_red_only=pct(off[True]["under"]), sens_none=pct(off[False]["under"]))

    # consultation
    c, c0 = cons["summary"], cons["without_rule_out"]
    V.update(c_q=f"{c['questions_mean']:.1f}", c_top1=pct(c["top1"]), c_top3=pct(c["top3"]), c_under=pct(c["under_triage"]),
             c_missed=pct(c["urgent_missed"]), c0_q=f"{c0['questions_mean']:.1f}", c0_top1=pct(c0["top1"]), c0_top3=pct(c0["top3"]),
             c0_under=pct(c0["under_triage"]), c0_missed=pct(c0["urgent_missed"]),
             c_extra_q=f"{c['questions_mean'] - c0['questions_mean']:.1f}")
    c99 = next(r for r in curve["rows"] if abs(r["confidence"] - 0.99) < 1e-9)
    V.update(c99_q=f"{c99['questions_mean']:.1f}", c99_top1=pct(c99["top1"]))
    V["curve_table"] = table(["Stop when the leading P reaches", "Questions (mean)", "Top-1", "Top-3"],
                             [(f"{r['confidence']}", f"{r['questions_mean']:.1f}", pct(r["top1"]), pct(r["top3"])) for r in curve["rows"]],
                             ["l", "r", "r", "r"], [30, 16, 8, 8])
    er = {(r["red_flag_rules"], r["questions"]): r for r in early["rows"]}
    ks = sorted({r["questions"] for r in early["rows"]})
    V["early_table"] = table(["Questions answered", "Under-triage", "Urgent cases missed", "Urgent missed, no red-flag rules"],
                             [("all (end)" if k >= 30 else str(k), pct(er[(True, k)]["under"]), pct(er[(True, k)]["urgent_missed"]),
                               pct(er[(False, k)]["urgent_missed"])) for k in ks], ["l", "r", "r", "r"], [18, 12, 18, 28])

    # free-text intake with a language model (scripts/evaluate_intake.py)
    it = J("intake.json")
    L = it["languages"]
    n_path = len(json.loads((ROOT / "data" / "processed" / "pathologies.json").read_text(encoding="utf-8")))
    V["in_scope"] = ("one demo patient per pathology" if it["patients"] >= n_path else
                     f"one demo patient for each of the first {it['patients']} pathologies in alphabetical order")
    V["in_limit"] = ("" if it["patients"] >= n_path else
                     f" The run used the free tier of Groq, whose limit of 200,000 tokens per day ended it after "
                     f"{it['patients']} of the {n_path} pathologies; the script resumes from its cache, so the remaining "
                     f"pathologies can be added and the tables regenerated.")
    writers = Counter(row["writer"].split(" (")[1].rstrip(")") for row in it["cases"]).most_common()
    V.update(in_n=str(it["patients"]), in_reader=it["reader"].replace("groq (", "").rstrip(")"),
             in_writer=writers[0][0] if len(writers) == 1 else
             ", ".join(f"`{m}` for {n} patients" for m, n in writers[:-1]) + f" and `{writers[-1][0]}` for {writers[-1][1]}",
             in_mentioned=f"{L['en']['findings_mentioned'] / L['en']['descriptions']:.1f}",
             in_base_q=f"{it['baseline']['questions']:.1f}", in_base_top1=pct(it["baseline"]["top1"]),
             in_oracle_q=f"{it['oracle']['questions']:.1f}", in_oracle_top1=pct(it["oracle"]["top1"]))
    for lang in ("en", "id"):
        s = L[lang]
        V.update({f"in_{lang}_prec": pct(s["precision_vs_record"]), f"in_{lang}_rec": pct(s["recall_of_mentioned"]),
                  f"in_{lang}_f1": pct(s["f1"]), f"in_{lang}_cc": pct(s["main_complaint"], 0),
                  f"in_{lang}_age": pct(s["age"], 0), f"in_{lang}_sex": pct(s["sex"], 0),
                  f"in_{lang}_sec": f"{s['seconds_per_reading']:.1f}", f"in_{lang}_q": f"{s['questions']:.1f}",
                  f"in_{lang}_top1": pct(s["top1"]), f"in_{lang}_top3": pct(s["top3"])})
    V["intake_table"] = table(
        ["Measure", "English", "Bahasa Indonesia"],
        [("Descriptions", L["en"]["descriptions"], L["id"]["descriptions"]),
         ("Findings mentioned / read", f"{L['en']['findings_mentioned']} / {L['en']['findings_read']}",
          f"{L['id']['findings_mentioned']} / {L['id']['findings_read']}"),
         ("Precision against the patient record", V["in_en_prec"], V["in_id_prec"]),
         ("Recall of the mentioned findings", V["in_en_rec"], V["in_id_rec"]),
         ("F1", V["in_en_f1"], V["in_id_f1"]),
         ("Main complaint as recorded", V["in_en_cc"], V["in_id_cc"]),
         ("Age / sex read correctly", f"{V['in_en_age']} / {V['in_en_sex']}", f"{V['in_id_age']} / {V['in_id_sex']}"),
         ("Seconds per reading", V["in_en_sec"], V["in_id_sec"])],
        ["l", "r", "r"], [40, 16, 18])
    V["intake_consult_table"] = table(
        ["The consultation starts from", "Questions (mean)", "Top-1", "Top-3"],
        [("The main complaint only (questionnaire)", V["in_base_q"], V["in_base_top1"], pct(it["baseline"]["top3"])),
         ("The reading of the English description", V["in_en_q"], V["in_en_top1"], V["in_en_top3"]),
         ("The reading of the Indonesian description", V["in_id_q"], V["in_id_top1"], V["in_id_top3"]),
         ("The mentioned findings, read without error", V["in_oracle_q"], V["in_oracle_top1"], pct(it["oracle"]["top3"]))],
        ["l", "r", "r", "r"], [42, 16, 8, 8])

    # confusions
    pn = {p["id"]: p["name_en"] for p in json.loads((ROOT / "data" / "processed" / "pathologies.json").read_text(encoding="utf-8"))}
    V["confusions"] = "; ".join(f"{pn[x['truth']]} taken for {pn[x['predicted']]} ({num(x['patients'])} patients)"
                                for x in main_r["confusions"][:3])

    # appendix: every rule
    fid_letter = {"faithful": "f", "partial": "p", "proxy": "x"}
    arows = []
    for r in rules:
        val = r.consequents[0].points if r.category == "scoring" else r.cf
        arows.append((r.id, r.name, f"{val:g}", fid_letter[r.fidelity], f"[@{r.source}]"))
    V["rules_appendix"] = table(["Id", "Rule", "CF", "f", "Source"], arows, ["l", "l", "r", "l", "l"], [20, 44, 6, 3, 27])

    # Indonesian number format for the Intisari: decimal comma, thousands point
    for k, v in list(V.items()):
        if "\n" not in v and re.fullmatch(r"[\d.,%]+", v):
            V[k + "_id"] = v.replace(",", "\0").replace(".", ",").replace("\0", ".")
    return V


def main():
    out_pdf = pathlib.Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else DOCS / "report.pdf"
    V = values()
    text = (DOCS / "report.md").read_text(encoding="utf-8")
    missing = sorted(set(re.findall(r"<<(\w+)>>", text)) - set(V))
    if missing:
        sys.exit(f"unfilled placeholders: {missing}")
    text = re.sub(r"<<(\w+)>>", lambda m: V[m.group(1)], text)
    build = DOCS / "build"
    build.mkdir(exist_ok=True)
    (build / "report.md").write_text(text, encoding="utf-8")
    # pandoc -> LaTeX (citations resolved by citeproc), then XeLaTeX run from docs/ so that figures/ resolves
    cmd = ["pandoc", "build/report.md", "-s", "-o", "build/report.tex", "--citeproc", "--bibliography", "references.json",
           "--csl", "harvard-fmipa-ugm.csl", "-H", "report-header.tex", "--number-sections", "--top-level-division=chapter",
           "-M", "link-citations=true", "-V", "documentclass=report", "-V", "papersize=a4", "-V", "fontsize=12pt",
           "-V", "geometry:top=4cm", "-V", "geometry:left=4cm", "-V", "geometry:bottom=3cm", "-V", "geometry:right=3cm",
           "-V", "mainfont=Times New Roman", "-V", "monofont=Consolas", "-V", "monofontoptions=HyphenChar=None", "-V", "mathfont=Cambria Math",
           "-V", "colorlinks=true", "-V", "linkcolor=black", "-V", "citecolor=black", "-V", "urlcolor=blue",
           "--syntax-highlighting=tango"]
    subprocess.run(cmd, check=True, cwd=DOCS)
    tex_file = build / "report.tex"
    tex_file.write_text(polish_tex(tex_file.read_text(encoding="utf-8")), encoding="utf-8")
    for _ in range(3):   # contents, lists of tables and figures, longtable widths
        run = subprocess.run(["xelatex", "-interaction=nonstopmode", "-halt-on-error", "-output-directory=build", "build/report.tex"],
                             cwd=DOCS, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if run.returncode != 0:
            print(run.stdout[-3000:])
            sys.exit("xelatex failed")
    log = (DOCS / "build" / "report.log").read_text(encoding="utf-8", errors="replace")
    missing_chars = sorted(set(re.findall(r"Missing character: There is no (.) ", log)))
    if missing_chars:
        print("WARNING: glyphs missing in the fonts:", missing_chars)
    out_pdf.write_bytes((DOCS / "build" / "report.pdf").read_bytes())
    print(f"wrote {out_pdf}")


if __name__ == "__main__":
    main()
