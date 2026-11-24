"""Build docs/explainer.pdf: a plain-language guide to DDX-ES, one analogy and one diagram per idea, and a glossary.

The text is docs/explainer.html.j2 (Jinja2). Every number is filled in from results/*.json, kg/stats.json and the
rule base, so the guide always matches the report and the slides. Playwright prints the page with Microsoft Edge.

    python scripts/build_explainer.py [--copy-to PATH]

Needs the `docs` extra (playwright, Pillow) and Microsoft Edge.
"""
import base64
import json
import pathlib
import shutil
import statistics
import sys
from collections import Counter

import jinja2
import yaml
from markupsafe import Markup
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from ddxes import rulebase as rb  # noqa: E402

SOURCE = ROOT / "docs" / "explainer.html.j2"
BUILD = ROOT / "docs" / "build" / "explainer"
OUT = ROOT / "docs" / "explainer.pdf"
FIG = ROOT / "docs" / "figures"
FONTS = ROOT / "src" / "ddxes" / "web" / "static" / "fonts"
CROPS = {"why": ("ui-consultation.png", (236, 330, 1870, 1170)),
         "whynot": ("ui-whynot.png", (250, 215, 2640, 1075)),
         "triage": ("ui-case.png", (1870, 830, 2640, 1520))}
FONT_FACES = [("Work Sans", "WorkSans-Regular", 400, "normal"), ("Work Sans", "WorkSans-Italic", 400, "italic"),
              ("Work Sans", "WorkSans-Bold", 700, "normal"), ("Work Sans", "WorkSans-BoldItalic", 700, "italic"),
              ("IBM Plex Serif", "IBMPlexSerif-Regular", 400, "normal"),
              ("IBM Plex Serif", "IBMPlexSerif-Bold", 700, "normal"),
              ("IBM Plex Serif", "IBMPlexSerif-Italic", 400, "italic"),
              ("IBM Plex Mono", "IBMPlexMono-Regular", 400, "normal")]
FOOTER = ('<div style="width:100%;padding:0 18mm;box-sizing:border-box;font:7.5pt \'Segoe UI\',Arial,sans-serif;color:#5E6E7E;'
          'display:flex;justify-content:space-between"><span>DDX-ES, explained · a companion to the report and the '
          'slides</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>')


