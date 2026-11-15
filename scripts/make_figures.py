"""Figures for the report and the slides -> docs/figures/*.png (matplotlib).

Diagrams: architecture, ontology, inference pipeline.  Charts (from results/*.json): system comparison, ablation,
triage confusion matrix, triage sensitivity, rule calibration, consultation length.
Palette: the validated reference palette of the dataviz guidance (categorical blue / orange / aqua, a sequential
blue ramp for magnitudes); every bar carries a direct value label, all bars start at zero, no dual axes.

Usage (from the repository root):  python -I scripts/make_figures.py
"""
import json
import pathlib

import matplotlib
import yaml

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402
from matplotlib.ticker import MaxNLocator, PercentFormatter  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "figures"
RES = ROOT / "results"
OUT.mkdir(parents=True, exist_ok=True)

SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
S1, S2, S3 = "#2a78d6", "#eb6834", "#1baf7a"          # categorical slots 1-3 (validated all-pairs)
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
UGM_BLUE = "#01416B"
GREEN, GOLD, PURPLE, RUST = "#1b5e20", "#8a6d00", "#5b2c6f", "#a23b12"
TINT = {"kg": "#e6eef5", "rules": "#fff4cc", "engine": "#e8f3e8", "ui": "#f3e8f5", "src": "#f2f2f0", "ml": "#fbe9e3"}

plt.rcParams.update({"font.family": ["Segoe UI", "DejaVu Sans"], "font.size": 10, "axes.edgecolor": AXIS,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2, "text.color": INK,
                     "figure.facecolor": "white", "axes.facecolor": SURFACE, "savefig.dpi": 220,
                     "axes.spines.top": False, "axes.spines.right": False})


def load(name):
    p = RES / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save(fig, name):
    fig.savefig(OUT / name, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("wrote docs/figures/" + name)


def canvas(w, h, title=None):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(-0.01, 1.01)
    ax.set_ylim(-0.02, 1.0)
    ax.axis("off")
    if title:
        ax.text(0.0, 0.995, title, fontsize=14, fontweight="bold", color=UGM_BLUE, va="top")
    return fig, ax


def rbox(ax, x, y, w, h, fill, edge, lw=1.0, r=0.012):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0.004,rounding_size={r}", fc=fill, ec=edge, lw=lw))


def block(ax, x, y, w, h, title, lines=(), fill="#e6eef5", edge=UGM_BLUE, title_color=UGM_BLUE, size=9.5):
    rbox(ax, x, y, w, h, fill, edge)
    ax.text(x + 0.012, y + h - 0.014, title, ha="left", va="top", fontsize=size, fontweight="bold", color=title_color)
    for i, ln in enumerate(lines):
        ax.text(x + 0.012, y + h - 0.055 - i * 0.034, ln, ha="left", va="top", fontsize=size - 1.6, color=INK2)


def item(ax, x, y, w, h, title, desc, edge, title_color):
    rbox(ax, x, y, w, h, "white", edge, lw=0.8, r=0.01)
    ax.text(x + 0.01, y + h * 0.68, title, fontsize=8.8, fontweight="bold", color=title_color, va="center")
    ax.text(x + 0.01, y + h * 0.28, desc, fontsize=7.8, color=INK2, va="center")


def link(ax, a, b, text=None, color=MUTED, rad=0.0, lw=1.2, dy=0.012, conn=None):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=11, color=color, lw=lw,
                                 connectionstyle=conn or f"arc3,rad={rad}", shrinkA=1, shrinkB=1))
    if text:
        ax.text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + dy, text, ha="center", va="bottom", fontsize=7.8, color=INK2,
                bbox=dict(fc="white", ec="none", pad=0.6))


# ---------------------------------------------------------------------------------------------- diagrams
def architecture():
    rules = yaml.safe_load(open(ROOT / "knowledge" / "rules.yaml", encoding="utf-8"))["rules"]
    n_rules, n_src = len(rules), len({r["source"] for r in rules})
    inferred = json.loads((ROOT / "kg" / "stats.json").read_text(encoding="utf-8"))["inferred"]
    fig, ax = canvas(12.5, 6.8, "DDX-ES architecture")
    # column 1: knowledge sources and calibration data
    block(ax, 0.0, 0.52, 0.19, 0.4, "Knowledge sources",
          ["DDXPlus expert knowledge base:", "49 pathologies, 223 questions,", "888 links reviewed by physicians",
           "", f"{n_src} cited clinical sources:", "ESC, WHO, NICE, CDC, IDSA ...", f"-> {n_rules} IF-THEN rules"],
          fill=TINT["src"], edge=MUTED, title_color=INK)
    block(ax, 0.0, 0.08, 0.19, 0.34, "Calibration data",
          ["DDXPlus validation split,", "132,448 synthetic patients", "-> likelihood ratio and CF", "   of every knowledge-base link"],
          fill=TINT["src"], edge=MUTED, title_color=INK)
    # column 2: the knowledge graph
    block(ax, 0.23, 0.0, 0.27, 0.92, "Knowledge graph (RDF / OWL 2)", [], fill=TINT["kg"])
    files = [("ontology.ttl", "TBox: classes, properties, OWL 2 RL axioms"), ("kb.ttl", "pathologies, questions, profiles"),
             ("rules.ttl", f"{n_rules} rules as RDF, with their sources"), ("stats.ttl", "calibration: LR and CF per link"),
             ("inferred.ttl", f"OWL 2 RL closure (owlrl), {inferred:,} triples"), ("cases.ttl", "980 demo patients"),
             ("+ provenance", "inferences written back (PROV-O)")]
    for i, (f, d) in enumerate(files):
        item(ax, 0.245, 0.775 - i * 0.11, 0.24, 0.088, f, d, "#9fb6cc", UGM_BLUE)
    # column 3: case on top, engine, ML baseline
    block(ax, 0.54, 0.8, 0.27, 0.12, "Case (working memory)", ["DDXPlus record (closed world)", "or live answers (open world)"],
          fill=TINT["src"], edge=MUTED, title_color=INK)
    block(ax, 0.54, 0.2, 0.27, 0.57, "Inference engine", [], fill=TINT["engine"], edge="#2e7d32", title_color=GREEN)
    phases = [("1  Forward chaining", "stratified rules, CF, three-valued logic"), ("2  Abduction", "parsimonious set covering"),
              ("3  Belief aggregation", "MYCIN CF as a sum of log LR, + prior"), ("4  Triage and next steps", "red flags, severity, safety first"),
              ("Consultation / Explanation", "backward chaining, WHY · HOW · WHY NOT")]
    for i, (t, d) in enumerate(phases):
        item(ax, 0.555, 0.63 - i * 0.096, 0.24, 0.078, t, d, "#a5c9a7", GREEN)
    block(ax, 0.54, 0.0, 0.27, 0.15, "ML baseline (comparison)", ["naive Bayes on the same counts;", "hybrid = log-linear pooling"],
          fill=TINT["ml"], edge=S2, title_color=RUST)
    # column 4: interfaces and outputs
    block(ax, 0.85, 0.47, 0.15, 0.45, "Interfaces", ["Web UI (Starlette)", "· consultation", "· case analysis",
          "· knowledge base", "· SPARQL", "· evaluation", "CLI  ddxes ...", "pytest suite"], fill=TINT["ui"], edge="#7b3f8c", title_color=PURPLE)
    block(ax, 0.85, 0.0, 0.15, 0.42, "Outputs", ["differential (P, CF)", "triage level", "recommendations", "explanations",
          "RDF provenance"], fill=TINT["ui"], edge="#7b3f8c", title_color=PURPLE)
    link(ax, (0.19, 0.72), (0.245, 0.72), "build_kg.py", dy=0.014)
    link(ax, (0.19, 0.3), (0.245, 0.49), "counts", dy=0.0)
    link(ax, (0.485, 0.6), (0.555, 0.6), "rules,\nprofiles", dy=0.01)
    link(ax, (0.675, 0.8), (0.675, 0.775))
    link(ax, (0.555, 0.24), (0.485, 0.16), "PROV-O", rad=-0.2, dy=0.0)
    link(ax, (0.81, 0.6), (0.85, 0.6))
    link(ax, (0.81, 0.3), (0.85, 0.3))
    save(fig, "architecture.png")