def jload(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def pct(x, d=1):
    return f"{100 * x:.{d}f}%"


def fmt(n):
    return f"{n:,}"


def values():
    rules, _ = rb.load_yaml(ROOT / "knowledge" / "rules.yaml")
    sources = yaml.safe_load((ROOT / "knowledge" / "rules.yaml").read_text(encoding="utf-8"))["sources"]
    cited = {r.source for r in rules}
    kg, summary = jload("kg/stats.json"), jload("data/processed/summary.json")
    main, abl, sens = jload("results/main.json"), jload("results/ablation.json"), jload("results/triage_sensitivity.json")
    rv, cons, curve = jload("results/rule_validation.json"), jload("results/consultation.json"), \
        jload("results/consultation_curve.json")
    es, nb, hy, tri, ex = main["es"], main["nb"], main["hybrid"], main["triage"], main["explanation"]
    n = main["patients"]
    c, c0 = cons["summary"], cons["without_rule_out"]
    c99 = next(r for r in curve["rows"] if abs(r["confidence"] - 0.99) < 1e-9)
    pos = [r for r in rv["rows"] if r["category"] == "diagnostic" and r.get("precision") is not None and r["cf"] > 0]
    ab = {r["name"]: r["top1"] for r in abl["rows"]}
    cat, fid = Counter(r.category for r in rules), Counter(r.fidelity for r in rules)
    names = {"ClinicalGuideline": "guidelines", "ReviewArticle": "reviews", "DiagnosticCriteria": "criteria",
             "ClinicalStudy": "clinical studies", "ClinicalDecisionRule": "decision rules",
             "CaseDefinition": "case definitions"}
    types = Counter(sources[s]["type"] for s in cited)
    mix = ", ".join(f"{k} {names[t]}" for t, k in types.most_common() if t in names)
    if types.get("ExpertKnowledgeBase"):
        mix += " and the DDXPlus paper itself"
    asserted = sum(kg[k] for k in ("ontology", "kb", "rules", "stats", "cases"))

    def query(name):
        import csv
        out = []
        for folder in ("results", "results-no-inference"):
            with open(ROOT / "queries" / folder / f"{name}.csv", encoding="utf-8") as f:
                data = list(csv.reader(f))
            out.append(data[1][0] if data[0] == ["ask"] else str(len(data) - 1))
        return out

    off = {r["red_flag_rules"]: r for r in sens["rows"] if r["severity_threshold"] > 1}
    it = jload("results/intake.json")                      # the optional free-text intake (scripts/evaluate_intake.py)
    il = it["languages"]
    writers = [w.split(" (")[1].rstrip(")") for w in it["writer"]]
    n_path = len(jload("data/processed/pathologies.json"))
    intake = dict(n=it["patients"],
                  scope=("one demo patient per disease" if it["patients"] >= n_path else
                         f"one demo patient for each of the first {it['patients']} diseases in alphabetical order "
                         f"(the free tier's daily token limit ended the run there)"), reader=it["reader"].replace("groq (", "").rstrip(")") + " on Groq",
                  writer=(f"Another language model ({writers[0]})" if len(writers) == 1 else
                          f"Other language models ({' and '.join(writers)})"),
                  en_prec=pct(il["en"]["precision_vs_record"]), id_prec=pct(il["id"]["precision_vs_record"]),
                  en_rec=pct(il["en"]["recall_of_mentioned"]), id_rec=pct(il["id"]["recall_of_mentioned"]),
                  base_q=f"{it['baseline']['questions']:.1f}", en_q=f"{il['en']['questions']:.1f}",
                  en1=pct(il["en"]["top1"]), id1=pct(il["id"]["top1"]), base1=pct(it["baseline"]["top1"]))
    return dict(
        intake=intake,
        n_rules=len(rules), n_sources=len(cited), cat=cat, fid=fid, mix=mix, kg=kg, asserted=fmt(asserted),
        inferred=fmt(kg["inferred"]), kg_files=[(k, fmt(kg[k])) for k in ("ontology", "kb", "rules", "stats", "cases",
                                                                           "inferred")],
        n_test=fmt(n), n_val=fmt(summary["validation_patients"]), sample=summary["sample"],
        kb_links=summary["kb_links"], pairs=fmt(summary["feature_pairs"]),
        es1=pct(es["top1"], 2), nb1=pct(nb["top1"], 2), hy1=pct(hy["top1"], 2), ddf1=pct(es["ddf1"]),
        miss3=[round((1 - m["top3"]) * n) for m in (es, nb, hy)], ms=f"{main['ms_per_patient']:.1f}",
        supported=pct(ex["top1_supported_by_guideline_rule"]), fired=f"{ex['rules_fired_mean']:.0f}",
        cands=f"{ex['candidates_after_covering_mean']:.1f}", single=pct(ex["single_candidate_share"], 0),
        emerg=pct(tri["emergency_recall"], 0), urgent=pct(tri["urgent_recall"], 0), under=pct(tri["under"], 2),
        over=pct(tri["over"]), exact=pct(tri["exact"]), red_only_under=pct(off[True]["under"]),
        neither_under=pct(off[False]["under"]),
        abl_n=fmt(abl["patients"]), ab={k: pct(v) for k, v in ab.items()},
        cq=f"{c['questions_mean']:.1f}", c1=pct(c["top1"]), c99q=f"{c99['questions_mean']:.1f}", c99_1=pct(c99["top1"]),
        c_missed=pct(c["urgent_missed"]), c0_missed=pct(c0["urgent_missed"]),
        extra_q=f"{c['questions_mean'] - c0['questions_mean']:.1f}",
        rv_n=fmt(rv["patients"]), rv_fire=sum(1 for r in rv["rows"] if r["fires"] > 0),
        rv_prec=pct(statistics.median(r["precision"] for r in pos), 0),
        rv_lift=f"{statistics.median(r['lift'] for r in pos):.1f}",
        q={k: query(k) for k in ("q05-red-flag-rules", "q06-entailed-pathology-classes", "q11-set-covering")},
    )


def fonts_css(faces=FONT_FACES):
    rules = []
    for family, stem, weight, style in faces:
        data = base64.b64encode((FONTS / f"{stem}.ttf").read_bytes()).decode("ascii")
        rules.append(f"@font-face{{font-family:'{family}';font-weight:{weight};font-style:{style};"
                     f"src:url(data:font/ttf;base64,{data}) format('truetype')}}")
    return "\n".join(rules)


def crops():
    BUILD.mkdir(parents=True, exist_ok=True)
    out = {}
    for key, (name, box) in CROPS.items():
        path = BUILD / f"{key}.png"
        Image.open(FIG / name).crop(box).save(path, optimize=True)
        out[key] = path.as_uri()
    return out


def main():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(SOURCE.parent), autoescape=True,
                             undefined=jinja2.StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    figs = {p.stem: p.as_uri() for p in FIG.iterdir() if p.suffix in (".png", ".jpg")} | crops()
    html = env.get_template(SOURCE.name).render(fonts=Markup(fonts_css()), fig=figs, **values())
    page_path = BUILD / "explainer.html"
    page_path.write_text(html, encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page()
        page.goto(page_path.as_uri(), wait_until="networkidle")
        page.evaluate("document.fonts.ready.then(() => true)")
        page.pdf(path=str(OUT), format="A4", print_background=True, display_header_footer=True,
                 header_template="<span></span>", footer_template=FOOTER,
                 margin={"top": "16mm", "bottom": "17mm", "left": "18mm", "right": "18mm"})
        browser.close()
    print(f"wrote {OUT}")
    if "--copy-to" in sys.argv:
        dest = pathlib.Path(sys.argv[sys.argv.index("--copy-to") + 1])
        shutil.copyfile(OUT, dest)
        print(f"copied to {dest}")


if __name__ == "__main__":
    main()