def ontology():
    fig, ax = canvas(12.5, 6.4, "Core of the ontology (TBox)")
    N = {}
    W, H = 0.17, 0.085

    def node(key, x, y, label, sub, fill, edge):
        rbox(ax, x - W / 2, y - H / 2, W, H, fill, edge, lw=1.1)
        ax.text(x, y + 0.014, label, ha="center", va="center", fontsize=9.6, fontweight="bold", color=edge)
        ax.text(x, y - 0.022, sub, ha="center", va="center", fontsize=7.4, color=INK2)
        N[key] = (x, y)

    def edge(a, b, label, side="above", rad=0.0):
        (xa, ya), (xb, yb) = N[a], N[b]
        # start and end on the box borders
        if abs(ya - yb) < 1e-6:
            s, e = (xa + (W / 2 if xb > xa else -W / 2), ya), (xb - (W / 2 if xb > xa else -W / 2), yb)
        elif abs(xa - xb) < 1e-6:
            s, e = (xa, ya + (H / 2 if yb > ya else -H / 2)), (xb, yb - (H / 2 if yb > ya else -H / 2))
        else:
            s, e = (xa + (W / 2 if xb > xa else -W / 2) * 0.6, ya + (H / 2 if yb > ya else -H / 2)), \
                   (xb - (W / 2 if xb > xa else -W / 2) * 0.6, yb - (H / 2 if yb > ya else -H / 2))
        ax.add_patch(FancyArrowPatch(s, e, arrowstyle="-|>", mutation_scale=11, color=MUTED, lw=1.2,
                                     connectionstyle=f"arc3,rad={rad}", shrinkA=0, shrinkB=0))
        mx, my = (s[0] + e[0]) / 2, (s[1] + e[1]) / 2
        if abs(s[0] - e[0]) < 1e-6:          # vertical edge: label to the right
            ax.text(mx + 0.008, my, label, ha="left", va="center", fontsize=7.8, color=INK2)
        else:
            ax.text(mx, my + (0.016 if side == "above" else -0.016), label, ha="center", va="bottom" if side == "above" else "top",
                    fontsize=7.8, color=INK2, bbox=dict(fc="white", ec="none", pad=0.5))

    A, B, C, D = 0.1, 0.38, 0.64, 0.9
    node("Pathology", A, 0.84, ":Pathology", "49 · severity, ICD-10, prior", TINT["kg"], UGM_BLUE)
    node("Evidence", B, 0.84, ":Evidence", "Symptom | Antecedent", TINT["kg"], UGM_BLUE)
    node("Value", C, 0.84, ":AnswerValue", "199 answer values", TINT["kg"], UGM_BLUE)
    node("Strength", A, 0.55, ":EvidenceStrength", "LR and CF of a link (n-ary)", TINT["kg"], UGM_BLUE)
    node("Condition", B, 0.55, ":Condition", "AllOf, AnyOf, AtLeast, Not, ...", TINT["rules"], GOLD)
    node("Patient", C, 0.55, ":Patient", "the case", TINT["src"], INK2)
    node("Triage", D, 0.55, ":TriageLevel", "Emergency ... SelfCare", "#fbe3d6", RUST)
    node("Consequent", A, 0.26, ":Consequent", "concept, pathology, triage ...", TINT["rules"], GOLD)
    node("Rule", B, 0.26, ":Rule", "prov:Plan · 5 subclasses", TINT["rules"], GOLD)
    node("Firing", C, 0.26, ":RuleFiring", "prov:Activity", TINT["engine"], GREEN)
    node("Inference", D, 0.26, ":Inference", "prov:Entity", TINT["engine"], GREEN)
    node("Source", B, 0.03, ":Source", "guideline, criteria ... (DOI)", TINT["ui"], PURPLE)
    edge("Pathology", "Evidence", "characterizedBy")
    edge("Evidence", "Value", "hasPossibleValue")
    edge("Strength", "Pathology", "ofPathology")
    edge("Condition", "Evidence", "onEvidence")
    edge("Patient", "Evidence", "hasEvidence")
    edge("Patient", "Triage", "hasTriage")
    edge("Rule", "Condition", "premise")
    edge("Rule", "Consequent", "asserts", side="below")
    edge("Rule", "Source", "dct:source")
    edge("Firing", "Rule", "firedRule", side="below")
    edge("Inference", "Firing", "prov:wasGeneratedBy", side="below")
    edge("Inference", "Patient", "aboutCase")
    ax.text(A - W / 2, 0.93, "hasSymptom, hasAntecedent ⊑ characterizedBy (entailed)", fontsize=7.6, color=INK2)
    ax.text(0.53, 0.06, "Defined classes, entailed by OWL 2 RL:", fontsize=8.4, color=INK, fontweight="bold")
    ax.text(0.53, 0.02, "UrgentPathology, LifeThreateningPathology, RespiratoryDisease ... ·\nRedFlagRule, GuidelineBasedRule, "
            "CriteriaBasedRule · EmergencyCase, UrgentCase", fontsize=8.0, color=INK2, va="top")
    save(fig, "ontology.png")


def pipeline():
    fig, ax = canvas(12.5, 3.3)
    steps = [("Case facts", "answers,\nage, sex", TINT["src"], INK2),
             ("1  Forward chaining", "abstraction, scoring,\nthen diagnostic\nrules (CF)", TINT["rules"], GOLD),
             ("2  Abduction", "keep pathologies\nwhose profile explains\nevery symptom", TINT["kg"], UGM_BLUE),
             ("3  Aggregation", "rule CFs + link CFs\nas a sum of log LR\n+ log prior odds", TINT["engine"], GREEN),
             ("4  Triage, advice", "red-flag rules,\nseverity schema,\nrecommendations", "#fbe3d6", RUST),
             ("Explanation", "proof trees, HOW,\nWHY NOT, PROV-O\nin the graph", TINT["ui"], PURPLE)]
    w, gap = 0.148, 0.0222
    for i, (t, d, fill, col) in enumerate(steps):
        x = i * (w + gap)
        rbox(ax, x, 0.24, w, 0.62, fill, col, lw=1.1, r=0.02)
        ax.text(x + w / 2, 0.74, t, ha="center", va="center", fontsize=10, fontweight="bold", color=col)
        ax.text(x + w / 2, 0.46, d, ha="center", va="center", fontsize=8.6, color=INK2, linespacing=1.35)
        if i < len(steps) - 1:
            link(ax, (x + w + 0.002, 0.55), (x + w + gap - 0.002, 0.55))
    ax.text(0.5, 0.08, "Example T002456 (pulmonary embolism): scoring rules give Wells score 6.5 → D-PE-1 (Wells et al. 2000) · "
            "covering keeps 1 of 49 pathologies · P = 1.00 → T-PE: Emergency · R-PE-HIGH: CT pulmonary angiography",
            ha="center", fontsize=8.6, color=INK2)
    save(fig, "pipeline.png")


# ---------------------------------------------------------------------------------------------- charts
def barh(ax, labels, values, color, fmt="{:.1%}", xmax=None):
    y = list(range(len(labels)))
    bars = ax.barh(y, values, color=color, height=0.62, edgecolor=SURFACE, linewidth=2)
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.grid(axis="x", color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(axis="y", length=0)
    top = xmax or max(values) * 1.2
    ax.set_xlim(0, top)
    for b, v in zip(bars, values):
        ax.text(b.get_width() + top * 0.012, b.get_y() + b.get_height() / 2, fmt.format(v), va="center", fontsize=9, color=INK)
    return bars


def systems():
    m = load("main.json")
    if not m:
        return
    names = ["DDX-ES (expert system)", "Naive Bayes (ML baseline)", "Hybrid (ES + NB)"]
    keys = ["es", "nb", "hybrid"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 2.8), gridspec_kw={"wspace": 0.6})
    err = [1 - m[k]["top1"] for k in keys]
    barh(axes[0], names, err, [S1, S2, S3], fmt="{:.2%}", xmax=max(err) * 1.3)
    axes[0].set_title("Top-1 error rate (lower is better)", loc="left", fontsize=10.5, color=INK)
    miss = [1 - m[k]["gtpa"] for k in keys]
    barh(axes[1], names, miss, [S1, S2, S3], fmt="{:.3%}", xmax=max(miss) * 1.35)
    axes[1].set_title("True pathology missing from the differential", loc="left", fontsize=10.5, color=INK)
    for ax in axes:
        ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=2))
        ax.xaxis.set_major_locator(MaxNLocator(4))
    fig.suptitle(f"Full DDXPlus test split, {m['patients']:,} patients", x=0.125, ha="left", fontsize=9.5, color=INK2, y=1.05)
    save(fig, "systems.png")


def ablation():
    a = load("ablation.json")
    if not a:
        return
    rows = sorted(a["rows"], key=lambda r: -r["top1"])
    fig, ax = plt.subplots(figsize=(9.5, 4.6))
    colors = [S1 if r["name"] == "Full expert system" else "#86b6ef" for r in rows]
    barh(ax, [r["name"] for r in rows], [r["top1"] for r in rows], colors, xmax=1.13)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0], ["0%", "25%", "50%", "75%", "100%"])
    ax.set_title(f"Ablation: top-1 accuracy ({a['patients']:,} test patients, up to 100 per pathology)", loc="left", fontsize=11, color=INK)
    save(fig, "ablation.png")


def triage_matrix():
    m = load("main.json")
    if not m:
        return
    levels = ["Emergency", "Urgent", "NonUrgent", "SelfCare"]
    lab = ["Emergency", "Urgent", "Non-urgent", "Self-care"]
    mat = [[m["triage"]["matrix"][t][p] for p in levels] for t in levels]
    rows = [sum(r) for r in mat]
    frac = [[v / rows[i] if rows[i] else 0 for v in r] for i, r in enumerate(mat)]
    cmap = LinearSegmentedColormap.from_list("seq", ["#f6f9fe"] + SEQ)
    fig, ax = plt.subplots(figsize=(6.2, 4.7))
    ax.imshow(frac, cmap=cmap, vmin=0, vmax=1)
    for i in range(4):
        for j in range(4):
            col = "white" if frac[i][j] > 0.55 else INK
            ax.text(j, i, "0" if mat[i][j] == 0 else f"{mat[i][j]:,}\n{frac[i][j]:.1%}", ha="center", va="center",
                    fontsize=8.6, color=MUTED if mat[i][j] == 0 else col)
    ax.set_xticks(range(4), lab)
    ax.set_yticks(range(4), lab)
    ax.set_xlabel("Expert-system triage")
    ax.set_ylabel("Level implied by the true pathology")
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("Triage on the full test split (row %)", loc="left", fontsize=11, color=INK)
    ax.text(-0.5, 4.05, "Right of the diagonal = under-triage (less urgent than the truth); left = over-triage",
            fontsize=7.8, color=INK2, ha="left", va="top")
    save(fig, "triage_matrix.png")


def triage_sensitivity():
    t = load("triage_sensitivity.json")
    if not t:
        return
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.4), sharex=True, gridspec_kw={"wspace": 0.3})
    for red, color, name in ((True, S1, "with red-flag rules"), (False, S2, "severity schema only")):
        rows = [r for r in t["rows"] if r["red_flag_rules"] == red and r["severity_threshold"] <= 1]
        xs = [r["severity_threshold"] for r in rows]
        for ax, key in ((axes[0], "under"), (axes[1], "over")):
            ax.plot(xs, [r[key] for r in rows], color=color, lw=2, marker="o", ms=5, label=name)
            ax.annotate(f"{rows[-1][key]:.1%}", (xs[-1], rows[-1][key]), textcoords="offset points", xytext=(6, 0),
                        fontsize=8.5, color=INK, va="center")
    axes[0].set_title("Under-triage (unsafe)", loc="left", fontsize=10.5)
    axes[1].set_title("Over-triage", loc="left", fontsize=10.5)
    for ax in axes:
        ax.set_xlabel("Severity-schema threshold on P(pathology)")
        ax.grid(color=GRID, lw=0.6)
        ax.set_ylim(bottom=0)
        ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    axes[1].legend(frameon=False, fontsize=8.5, loc="upper right")
    save(fig, "triage_sensitivity.png")


def rule_calibration():
    v = load("rule_validation.json")
    if not v:
        return
    rows = [r for r in v["rows"] if r["category"] == "diagnostic" and r.get("precision") is not None and r["cf"] > 0 and r["fires"] >= 20]
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    ax.scatter([r["cf"] for r in rows], [r["precision"] for r in rows], s=34, color=S1, edgecolor="white", lw=1.2, zorder=3)
    shown = sorted(rows, key=lambda r: r["precision"])[:4]
    for r in shown:
        ax.annotate(r["id"], (r["cf"], r["precision"]), textcoords="offset points", xytext=(5, 3), fontsize=7.8, color=INK2)
    ax.set_xlabel("Certainty factor assigned from the source")
    ax.set_ylabel("Precision on the validation sample")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.05)
    ax.grid(color=GRID, lw=0.6)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.set_title(f"Diagnostic rules: assigned CF vs empirical precision ({len(rows)} rules firing ≥ 20 times)", loc="left", fontsize=10.5)
    save(fig, "rule_calibration.png")


def consultation():
    c = load("consultation.json")
    if not c:
        return
    qs = [r["questions"] for r in c["rows"]]
    fig, ax = plt.subplots(figsize=(7.2, 3.3))
    bins = list(range(0, max(qs) + 1))
    ax.bar(bins, [qs.count(b) for b in bins], color=S1, width=0.8, edgecolor=SURFACE, linewidth=2)
    ax.grid(axis="y", color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    ax.set_xlabel("Questions asked after the chief complaint")
    ax.set_ylabel("Patients")
    s = c["summary"]
    ax.set_title(f"Simulated consultations: mean {s['questions_mean']:.1f} questions, top-1 {s['top1']:.1%}, top-3 {s['top3']:.1%}",
                 loc="left", fontsize=10.5)
    save(fig, "consultation.png")


if __name__ == "__main__":
    architecture()
    ontology()
    pipeline()
    systems()
    ablation()
    triage_matrix()
    triage_sensitivity()
    rule_calibration()
    consultation()
