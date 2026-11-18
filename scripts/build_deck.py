"""Build the presentation (docs/slides.pptx) on the UGM template with python-pptx.

Every number on the slides is read from results/*.json, kg/stats.json, data/processed/summary.json and the rule
base, as in the report. The template is the official UGM one (not part of the repository); by default it is looked
for next to the repository folder.

Usage (from the repository root):  python -I scripts/build_deck.py [--template PATH] [--copy-to PATH]
"""
import json
import pathlib
import shutil
import statistics
import sys
from collections import Counter
from copy import deepcopy

import yaml
from lxml import etree
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.opc.packuri import PackURI
from pptx.oxml.ns import qn
from pptx.util import Pt

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from ddxes import rulebase as rb  # noqa: E402
from ddxes.kb import KnowledgeBase  # noqa: E402

TEMPLATE = (pathlib.Path(sys.argv[sys.argv.index("--template") + 1]) if "--template" in sys.argv
            else ROOT.parent / "(NEW) Template Presentasi UGM 5.pptx")
OUT = ROOT / "docs" / "slides.pptx"
FIG, CROPS = ROOT / "docs" / "figures", ROOT / "docs" / "build" / "deck"

BLUE, GOLD, DARKGOLD = RGBColor(0x01, 0x41, 0x6B), RGBColor(0xFD, 0xD4, 0x02), RGBColor(0xC9, 0x9A, 0x00)
INK, MUTED, TINT = RGBColor(0x1F, 0x2D, 0x3A), RGBColor(0x5E, 0x6E, 0x7E), RGBColor(0xE6, 0xEE, 0xF5)
WHITE, CODEBG, LINE = RGBColor(0xFF, 0xFF, 0xFF), RGBColor(0xF3, 0xF6, 0xF9), RGBColor(0xC9, 0xD6, 0xE3)
AMBER, CREAM, RED, REDTINT = (RGBColor(0x9A, 0x6D, 0x00), RGBColor(0xFF, 0xF4, 0xCC), RGBColor(0xB4, 0x23, 0x18),
                              RGBColor(0xFB, 0xE4, 0xE1))
GREEN, GREENTINT = RGBColor(0x1E, 0x6E, 0x3E), RGBColor(0xE3, 0xF2, 0xE8)
BOLD, REG, LIGHT_IT, CODE, MATH = "Gama-Sans-Bold", "Gama-Sans-Regular", "Gama-Sans-Light-Italic", "Consolas", "Cambria Math"
X0, X1 = 66, 894
FONT_STEP = 1  # points added to every body font size; slide titles and section dividers keep theirs


def jload(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def fmt(n):
    return f"{n:,}"


def pct(x, d=1):
    return f"{100 * x:.{d}f}%"


# ------------------------------------------------------------------ primitives
def fs(size):
    return Pt(size + FONT_STEP)


def text(slide, x, y, w, h, paras, size=14, color=INK, font=REG, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, after=4, spacing=None, bullets=False):
    """paras: list of str or of [(text, {size, color, font, italic})] run lists."""
    tb = slide.shapes.add_textbox(Pt(x), Pt(y), Pt(w), Pt(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        for t, o in (para if isinstance(para, list) else [(para, {})]):
            r = p.add_run()
            r.text = t
            r.font.size = fs(o.get("size", size))
            r.font.name = o.get("font", font)
            r.font.color.rgb = o.get("color", color)
            if o.get("italic"):
                r.font.italic = True
        p.alignment = align
        p.space_after = Pt(after)
        if spacing:
            p.line_spacing = spacing
        if bullets:
            bullet(p)
    return tb


def bullet(p, indent=13):
    pPr = p._p.get_or_add_pPr()
    pPr.set("marL", str(Pt(indent)))
    pPr.set("indent", str(-Pt(indent)))
    clr = etree.SubElement(pPr, qn("a:buClr"))
    etree.SubElement(clr, qn("a:srgbClr")).set("val", "01416B")
    etree.SubElement(pPr, qn("a:buFont")).set("typeface", "Arial")
    etree.SubElement(pPr, qn("a:buChar")).set("char", "•")


def box(slide, x, y, w, h, fill=TINT, line=None, radius=0.06, shape=MSO_SHAPE.ROUNDED_RECTANGLE):
    s = slide.shapes.add_shape(shape, Pt(x), Pt(y), Pt(w), Pt(h))
    s.fill.solid()
    s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line
        s.line.width = Pt(1)
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = radius
    s.shadow.inherit = False
    return s


def arrow(slide, x1, y1, x2, y2, color=MUTED, width=1.25):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Pt(x1), Pt(y1), Pt(x2), Pt(y2))
    c.line.color.rgb = color
    c.line.width = Pt(width)
    etree.SubElement(c.line._get_or_add_ln(), qn("a:tailEnd")).set("type", "triangle")
    return c


def badge(slide, x, y, d, label, fill=BLUE, color=WHITE, size=14):
    s = box(slide, x, y, d, d, fill=fill, shape=MSO_SHAPE.OVAL)
    tf = s.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = label
    r.font.size, r.font.name, r.font.color.rgb = fs(size), BOLD, color
    return s


def image(slide, path, x, y, w=None, h=None, crop=None, border=False, max_px=2400):
    """Picture fitted (centred) into the w x h box; crops and very large pictures are resampled into docs/build/deck."""
    path = pathlib.Path(path)
    im = Image.open(path)
    if crop or max(im.size) > max_px:
        im = im.crop(crop) if crop else im
        im.thumbnail((max_px, max_px), Image.LANCZOS)
        CROPS.mkdir(parents=True, exist_ok=True)
        path = CROPS / f"{path.stem}{'-' + '-'.join(map(str, crop)) if crop else ''}.png"
        im.save(path, optimize=True)
    iw, ih = im.size
    if w and h:
        s = min(w / iw, h / ih)
        x, y, w, h = x + (w - iw * s) / 2, y + (h - ih * s) / 2, iw * s, ih * s
    elif w:
        h = w * ih / iw
    else:
        w = h * iw / ih
    pic = slide.shapes.add_picture(str(path), Pt(x), Pt(y), Pt(w), Pt(h))
    if border:
        pic.line.color.rgb = LINE
        pic.line.width = Pt(0.75)
    return pic, (x, y, w, h)


def table(slide, x, y, widths, rows, size=12, row_h=26, fonts=None, colors=None, aligns=None, bold_rows=()):
    gf = slide.shapes.add_table(len(rows), len(widths), Pt(x), Pt(y), Pt(sum(widths)), Pt(row_h * len(rows)))
    tbl = gf.table
    tbl.first_row, tbl.horz_banding = True, False
    for j, w in enumerate(widths):
        tbl.columns[j].width = Pt(w)
    for i, row in enumerate(rows):
        tbl.rows[i].height = Pt(row_h)
        for j, val in enumerate(row):
            cell = tbl.cell(i, j)
            cell.margin_left = cell.margin_right = Pt(6)
            cell.margin_top = cell.margin_bottom = Pt(2)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            head = i == 0
            cell.fill.fore_color.rgb = BLUE if head else (TINT if i % 2 == 0 else WHITE)
            tf = cell.text_frame
            tf.word_wrap = True
            runs = val if isinstance(val, list) else [(str(val), {})]
            p = tf.paragraphs[0]
            p.space_after = Pt(0)
            if aligns and not head:
                p.alignment = aligns[j]
            for t, o in runs:
                r = p.add_run()
                r.text = t
                r.font.size = fs(o.get("size", size))
                strong = head or i in bold_rows
                r.font.name = o.get("font", BOLD if strong else (fonts or {}).get(j, REG))
                r.font.color.rgb = o.get("color", WHITE if head else (colors or {}).get(j, INK))
                if o.get("italic"):
                    r.font.italic = True
    return gf


def code(slide, x, y, w, h, src, size=11, label=None):
    if label:
        text(slide, x, y, w, 18, [label], size=12, font=BOLD, color=BLUE)
        y, h = y + 22, h - 22
    box(slide, x, y, w, h, fill=CODEBG, line=LINE, radius=0.03)
    lines = src.strip("\n").split("\n")
    paras = [[(ln if ln else " ", {"color": MUTED if ln.lstrip().startswith("#") else INK})] for ln in lines]
    text(slide, x + 10, y + 8, w - 20, h - 14, paras, size=size, font=CODE, after=0, spacing=1.0)


def stat(slide, x, y, w, h, number, label, fill=TINT, num_color=BLUE, num_size=30):
    box(slide, x, y, w, h, fill=fill)
    text(slide, x + 14, y + 8, w - 28, h * 0.5, [number], size=num_size, font=BOLD, color=num_color,
         anchor=MSO_ANCHOR.BOTTOM, after=0)
    text(slide, x + 14, y + 12 + h * 0.5, w - 28, h * 0.5 - 16, [label], size=11.5, color=MUTED, after=0)


def card(slide, x, y, w, h, head, body, fill=TINT, head_color=BLUE, size=12.5, head_size=14.5):
    box(slide, x, y, w, h, fill=fill)
    text(slide, x + 14, y + 10, w - 28, h - 18, [[(head, {"font": BOLD, "color": head_color, "size": head_size})]]
         + [b for b in (body if isinstance(body, list) else [body])], size=size, after=5, spacing=1.0)


def notes(slide, s):
    slide.notes_slide.notes_text_frame.text = s


def set_lines(shape, lines, fonts=None):
    tf = shape.text_frame
    paras = list(tf.paragraphs)
    while len(paras) < len(lines):
        paras[-1]._p.addnext(deepcopy(paras[-1]._p))
        paras = list(tf.paragraphs)
    for i, p in enumerate(paras):
        if i >= len(lines):
            p._p.getparent().remove(p._p)
            continue
        runs = p.runs
        runs[0].text = lines[i]
        for r in runs[1:]:
            r._r.getparent().remove(r._r)
        if fonts and fonts.get(i):
            runs[0].font.name = fonts[i]


class Deck:
    def __init__(self):
        if not TEMPLATE.exists():
            sys.exit(f"UGM template not found: {TEMPLATE} (pass --template PATH)")
        self.prs = Presentation(TEMPLATE)
        layouts = {lay.name: lay for lay in self.prs.slide_layouts}
        self.title_only, self.section = layouts["Title Only"], layouts["Section Header"]
        self.cover, *self.examples, self.closing = list(self.prs.slides)

    def _sld_id(self, slide):
        return next(i for i in self.prs.slides._sldIdLst if self.prs.part.related_part(i.rId) is slide.part)

    def content(self, title, note):
        s = self.prs.slides.add_slide(self.title_only)
        t = s.shapes.title
        t.left, t.top, t.width, t.height = Pt(X0), Pt(24), Pt(706), Pt(62)
        t.text_frame.text = title
        t.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        t.text_frame.word_wrap = True
        for r in t.text_frame.paragraphs[0].runs:
            r.font.size, r.font.name, r.font.color.rgb = Pt(28), BOLD, BLUE
        notes(s, note)
        return s

    def divider(self, number, title, desc, note):
        s = self.prs.slides.add_slide(self.section)
        t = s.shapes.title
        t.left, t.top, t.width, t.height = Pt(251), Pt(118), Pt(620), Pt(200)
        tf = t.text_frame
        tf.vertical_anchor = MSO_ANCHOR.BOTTOM
        tf.word_wrap = True
        r = tf.paragraphs[0].add_run()
        r.text = number
        r.font.size, r.font.name, r.font.color.rgb = Pt(66), BOLD, DARKGOLD
        r = tf.add_paragraph().add_run()
        r.text = title
        r.font.size, r.font.name, r.font.color.rgb = Pt(38), BOLD, BLUE
        body = s.placeholders[10]
        body.left, body.top, body.width, body.height = Pt(251), Pt(336), Pt(560), Pt(110)
        body.text_frame.text = desc
        body.text_frame.word_wrap = True
        for r in body.text_frame.paragraphs[0].runs:
            r.font.size, r.font.name, r.font.color.rgb = Pt(17), REG, INK
        notes(s, note)
        return s

    def save(self, path):
        for s in self.examples:  # drop the template's example slides only now (part names stay unique)
            sid = self._sld_id(s)
            self.prs.part.drop_rel(sid.rId)
            self.prs.slides._sldIdLst.remove(sid)
        lst = self.prs.slides._sldIdLst
        sid = self._sld_id(self.closing)
        lst.remove(sid)
        lst.append(sid)
        for i, slide in enumerate(self.prs.slides, 1):
            slide.part.partname = PackURI(f"/ppt/slides/slide{i}.xml")
        self.prs.save(path)


# ------------------------------------------------------------------ data
RULES, _ = rb.load_yaml(ROOT / "knowledge" / "rules.yaml")
SOURCES = yaml.safe_load(open(ROOT / "knowledge" / "rules.yaml", encoding="utf-8"))["sources"]
BY_ID = {r.id: r for r in RULES}
CITED = {r.source for r in RULES}
KG, SUMMARY = jload("kg/stats.json"), jload("data/processed/summary.json")
MAIN, ABL, SENS = jload("results/main.json"), jload("results/ablation.json"), jload("results/triage_sensitivity.json")
RV, CONS, CURVE = jload("results/rule_validation.json"), jload("results/consultation.json"), jload("results/consultation_curve.json")
EARLY = jload("results/early_triage.json")
INTAKE = jload("results/intake.json")          # the optional free-text intake (scripts/evaluate_intake.py)
IL = INTAKE["languages"]
N_PATH = len(jload("data/processed/pathologies.json"))
INTAKE_SCOPE = ("one per pathology" if INTAKE["patients"] >= N_PATH else
                f"one for each of the first {INTAKE['patients']} pathologies; the daily token limit of the free tier "
                "ended the run there")
ASSERTED = sum(KG[k] for k in ("ontology", "kb", "rules", "stats", "cases"))
CAT, FID = Counter(r.category for r in RULES), Counter(r.fidelity for r in RULES)
SRC_TYPES = Counter(SOURCES[s]["type"] for s in CITED)
AB = {r["name"]: r for r in ABL["rows"]}
C, C0 = CONS["summary"], CONS["without_rule_out"]
C99 = next(r for r in CURVE["rows"] if abs(r["confidence"] - 0.99) < 1e-9)
POS = [r for r in RV["rows"] if r["category"] == "diagnostic" and r.get("precision") is not None and r["cf"] > 0]
OFF = {r["red_flag_rules"]: r for r in SENS["rows"] if r["severity_threshold"] > 1}
T = MAIN["triage"]


def query_rows(name):
    import csv
    out = {}
    for folder in ("results", "results-no-inference"):
        with open(ROOT / "queries" / folder / f"{name}.csv", encoding="utf-8") as f:
            data = list(csv.reader(f))
        out[folder] = data[1][0] if data[0] == ["ask"] else str(len(data) - 1)
    return out["results"], out["results-no-inference"]


def build(kb):
    d = Deck()

    # 1 ------------------------------------------------------------------------ cover
    shapes = {s.name: s for s in d.cover.shapes}
    set_lines(shapes["TextBox 338"], ["DDX-ES: An Explainable", "Knowledge-Graph Expert", "System for Diagnosis"])
    sub = shapes["TextBox 9"]
    sub.width = Pt(380)
    set_lines(sub, ["Differential diagnosis and triage on DDXPlus with",
                    "cited IF–THEN rules, OWL 2 RL and PROV-O",
                    "Muhammad Zidan Arsyad · [group members 2–4]",
                    "Sistem Pakar Lanjut · Magister Kecerdasan Artifisial",
                    "Departemen Ilmu Komputer dan Elektronika · UGM · 2026"], fonts={2: BOLD})
    notes(d.cover, "Good morning. Our final project is DDX-ES, an expert system that takes a patient's answers to a "
                   "symptom questionnaire and returns a differential diagnosis, a triage level and recommended next "
                   "steps, and explains every one of these conclusions with the rules that fired, the facts they "
                   "matched and the published guideline each rule encodes. The knowledge, the rules and the reasoning "
                   "trail are all stored in one RDF/OWL knowledge graph.")

    # 2 ------------------------------------------------------------------------ agenda
    s = d.content("Assignment components and deliverables",
                  "The assignment asks for four components and three deliverables. Each card maps one component to the "
                  "slides that answer it. The fourth component, machine learning or a language model, is optional. We "
                  "use a naive-Bayes classifier as a baseline and in a hybrid mode, and a language model only to read "
                  "a free-text description into the questionnaire, so the expert system remains the core. The report, "
                  "this deck and the repository with its README are the deliverables.")
    cards = [("Ontology + knowledge graph",
              f"Hand-written OWL 2 ontology; {fmt(ASSERTED)} asserted triples in six Turtle files; the OWL 2 RL "
              f"closure adds {fmt(KG['inferred'])}.", "Slide 8"),
             ("IF–THEN rules with references",
              f"{len(RULES)} rules from {len(CITED)} published sources — guidelines, case definitions, criteria — "
              "each with its DOI and mapping fidelity.", "Slides 5–7"),
             ("Reasoning and explanations",
              "Forward chaining, abduction, certainty factors, safety-first triage and a consultation; HOW, WHY, "
              "WHY NOT and PROV-O provenance.", "Slides 9–17"),
             ("Machine learning and LLM (optional)",
              f"A naive-Bayes baseline and a hybrid mode on the same counts ({fmt(MAIN['patients'])} test "
              "patients); a language-model intake that reads free text in English or Bahasa Indonesia.",
              "Slides 12, 18–21")]
    cw = (X1 - X0 - 3 * 16) / 4
    for i, (head, body, ref) in enumerate(cards):
        x = X0 + i * (cw + 16)
        box(s, x, 108, cw, 304)
        badge(s, x + 16, 124, 34, str(i + 1))
        text(s, x + 60, 133, cw - 76, 18, [f"COMPONENT {i + 1}"], size=10.5, font=BOLD, color=MUTED)
        text(s, x + 16, 172, cw - 32, 54, [head], size=15.5, font=BOLD, color=BLUE, after=0)
        text(s, x + 16, 232, cw - 32, 140, [body], size=12.5, after=0, spacing=1.05)
        text(s, x + 16, 382, cw - 32, 18, [ref], size=11.5, font=BOLD, color=AMBER)
    box(s, X0, 426, X1 - X0, 50, fill=CODEBG, line=LINE, radius=0.08)
    text(s, X0 + 16, 430, X1 - X0 - 32, 42,
         [[("Deliverables: ", {"font": BOLD, "color": BLUE}),
           ("report (PDF, FMIPA UGM format) · this presentation · repository ddx-expert-system with a README on how "
            "to run it — 120 tests, every result reproducible from the raw data", {})]],
         size=12, anchor=MSO_ANCHOR.MIDDLE)

    # 3 ------------------------------------------------------------------- the problem
    s = d.content("Problem statement and objectives",
                  "A symptom checker asks questions, ranks the possible diseases and says how urgently to seek care. "
                  "Learned models can be accurate, but they cannot say which facts and which guideline support a "
                  "decision, and the dangerous error is under-triage: assigning an emergency patient a lower urgency. "
                  "Classic expert systems such as MYCIN and INTERNIST-I explain by construction, but their knowledge "
                  "was hand-coded and hard to audit. Our objectives combine the two approaches: auditable knowledge in "
                  "a graph, rules with sources, explainable reasoning, evaluated on a large benchmark.")
    probs = [("Ranking and urgency. ", "From a few answers, propose a differential diagnosis and a triage level "
              "(Emergency, Urgent, Non-urgent, Self-care)."),
             ("Limited explainability. ", "A learned model cannot say which facts and which guideline support its "
              "answer; clinicians cannot check it."),
             ("Patient safety. ", "Under-triage — assigning an emergency a lower urgency — is the most serious error."),
             ("Classic expert systems explain ", "(MYCIN, INTERNIST-I), but hand-coded knowledge without sources is "
              "hard to audit and maintain.")]
    text(s, X0, 108, 440, 360, [[(a, {"font": BOLD, "color": BLUE}), (b, {})] for a, b in probs],
         size=14, after=12, bullets=True, spacing=1.05)
    box(s, 536, 108, X1 - 536, 360, fill=TINT)
    text(s, 552, 120, X1 - 568, 24, ["Objectives of DDX-ES"], size=15, font=BOLD, color=BLUE)
    objs = ["Represent the domain, the rules (as data, each with its source), the cases and the provenance in one "
            "RDF/OWL 2 knowledge graph",
            f"Encode {len(RULES)} IF–THEN rules from {len(CITED)} published sources, with certainty factors and "
            "mapping fidelity",
            "Reason: forward chaining, abduction, certainty factors, safety-first triage, interactive consultation",
            "Explain every conclusion: HOW, WHY, WHY NOT, proof trees, PROV-O",
            f"Evaluate on the {fmt(MAIN['patients'])} DDXPlus test patients against ML and published agents"]
    for i, o in enumerate(objs):
        y = 154 + i * 61
        badge(s, 552, y, 22, str(i + 1), size=10)
        text(s, 584, y + 1, X1 - 600, 58, [o], size=12, after=0, spacing=1.0)

    # 4 -------------------------------------------------------------------- DDXPlus
    s = d.content("The domain: DDXPlus (NeurIPS 2022)",
                  "DDXPlus is a benchmark for automatic diagnosis. It publishes an expert knowledge base of 49 "
                  "pathologies and 223 questions, and about 1.3 million synthetic patients generated from it by a "
                  "commercial rule-based system, each with answers, the true pathology and a ranked differential. We "
                  "use the knowledge base as expert knowledge, the validation split for calibration counts and the "
                  "test split only for evaluation. The patients are synthetic, which matters for the limitations.")
    box(s, X0, 108, 460, 84)
    text(s, X0 + 14, 116, 432, 72,
         ["Fansi Tchango, A., Goel, R., Wen, Z., Martel, J. and Ghosn, J. (2022)",
          [("DDXPlus: A new dataset for automatic medical diagnosis.", {"font": BOLD, "color": BLUE, "size": 13.5})],
          "NeurIPS 35, Datasets and Benchmarks · data CC BY 4.0, figshare"],
         size=11.5, color=MUTED, after=3)
    facts = [("Knowledge base — ", "49 pathologies with symptoms, antecedents (risk factors), ICD-10 code and "
              "severity 1–5; 888 links reviewed by physicians."),
             ("Questions — ", "yes/no, categorical (0–10 scales) and multi-choice (e.g. pain location)."),
             ("Patients — ", "age, sex, answers, true pathology, reference differential."),
             ("How we use it — ", f"validation split ({fmt(SUMMARY['validation_patients'])}) for calibration counts; "
              f"test split ({fmt(SUMMARY['test_patients'])}) only for evaluation.")]
    text(s, X0, 206, 460, 270, [[(a, {"font": BOLD, "color": BLUE}), (b, {})] for a, b in facts],
         size=13, after=8, bullets=True, spacing=1.0)
    tiles = [("49", "pathologies: respiratory, cardiac, ENT, emergencies"), ("223", "questions: 110 symptoms, "
             "113 antecedents"), ("1.3 M", "synthetic patients in three splits"),
             (fmt(SUMMARY["test_patients"]), "test patients used for evaluation")]
    for i, (num, lab) in enumerate(tiles):
        stat(s, 552 + (i % 2) * 175, 108 + (i // 2) * 128, 167, 116, num, lab, num_size=28)
    text(s, 552, 374, X1 - 552, 100,
         ["Synthetic but clinically structured: the patients were generated from the same knowledge base, so their "
          "statistics describe that knowledge base, not real-world prevalence."], size=11.5, color=MUTED)

    # 5 ------------------------------------------------------------ three sources
    s = d.content("Integration of the knowledge sources",
                  "The knowledge has three sources. First, the DDXPlus expert knowledge base: which symptoms and risk "
                  "factors each pathology can produce, with severity and ICD-10 chapter. Second, published clinical "
                  "criteria and guidelines, from which we wrote the IF-THEN rules; every DOI was resolved against "
                  "Crossref, and the sources without DOI were checked on the publisher's page. Third, counts from the "
                  "validation split, which give a likelihood ratio for every link of the knowledge base. A build "
                  "script turns all three into Turtle files.")
    names = {"ClinicalGuideline": "guidelines", "ReviewArticle": "reviews", "DiagnosticCriteria": "criteria",
             "ClinicalStudy": "clinical studies", "ClinicalDecisionRule": "decision rules",
             "CaseDefinition": "case definitions"}
    mix = ", ".join(f"{n} {names[k]}" for k, n in SRC_TYPES.most_common() if k in names)
    if SRC_TYPES.get("ExpertKnowledgeBase"):
        mix += " and the DDXPlus paper"
    cols = [("1", "Expert knowledge base", "DDXPlus, physician-reviewed",
             [f"49 pathologies · 223 questions · {SUMMARY['kb_links']} profile links",
              "Severity 1–5 and ICD-10 chapter of every pathology",
              "→ disease profiles, set covering, severity schema"]),
            ("2", "Published criteria", f"{len(CITED)} sources, DOIs checked",
             [f"{len(RULES)} IF–THEN rules: {mix}",
              "Wells, Sampson, CRB-65, Centor/IDSA, WHO, CDC, NICE NG12, DSM-5, ICHD-3, ESC, GOLD, GINA",
              "→ concepts, scores, diagnoses, triage, advice"]),
            ("3", "Calibration counts", f"{fmt(SUMMARY['validation_patients'])} validation patients",
             [f"{fmt(SUMMARY['feature_pairs'])} (feature, pathology) pairs counted",
              "Likelihood ratio and CF for every knowledge-base link (Laplace smoothing)",
              "→ belief aggregation, absent-evidence rules"])]
    cw = (X1 - X0 - 2 * 18) / 3
    for i, (num, head, sub_, lines) in enumerate(cols):
        x = X0 + i * (cw + 18)
        box(s, x, 106, cw, 286)
        badge(s, x + 14, 118, 28, num, size=12)
        text(s, x + 50, 116, cw - 62, 40, [[(head, {"font": BOLD, "color": BLUE, "size": 15})],
                                           [(sub_, {"font": LIGHT_IT, "color": AMBER, "size": 11.5})]], after=1)
        text(s, x + 14, 168, cw - 28, 220, lines, size=12, after=8, bullets=True, spacing=1.0)
        arrow(s, x + cw / 2, 394, x + cw / 2, 420, color=DARKGOLD, width=2)
    box(s, X0, 422, X1 - X0, 52, fill=BLUE)
    text(s, X0 + 16, 426, X1 - X0 - 32, 44,
         [[("Knowledge graph (RDF / OWL 2): ", {"font": BOLD, "color": GOLD}),
           (f"build_kg.py writes six Turtle files — {fmt(ASSERTED)} asserted triples; OWL 2 RL entails "
            f"{fmt(KG['inferred'])} more", {"color": WHITE})]], size=13, anchor=MSO_ANCHOR.MIDDLE)

    # 6 ------------------------------------------------------------- Wells mapping
    s = d.content("From a guideline to rules: the Wells score",
                  "Each criterion was translated item by item into DDXPlus questions. The Wells score for pulmonary "
                  "embolism shows the typical situation: most items map to a question, some only approximately, and "
                  "some cannot be asked at all, such as the heart rate. Every rule records this as its mapping "
                  "fidelity, with a note on what is missing, so the knowledge base documents how closely each rule "
                  "follows its source. Certainty factors follow a fixed scale tied to the strength of the source.")
    rows = [["Wells item", "Points", "DDXPlus question(s)", "Mapping"],
            ["Clinical signs of DVT", "3", "E_151 swelling, E_152 in calf, popliteal fossa or thigh", "proxy"],
            ["Heart rate > 100/min", "1.5", "not asked", "missing"],
            ["Immobilisation or recent surgery", "1.5", "E_110 immobile > 3 days; E_196 surgery last month", "faithful"],
            ["Previous DVT or PE", "1.5", "E_109 deep vein thrombosis", "faithful"],
            ["Haemoptysis", "1", "E_45 coughing up blood", "faithful"],
            ["Malignancy", "1", "E_34 active cancer; E_37 metastatic cancer", "faithful"],
            ["PE most likely diagnosis", "3", "circular for a diagnostic system", "missing"]]
    colmap = {"proxy": BLUE, "missing": RED, "faithful": GREEN}
    rows = [rows[0]] + [r[:3] + [[(r[3], {"font": BOLD, "color": colmap[r[3]]})]] for r in rows[1:]]
    table(s, X0, 108, [150, 50, 250, 72], rows, size=11, row_h=40,
          aligns=[PP_ALIGN.LEFT, PP_ALIGN.CENTER, PP_ALIGN.LEFT, PP_ALIGN.LEFT])
    rx = X0 + 540
    text(s, rx, 108, X1 - rx, 20, ["Mapping fidelity of all rules"], size=13, font=BOLD, color=BLUE)
    for i, (k, lab, col, tint) in enumerate([("faithful", "faithful: every item has a question", GREEN, GREENTINT),
                                              ("partial", "partial: some items cannot be asked", AMBER, CREAM),
                                              ("proxy", "proxy: an item approximated by a question", BLUE, TINT)]):
        y = 134 + i * 62
        box(s, rx, y, X1 - rx, 54, fill=tint)
        text(s, rx + 12, y + 6, 70, 42, [str(FID[k])], size=24, font=BOLD, color=col, anchor=MSO_ANCHOR.MIDDLE)
        text(s, rx + 80, y + 6, X1 - rx - 92, 42, [lab], size=11.5, anchor=MSO_ANCHOR.MIDDLE, after=0)
    box(s, rx, 330, X1 - rx, 144, fill=CODEBG, line=LINE)
    text(s, rx + 12, 338, X1 - rx - 24, 132,
         [[("Certainty factors from the source", {"font": BOLD, "color": BLUE})],
          "0.8–0.9  case definition or criteria met", "0.5–0.7  strongly suggestive pattern",
          "0.2–0.4  supportive feature or risk factor", "< 0  evidence against (influenza without fever: −0.4)"],
         size=11.5, after=3)

    # 7 ------------------------------------------------------------- rule base
    s = d.content(f"The rule base: {len(RULES)} rules in five categories",
                  "The rules come in five categories. Abstraction rules turn raw answers into clinical concepts, "
                  "scoring rules add points to clinical scores, diagnostic rules add belief to pathologies, triage "
                  "rules propose urgency levels and recommendation rules propose next steps. The categories form "
                  "strata: a rule only fires after the rules that produce the concepts and scores it reads. Below is "
                  "the Wells rule exactly as the engine renders it from the graph.")
    ex = [("Abstraction", "abstraction", "IF chest pain AND pain increased by deep breathing THEN pleuritic chest pain"),
          ("Scoring", "scoring", "IF surgery in the last month OR immobile > 3 days THEN Wells score += 1.5"),
          ("Diagnostic", "diagnostic", "IF allergen contact AND at least 2 of {skin, respiratory, circulatory, GI} "
           "THEN anaphylaxis (CF 0.9)"),
          ("Triage", "triage", "IF P(anaphylaxis) ≥ 0.5 THEN Emergency (CF 0.95)"),
          ("Recommendation", "recommendation", "IF P(PE) ≥ 0.4 AND Wells score > 4 THEN CT pulmonary angiography")]
    rows = [["Category", "Rules", "Example"]] + [[a, str(CAT[k]), c] for a, k, c in ex]
    table(s, X0, 106, [140, 60, 628], rows, size=12, row_h=30, fonts={0: BOLD}, colors={0: BLUE},
          aligns=[PP_ALIGN.LEFT, PP_ALIGN.CENTER, PP_ALIGN.LEFT])
    pe = BY_ID["D-PE-1"]
    code(s, X0, 302, X1 - X0, 112,
         f"# rule {pe.id}, rendered from the knowledge graph (source: {kb.short_citation(pe.source)}, "
         f"fidelity: {pe.fidelity})\n" + kb.rule_text(pe).replace(" AND (", "\n  AND (").replace(" THEN ", "\nTHEN "),
         size=11)
    text(s, X0, 424, X1 - X0, 50,
         [[("Every rule: ", {"font": BOLD, "color": BLUE}),
           ("identifier · premise (AND, OR, at least n of, NOT, scales, age, sex, concepts, scores, beliefs) · "
            "consequents · certainty factor · source with DOI · fidelity · note. Validated on load: references, "
            "strata, no dependency cycles.", {})]], size=12)

    # 8 ------------------------------------------------------------------ the knowledge graph
    s = d.content("The knowledge graph: ontology, rules, OWL 2 RL",
                  "All the knowledge is stored in one RDF graph with a hand-written OWL 2 ontology in three parts: the "
                  "domain (pathologies, evidences, answer values, patients), the rule vocabulary (rules are prov:Plan, "
                  "with condition trees, consequents, certainty factors and sources) and the provenance model. The "
                  "rules are represented as data: they are written in YAML, stored as RDF, and the engine reads them "
                  "back from the graph; the build checks that the round trip is lossless. The engine depends on the "
                  "OWL 2 RL closure: the red-flag rules, the disease families and the 888 disease profiles it uses are "
                  "entailed, not asserted. Query q11 illustrates this: with the closure, pulmonary embolism is the "
                  "only pathology that explains every symptom of patient T002456; without it, none is found.")
    _, (_, iy, _, ih) = image(s, FIG / "ontology.png", X0, 104, w=420, border=True)
    by = iy + ih + 12
    text(s, X0, by, 420, 20, [[("Rule storage: ", {"font": BOLD, "color": BLUE}),
                               ("YAML → RDF, read back by the engine; round trip checked", {})]], size=11.5, after=0)
    code(s, X0, by + 24, 420, 476 - by - 24, """:Rule_D-PE-1 a :DiagnosticRule ;
  :premise [ a :AllOf ; :operands ( … ) ] ;
  :asserts [ a :DiagnosisConsequent ;
             :concludesPathology :PulmonaryEmbolism ] ;
  :certaintyFactor 0.7 ; dct:source :Src_wells2000 .""", size=10)
    rx = X0 + 440
    files = [["File", "Content", "Triples"],
             ["ontology.ttl", "TBox, written by hand", fmt(KG["ontology"])],
             ["kb.ttl", "49 pathologies, 223 questions, 888 links", fmt(KG["kb"])],
             ["rules.ttl", f"{len(RULES)} rules with their sources", fmt(KG["rules"])],
             ["stats.ttl", "calibration counts", fmt(KG["stats"])],
             ["cases.ttl", "980 demo patients", fmt(KG["cases"])],
             ["inferred.ttl", "OWL 2 RL closure (owlrl)", fmt(KG["inferred"])]]
    table(s, rx, 104, [96, 222, 70], files, size=11, row_h=26, fonts={0: CODE},
          aligns=[PP_ALIGN.LEFT, PP_ALIGN.LEFT, PP_ALIGN.RIGHT])
    text(s, rx, 298, X1 - rx, 22, ["What OWL 2 RL reasoning adds"], size=13.5, font=BOLD, color=BLUE)
    rows = [["SPARQL query", "With", "Without"]]
    for q, label in (("q05-red-flag-rules", "q05  rules entailed as :RedFlagRule"),
                     ("q06-entailed-pathology-classes", "q06  diseases with entailed classes"),
                     ("q11-set-covering", "q11  pathologies explaining all symptoms")):
        a, b = query_rows(q)
        rows.append([label, a, b])
    table(s, rx, 324, [244, 72, 72], rows, size=11, row_h=26, fonts={1: BOLD},
          aligns=[PP_ALIGN.LEFT, PP_ALIGN.CENTER, PP_ALIGN.CENTER])
    text(s, rx, 434, X1 - rx, 42, [f"{fmt(KG['inferred'])} entailed triples, among them the 888 :characterizedBy "
                                   "links the engine reads as disease profiles."], size=11, color=MUTED, after=0)

    # 9 ------------------------------------------------------------------ four phases
    s = d.content("One inference run in four phases",
                  "One run over a case has four phases. Forward chaining: abstraction, scoring and diagnostic rules "
                  "fire in strata, with conflict resolution and refraction, in three-valued logic so that the same "
                  "engine handles complete records and live consultations. Abduction: set covering keeps only the "
                  "pathologies whose profile explains every reported symptom. Aggregation: rule CFs and knowledge-base "
                  "links are combined as a sum of log likelihood ratios with the prior. Finally triage and "
                  "recommendation rules read the resulting beliefs.")
    image(s, FIG / "pipeline.png", X0, 102, w=X1 - X0)
    phases = [("1  Forward chaining", "Recognise–act cycle over strata; agenda ordered by salience, specificity, "
               "rule order; refraction. True / false / unknown: closed world for records, open world for "
               "consultations."),
              ("2  Abduction", "Parsimonious set covering (Reggia et al., 1983): a pathology stays a candidate only if "
               "its profile explains every reported symptom."),
              ("3  Aggregation", "Rule CFs and knowledge-base links (LR from counts, absent evidence too) summed as "
               "log likelihood ratios with the prior; softmax over candidates."),
              ("4  Triage, advice", "Red-flag rules and the severity schema propose levels; the most urgent with "
               "CF ≥ 0.5 is selected. Recommendation rules propose next steps.")]
    cw = (X1 - X0 - 3 * 14) / 4
    for i, (head, body) in enumerate(phases):
        x = X0 + i * (cw + 14)
        card(s, x, 318, cw, 158, head, body, size=11.5, head_size=13.5)

    # 10 ------------------------------------------------------------- CF as LR
    s = d.content("Certainty factors as likelihood ratios",
                  "MYCIN combines certainty factors with a simple function. Heckerman showed in 1986 that a CF can be "
                  "read as a transformed likelihood ratio, and that combining CFs then equals multiplying likelihood "
                  "ratios. This had practical consequences: with dozens of strong contributions, several hypotheses "
                  "reached CF 1.0 within floating-point precision, and then the prior alone decided — every "
                  "stable-angina patient of the demo sample was ranked as unstable angina. Computing the same calculus "
                  "as a sum of log likelihood ratios removed the problem.")
    lw = 420
    box(s, X0, 106, lw, 370, fill=TINT)
    text(s, X0 + 16, 116, lw - 32, 24, ["Probabilistic interpretation of CFs"], size=15, font=BOLD, color=BLUE)
    eqs = [("Parallel combination (Shortliffe & Buchanan, 1975)", "combine(a, b) = a + b (1 − a)   for a, b ≥ 0"),
           ("Heckerman (1986): a CF is a likelihood ratio λ", "CF = (λ − 1) / λ  if λ ≥ 1,   CF = λ − 1  if λ < 1"),
           ("so combining CFs multiplies likelihood ratios", "log odds(h | e₁…eₙ) = log odds(h) + Σᵢ log λᵢ")]
    for i, (lab, eq) in enumerate(eqs):
        y = 152 + i * 82
        text(s, X0 + 16, y, lw - 32, 20, [lab], size=11.5, color=MUTED)
        box(s, X0 + 16, y + 24, lw - 32, 42, fill=WHITE, line=LINE, radius=0.1)
        text(s, X0 + 28, y + 24, lw - 56, 42, [eq], size=14, font=MATH, anchor=MSO_ANCHOR.MIDDLE, after=0)
    text(s, X0 + 16, 404, lw - 32, 64, ["DDX-ES shows the user CF(exp Σ log λ); a test checks it against the "
                                        "classic formula wherever that does not saturate."], size=11.5)
    rx = X0 + lw + 24
    text(s, rx, 112, X1 - rx, 24, ["The saturation problem"], size=15, font=BOLD, color=RED)
    text(s, rx, 142, X1 - rx, 150,
         ["With a few dozen strong contributions the classic formula reaches CF = 1.0 for several hypotheses.",
          "Stable and unstable angina (nested profiles) both hit 1.0 — the prior decided, and every stable-angina "
          "patient of the demo sample became unstable angina."], size=12.5, after=8, bullets=True)
    stat(s, rx, 300, (X1 - rx - 14) / 2, 110, "96.9%", "top-1, classic combination (demo patients)", fill=REDTINT,
         num_color=RED)
    stat(s, rx + (X1 - rx + 14) / 2, 300, (X1 - rx - 14) / 2, 110, "99.3%", "top-1, sum of log likelihood ratios",
         fill=GREENTINT, num_color=GREEN)
    text(s, rx, 420, X1 - rx, 54, ["In log space the calculus does not saturate: evidence still counts at high belief."],
         size=12, font=BOLD, color=BLUE)

    # 11 ------------------------------------------------------------- triage
    s = d.content("Triage policy and recommendations",
                  "Triage combines two kinds of knowledge. Red-flag rules come from the guidelines and do not depend on "
                  "the diagnosis: anaphylaxis, suspected coronary syndrome, pulmonary embolism, Ebola after travel. "
                  "The severity schema is a rule over the graph: every hypothesis with probability at least 0.2 "
                  "proposes the level of its DDXPlus severity. The most urgent level proposed with CF at least 0.5 "
                  "is selected: we accept over-triage to avoid under-triage. Recommendation rules then propose next "
                  "steps, each with its source.")
    cw = (X1 - X0 - 2 * 16) / 3
    t_ex = [BY_ID[i] for i in ("T-ANA", "T-ACS", "T-PE", "T-EBOLA") if i in BY_ID]
    card(s, X0, 106, cw, 250, f"Red-flag rules ({CAT['triage']})",
         ["From the guidelines; independent of the diagnosis."]
         + [[(f"{r.id} ", {"font": BOLD, "color": BLUE}), (r.name, {})] for r in t_ex], size=11.5)
    sx = X0 + cw + 16
    box(s, sx, 106, cw, 250, fill=TINT)
    text(s, sx + 14, 116, cw - 28, 40, [[("Severity schema", {"font": BOLD, "color": BLUE, "size": 14.5})],
                                        "every hypothesis with P ≥ 0.2 proposes:"], size=11.5, after=4)
    levels = [("1", "Emergency", RED), ("2", "Urgent", AMBER), ("3–4", "Non-urgent", BLUE), ("5", "Self-care", GREEN)]
    for i, (sev, lev, col) in enumerate(levels):
        y = 172 + i * 44
        box(s, sx + 14, y, 64, 34, fill=WHITE, line=LINE, radius=0.15)
        text(s, sx + 14, y, 64, 34, [f"sev. {sev}"], size=11, font=BOLD, color=MUTED, align=PP_ALIGN.CENTER,
             anchor=MSO_ANCHOR.MIDDLE, after=0)
        arrow(s, sx + 84, y + 17, sx + 110, y + 17, color=MUTED)
        box(s, sx + 116, y, cw - 130, 34, fill=col, radius=0.15)
        text(s, sx + 116, y, cw - 130, 34, [lev], size=12, font=BOLD, color=WHITE, align=PP_ALIGN.CENTER,
             anchor=MSO_ANCHOR.MIDDLE, after=0)
    px = X0 + 2 * (cw + 16)
    card(s, px, 106, cw, 250, "Level selection policy",
         ["The most urgent level proposed with CF ≥ 0.5 is selected.",
          "Over-triage is accepted in order to avoid under-triaging emergencies.",
          "The explanation names the rule or the hypothesis behind the level."], size=11.5)
    r_ex = [BY_ID[i] for i in ("R-PE-HIGH", "R-ANA", "R-ACS") if i in BY_ID]
    box(s, X0, 370, X1 - X0, 104, fill=CODEBG, line=LINE)
    text(s, X0 + 16, 378, X1 - X0 - 32, 92,
         [[(f"Recommendation rules ({CAT['recommendation']}): ", {"font": BOLD, "color": BLUE}),
           ("next steps with their sources, e.g.", {})]]
         + [[(f"{r.id}  ", {"font": BOLD, "color": BLUE}), (f"{r.name} — {kb.short_citation(r.source)}", {})]
            for r in r_ex], size=12, after=3)

    # 12 ------------------------------------------------------------- consultation
    s = d.content("Interactive consultation by backward chaining",
                  "In a consultation the system starts, like the DDXPlus platform, from age, sex and the chief "
                  "complaint, and runs the whole inference after every answer. The next question is chosen by "
                  "backward chaining from goals: the leading hypotheses and any severe one still possible. For each "
                  "goal it collects the unknown conditions of its rules and the unasked questions of its profile, and "
                  "asks the question with the largest expected information gain. The WHY facility names the goal, "
                  "the rule and the source. The rule-out phase was added after the evaluation showed it was needed. "
                  "A consultation can also start from a description in ordinary sentences, in English or Bahasa "
                  "Indonesia: a language model maps it onto the questionnaire, every finding must quote the "
                  "description, the user confirms the reading, and the consultation asks only what is still open.")
    steps = [("Start", "age, sex and the chief complaint, or a free-text description read by a language model "
                       "(open world: the rest is unknown)"),
             ("Goals", "the five leading hypotheses + every severe one with P ≥ 0.02"),
             ("Candidates", "unknown conditions of the goals' rules; unasked questions of their profiles"),
             ("Choose", "largest expected information gain, weighted up for guideline rules and severe goals"),
             ("Ask and explain", "WHY: goal, rule and source behind the question; HOW and triage at any time"),
             ("Stop or rule out", "at P ≥ 0.95, first ask the most specific question of each severe hypothesis still "
              "possible (P ≥ 10⁻⁴)")]
    for i, (head, body) in enumerate(steps):
        y = 106 + i * 62
        last = i == len(steps) - 1
        badge(s, X0, y + 4, 30, str(i + 1), fill=RED if last else BLUE, size=12)
        if i < len(steps) - 1:
            arrow(s, X0 + 15, y + 36, X0 + 15, y + 64, color=LINE, width=1.5)
        text(s, X0 + 44, y, 420, 58, [[(head, {"font": BOLD, "color": RED if last else BLUE, "size": 13.5})], body],
             size=12, after=1, spacing=1.0)
    box(s, 548, 106, X1 - 548, 368, fill=TINT)
    text(s, 564, 118, X1 - 580, 24, ["Motivation for the rule-out phase"], size=15, font=BOLD, color=BLUE)
    text(s, 564, 150, X1 - 580, 150,
         [f"Stopping at P ≥ 0.95 alone left {pct(C0['urgent_missed'])} of the urgent patients with a non-urgent "
          "triage: Ebola and croup were improbable at the start, so travel to West Africa or stridor was never asked.",
          "Red-flag rules could not help: their questions had not been asked either."], size=12, after=8, bullets=True)
    stat(s, 564, 330, (X1 - 580 - 12) / 2, 120, pct(C0["urgent_missed"]), "urgent cases missed without it",
         fill=REDTINT, num_color=RED, num_size=28)
    stat(s, 564 + (X1 - 580 + 12) / 2, 330, (X1 - 580 - 12) / 2, 120, pct(C["urgent_missed"]),
         f"with it, for {C['questions_mean'] - C0['questions_mean']:.1f} extra questions", fill=GREENTINT,
         num_color=GREEN, num_size=28)

    # 13 ------------------------------------------------------------------ HOW
    s = d.content("HOW: a proof down to the patient's answers",
                  "This is the HOW explanation of patient T002456, drawn by the web interface from the proof tree. "
                  "It is read from left to right: the patient's answers, highlighted in yellow, feed abstraction and "
                  "scoring rules; every rule box shows the certainty factor it adds and its source. The derived facts, "
                  "in grey, include the signs of deep-vein thrombosis and the Wells score of 6.5, and the diagnostic "
                  "rules D-PE-1 and D-PE-3 conclude pulmonary embolism, so a clinician can check each step against "
                  "the guideline.")
    image(s, FIG / "ui-proof-graph.png", X0, 104, w=X1 - X0, h=346, border=True)
    text(s, X0, 460, X1 - X0, 18, ["Same proof in the command line: ddxes explain --patient T002456 --how"],
         size=10.5, color=MUTED, font=CODE, align=PP_ALIGN.RIGHT)

    # 14 ------------------------------------------------------------------ WHY
    s = d.content("WHY: the goal behind every question",
                  "This is the consultation page during a consultation. Under the question, 'Why this question' gives "
                  "the WHY explanation: which hypothesis the system is trying to confirm or rule out, with its "
                  "probability and severity, which rule needs the answer and the source of that rule. Here the "
                  "consultation is in its rule-out phase: pulmonary embolism is almost certain, but a severe "
                  "alternative, acute pulmonary oedema, must be excluded first. Beside the question, a triage tag, "
                  "the red flags and the differential are updated after every answer.")
    _, (ix, iy, iw, ih) = image(s, FIG / "ui-consultation.png", X0, 104, w=600, h=372, crop=(236, 330, 1870, 1170),
                                border=True)
    rx = X0 + 620
    text(s, rx, 108, X1 - rx, 368,
         [[("WHY ", {"font": BOLD, "color": BLUE}), ("names, for the question being asked:", {})],
          [("the goal — ", {"font": BOLD, "color": BLUE}), ("the hypothesis to confirm or rule out, with P and "
                                                          "severity", {})],
          [("the rule — ", {"font": BOLD, "color": BLUE}), ("the guideline rule whose condition is still unknown", {})],
          [("the source — ", {"font": BOLD, "color": BLUE}), ("the publication the rule encodes", {})],
          [("the gain — ", {"font": BOLD, "color": BLUE}), ("expected information gain over the differential", {})],
          [("HOW and triage ", {"font": BOLD, "color": BLUE}), ("can be asked at any time (CLI: why, how, triage)",
                                                              {})]], size=12.5, after=10, spacing=1.0)

    # 15 ------------------------------------------------------------------ WHY NOT
    s = d.content("WHY NOT: the conditions that failed",
                  "The WHY NOT explanation addresses a frequent clinical question, namely why another disease was not "
                  "concluded. For anaphylaxis in patient T002456, set covering first says which reported symptom the "
                  "pathology cannot explain — coughing up blood. Then every guideline rule for anaphylaxis is "
                  "evaluated condition by condition, each marked as holding, failing or not asked yet, so the user "
                  "sees exactly which criterion of Sampson's definition is not met.")
    image(s, FIG / "ui-whynot.png", X0, 104, w=X1 - X0, h=372, crop=(250, 215, 2640, 1075), border=True)

    # 16 ------------------------------------------------------------------ provenance
    s = d.content("Provenance of inferences (W3C PROV-O)",
                  "After a consultation, the inferences are written back to the graph with the W3C PROV-O vocabulary: "
                  "each inference was generated by a rule firing, which used certain facts and beliefs and followed a "
                  "rule, which cites a source. So SPARQL can answer audit questions such as: why is this case an "
                  "emergency, which rule fired on which facts, and which guideline does that rule encode? Here the "
                  "answer is T-PE, on the belief in pulmonary embolism, citing the 2019 ESC guideline.")
    code(s, X0, 106, 400, 300, """# q08: why is this case an emergency?
SELECT ?level ?ruleCode ?used ?citation
WHERE {
  ?inf a :TriageInference ;
       :aboutCase ?case ;
       :inferredTriage ?t ;
       prov:wasGeneratedBy ?firing .
  ?t rdfs:label ?level .
  ?firing :firedRule ?rule ;
          prov:used ?u .
  ?rule :code ?ruleCode ; dct:source
        /dct:bibliographicCitation ?citation .
  ...
}""", size=10.5)
    rows = [["level", "rule", "used", "citation"],
            ["Emergency", "T-PE", "Pulmonary embolism", "Konstantinides et al. (2020), 2019 ESC Guidelines ..."]]
    table(s, X0, 414, [74, 46, 120, 160], rows, size=10, row_h=30)
    # the provenance chain of the triage inference
    gx = X0 + 430
    chain = [(":Inference  (a :TriageInference)", ":inferredTriage :Emergency · :aboutCase :Patient_T002456", CREAM),
             (":RuleFiring  (a prov:Activity)", "the rule, the facts and beliefs it used, its step", TINT),
             (":Rule_T-PE  (a :RedFlagRule, entailed)", "IF P(pulmonary embolism) ≥ 0.5 THEN Emergency", TINT),
             (":Src_konstantinides2020", "2019 ESC Guidelines on acute pulmonary embolism · DOI", GREENTINT)]
    labels = ["prov:wasGeneratedBy", ":firedRule", "dct:source"]
    bw, bh, step = 262, 60, 94
    for i, (head, sub_, fill) in enumerate(chain):
        y = 106 + i * step
        box(s, gx, y, bw, bh, fill=fill, line=LINE)
        text(s, gx + 10, y + 6, bw - 20, bh - 10, [[(head, {"font": BOLD, "color": BLUE, "size": 11.5})],
                                                   [(sub_, {"size": 10.5, "color": MUTED})]], after=1, spacing=1.0)
        if i < len(chain) - 1:
            arrow(s, gx + 40, y + bh, gx + 40, y + step, color=BLUE, width=1.5)
            text(s, gx + 48, y + bh + 9, 200, 18, [labels[i]], size=10.5, font=CODE, color=BLUE)
    ux, uy = gx + bw + 22, 106 + step
    box(s, ux, uy, X1 - ux, bh, fill=CREAM, line=LINE)
    text(s, ux + 8, uy + 6, X1 - ux - 16, bh - 10, [[(":Inference", {"font": BOLD, "color": BLUE, "size": 11})],
                                                     [("belief: pulmonary embolism, P = 1.00",
                                                       {"size": 10, "color": MUTED})]], after=1, spacing=1.0)
    arrow(s, gx + bw, uy + bh / 2, ux, uy + bh / 2, color=BLUE, width=1.5)
    text(s, ux, uy - 20, X1 - ux, 18, ["prov:used"], size=10.5, font=CODE, color=BLUE)

    # 17 ------------------------------------------------------------------ worked example
    s = d.content("Worked example: patient T002456",
                  "A 40-year-old woman reports loss of consciousness, haemoptysis, dyspnoea, hormone use, active "
                  "cancer, recent surgery and swollen calves. Abstraction rules derive signs of DVT and recent surgery; "
                  "scoring rules give a Wells score of 6.5; D-PE-1 and D-PE-3 fire. Set covering keeps one candidate "
                  "of 49, pulmonary embolism, whose profile explains all twelve symptoms; the posterior is 1.00. T-PE "
                  "makes the case an emergency and overrules the urgent level proposed for the syncope; R-PE-HIGH "
                  "recommends CT pulmonary angiography.")
    steps = [("Answers", "loss of consciousness, haemoptysis, dyspnoea, hormones, active cancer, surgery, swollen calves"),
             ("Abstraction", "signs of DVT · recent surgery or immobilisation · chest pain"),
             ("Scoring", "Wells score 6.5 = 3 + 1.5 + 1 + 1"),
             ("Diagnosis", "D-PE-1 (Wells > 4 with dyspnoea or haemoptysis, CF 0.7) and D-PE-3 (hormones, CF 0.2)"),
             ("Abduction", "set covering keeps 1 of 49 pathologies: 12 of 12 symptoms explained, P = 1.00"),
             ("Triage, advice", "T-PE → Emergency (overrules T-SYNCOPE: urgent) · R-PE-HIGH → CT pulmonary angiography")]
    for i, (head, body) in enumerate(steps):
        y = 106 + i * 62
        badge(s, X0, y + 2, 26, str(i + 1), size=11, fill=RED if i == 5 else BLUE)
        text(s, X0 + 36, y, 360, 60, [[(head, {"font": BOLD, "color": BLUE, "size": 13})], body], size=11.5, after=1,
             spacing=1.0)
    image(s, FIG / "ui-case.png", X0 + 412, 106, w=X1 - X0 - 412, h=368, crop=(1870, 830, 2640, 1520))

    # 18 ----------------------------------------------------------------- results: accuracy and triage
    es, nb, hy = MAIN["es"], MAIN["nb"], MAIN["hybrid"]
    miss3 = round((1 - es["top3"]) * MAIN["patients"])
    s = d.content("Results: accuracy and triage",
                  f"On the full test split of {fmt(MAIN['patients'])} patients the expert system ranks the true "
                  f"pathology first for {pct(es['top1'], 2)} of them, ahead of the naive-Bayes classifier built from the "
                  f"same counts at {pct(nb['top1'], 2)}; the hybrid reaches {pct(hy['top1'], 2)}. The true pathology is "
                  f"missing from the DDX-ES top three for only {'one patient' if miss3 == 1 else f'{fmt(miss3)} patients'}. "
                  "The differential metrics are low by design, because DDX-ES "
                  "commits to a diagnosis while the reference differential lists about ten. For triage, every emergency "
                  f"is triaged as an emergency. Under-triage is {pct(T['under'], 2)}, and over-triage, about one patient "
                  "in five, is the intended safety-first trade-off: guideline red flags such as suspected pulmonary "
                  "embolism assign Emergency where the DDXPlus severity scale assigns a lower level.")
    tiles = [(pct(es["top1"], 2), "top-1, DDX-ES (no training)", TINT, BLUE),
             (pct(nb["top1"], 2), "top-1, naive Bayes on the same counts", CODEBG, MUTED),
             (pct(hy["top1"], 2), "top-1, hybrid of the two", CODEBG, MUTED),
             (pct(T["emergency_recall"]), "of emergencies triaged as Emergency", GREENTINT, GREEN)]
    tw = (X1 - X0 - 3 * 14) / 4
    for i, (num, lab, fill, col) in enumerate(tiles):
        stat(s, X0 + i * (tw + 14), 104, tw, 96, num, lab, fill=fill, num_color=col, num_size=26)
    image(s, FIG / "systems.png", X0, 212, w=486, h=232)
    image(s, FIG / "triage_matrix.png", X0 + 500, 212, w=X1 - X0 - 500, h=232)
    text(s, X0, 450, X1 - X0, 26,
         [f"Top-3 ≥ 99.99% for all three · DDF1 {pct(es['ddf1'])} by design (one committed diagnosis) · under-triage "
          f"{pct(T['under'], 2)}, over-triage {pct(T['over'])} · {MAIN['ms_per_patient']:.1f} ms per patient"],
         size=11.5, color=MUTED, align=PP_ALIGN.CENTER)

    # 19 ------------------------------------------------------------------ ablation
    s = d.content("Ablation study of the engine components",
                  "The ablation switches parts of the engine off on a stratified sample. The guideline rules alone "
                  "rank the true pathology first for only 30 percent of the patients: they describe syndromes that "
                  "several pathologies share and were never written to separate them. Set covering alone reaches 79 "
                  "percent, both together 88. The calibrated knowledge-base links account for the ranking accuracy, 99 "
                  "percent with covering. The guideline rules therefore contribute elsewhere: concepts and scores, "
                  "triage, recommendations and the explanations.")
    image(s, FIG / "ablation.png", X0, 104, w=540, h=372)
    rx = X0 + 556
    finds = [(pct(AB["Guideline rules only"]["top1"]), "guideline rules alone: syndromes shared by several pathologies",
              REDTINT, RED),
             (pct(AB["Set covering only"]["top1"]), "set covering alone (abduction over the expert profiles)", CREAM, AMBER),
             (pct(AB["KB evidence + covering"]["top1"]), "calibrated knowledge-base links + covering", GREENTINT, GREEN)]
    for i, (num, lab, fill, col) in enumerate(finds):
        stat(s, rx, 104 + i * 96, X1 - rx, 86, num, lab, fill=fill, num_color=col, num_size=24)
    text(s, rx, 396, X1 - rx, 80,
         [[("Role of the guideline rules: ", {"font": BOLD, "color": BLUE}),
           ("concepts and scores, triage, recommendations; the explanations cite them.", {})]], size=12)
    text(s, X0, 478, 540, 16, [f"{fmt(ABL['patients'])} stratified test patients (up to 100 per pathology)"],
         size=9.5, color=MUTED)

    # 20 ------------------------------------------------------------------ consultation
    s = d.content("Interactive consultation: evaluation",
                  "We simulated the consultation on the 980 demo patients by answering every question from the "
                  "patient's record. With a stopping threshold of 0.95, DDX-ES asks 3.8 questions on average after "
                  "the chief complaint and ranks the true pathology first for about 98 percent; at 0.99 it asks 4.2 "
                  "for 98.8 percent. The reinforcement-learning agents of the DDXPlus paper ask 6.7 and 18 questions "
                  "after training on a million patients; the comparison is indicative, because they report the whole "
                  "test split. The rule-out phase improves triage safety at the cost of 0.7 extra questions. Red-flag "
                  "rules made no difference during the consultation, because their questions stay unasked; an early "
                  "red-flag screen is future work. "
                  f"The optional free-text intake was tested on {INTAKE['patients']} demo patients ({INTAKE_SCOPE}). "
                  "A language model wrote each patient's description in English and in Indonesian; the intake read "
                  f"them with {pct(IL['en']['precision_vs_record'])} and {pct(IL['id']['precision_vs_record'])} "
                  "precision against the records, and the consultation then asked "
                  f"{IL['en']['questions']:.1f} and {IL['id']['questions']:.1f} questions instead of "
                  f"{INTAKE['baseline']['questions']:.1f}.")
    rows = [["System", "Questions", "Top-1", "Trained on patients"],
            ["AARLC (Fansi Tchango et al., 2022)", "6.73", "99.21%", "yes (RL)"],
            ["BASD (Fansi Tchango et al., 2022)", "17.99", "97.15%", "yes (RL)"],
            ["DDX-ES, stop at P ≥ 0.95", f"{C['questions_mean']:.1f}", pct(C["top1"]), "no (counts only)"],
            ["DDX-ES, stop at P ≥ 0.99", f"{C99['questions_mean']:.1f}", pct(C99["top1"]), "no (counts only)"]]
    table(s, X0, 106, [210, 74, 70, 118], rows, size=11.5, row_h=34, bold_rows=(3, 4),
          aligns=[PP_ALIGN.LEFT, PP_ALIGN.CENTER, PP_ALIGN.CENTER, PP_ALIGN.LEFT])
    image(s, FIG / "consultation.png", X0, 288, w=472, h=188)
    rx = X0 + 492
    box(s, rx, 106, X1 - rx, 370, fill=TINT)
    text(s, rx + 16, 116, X1 - rx - 32, 24, ["Effect of the rule-out phase"], size=15, font=BOLD, color=BLUE)
    rows = [["", "without", "with"],
            ["questions", f"{C0['questions_mean']:.1f}", f"{C['questions_mean']:.1f}"],
            ["top-1", pct(C0["top1"]), pct(C["top1"])],
            ["under-triage", pct(C0["under_triage"]), pct(C["under_triage"])],
            ["urgent missed", pct(C0["urgent_missed"]), pct(C["urgent_missed"])]]
    table(s, rx + 16, 150, [118, 84, 84], rows, size=11.5, row_h=32, bold_rows=(4,),
          aligns=[PP_ALIGN.LEFT, PP_ALIGN.CENTER, PP_ALIGN.CENTER])
    text(s, rx + 16, 318, X1 - rx - 32, 48,
         ["Early in the interview almost half of the urgent patients still have a non-urgent leading hypothesis, "
          "so stopping too early is unsafe."], size=11.5, after=0, bullets=True)
    text(s, rx + 16, 368, X1 - rx - 32, 20, [f"Starting from free text ({INTAKE['patients']} patients)"], size=13,
         font=BOLD, color=BLUE)
    rows = [["Consultation starts from", "questions", "top-1"],
            ["the main complaint", f"{INTAKE['baseline']['questions']:.1f}", pct(INTAKE["baseline"]["top1"])],
            ["an English description", f"{IL['en']['questions']:.1f}", pct(IL["en"]["top1"])],
            ["an Indonesian description", f"{IL['id']['questions']:.1f}", pct(IL["id"]["top1"])]]
    table(s, rx + 16, 392, [168, 64, 64], rows, size=10.5, row_h=19,
          aligns=[PP_ALIGN.LEFT, PP_ALIGN.CENTER, PP_ALIGN.CENTER])

    # 21 ------------------------------------------------------------------ rule validation
    s = d.content("Validating every rule on held-out patients",
                  f"Every rule was run on a stratified sample of {fmt(RV['patients'])} validation patients. All "
                  f"{len(RULES)} rules fire at least once. For the {len(POS)} positive diagnostic rules the median "
                  "precision is 100 percent. The weakest rules are broad screening criteria such as the WHO "
                  "influenza-like-illness definition or the NICE lung-cancer symptoms, which are written for "
                  "sensitivity. The scatter shows that the certainty factors assigned from the sources agree only "
                  "moderately with the measured precision, which is a reason to keep the calibrated links in the "
                  "aggregation and to calibrate the rules from data in future work.")
    image(s, FIG / "rule_calibration.png", X0, 104, w=470, h=372)
    rx = X0 + 490
    tw = (X1 - rx - 24) / 3
    for i, (num, lab) in enumerate([(str(sum(1 for r in RV["rows"] if r["fires"] > 0)), f"of {len(RULES)} rules fire"),
                                    (pct(statistics.median(r["precision"] for r in POS), 0), "median precision"),
                                    (f"{statistics.median(r['lift'] for r in POS):.1f}", "median lift")]):
        stat(s, rx + i * (tw + 12), 104, tw, 86, num, lab, num_size=22)
    low = sorted(POS, key=lambda r: r["precision"])[:5]
    rows = [["Weakest rules", "CF", "Precision"]] + [[[(r["id"] + " ", {"font": BOLD, "color": BLUE}),
                                                       (r["name"].split(":")[0], {})], f"{r['cf']:.1f}",
                                                      pct(r["precision"], 0)] for r in low]
    table(s, rx, 204, [210, 46, 82], rows, size=11, row_h=30,
          aligns=[PP_ALIGN.LEFT, PP_ALIGN.CENTER, PP_ALIGN.CENTER])
    text(s, rx, 392, X1 - rx, 84, ["Screening criteria are written for sensitivity: a low precision on its own is "
                                   "expected. Assigned CFs agree only moderately with measured precision."],
         size=11.5, color=MUTED)

    # 22 ------------------------------------------------------------------ limitations
    s = d.content("Limitations and ethics",
                  "The main limitation is that the data are synthetic: the patients were generated from the same "
                  "knowledge base we use, so set covering is exact here; on real patients, with comorbidities and "
                  "noisy answers, accuracy would be lower and the soft covering mode would be needed. Summing log "
                  "likelihood ratios assumes independent evidence, and guideline rules partly re-count answers the "
                  "links already count, so posteriors are overconfident. Some rules only approximate their criteria. "
                  "Finally, DDX-ES is an educational prototype, not a medical device.")
    lims = [("Synthetic data, circularity", "patients come from the same knowledge base; covering is exact only "
             "here; the triage reference is synthetic too"),
            ("Independence", "summed log LRs assume independent evidence; rules re-count answers the links count — "
             "posteriors are overconfident"),
            ("Mapping fidelity", f"{FID['partial'] + FID['proxy']} rules need vital signs, tests, examination or "
             "duration that the questionnaire cannot ask"),
            ("Certainty factors", "assigned on a fixed scale; only moderate agreement with measured precision"),
            ("Single disorder", "covering looks for one pathology that explains everything")]
    for i, (head, body) in enumerate(lims):
        y = 106 + i * 74
        badge(s, X0, y + 4, 26, str(i + 1), size=11)
        text(s, X0 + 38, y, 470, 70, [[(head, {"font": BOLD, "color": BLUE, "size": 13.5})], body], size=12, after=2,
             spacing=1.0)
    box(s, 590, 106, X1 - 590, 368, fill=REDTINT)
    text(s, 606, 118, X1 - 622, 350,
         [[("Ethics", {"font": BOLD, "color": RED, "size": 15})],
          "Educational prototype on synthetic data: not a medical device, not validated with clinicians or real "
          "patients, not for medical decisions — the interface says so on every page.",
          "The rules paraphrase published criteria with citations; the clinical documents remain the authority.",
          "Transparency: every conclusion can be traced to its rule, facts and source."],
         size=12, after=10)

    # 23 ------------------------------------------------------------------ conclusions
    s = d.content("Conclusions and future work",
                  "To conclude. One RDF/OWL graph holds the DDXPlus knowledge and 192 cited rules as data, and the "
                  "OWL closure contributes facts the engine relies on. A production-rule engine on this graph ranks "
                  "the true pathology first for 99.4 percent of the test patients without any training, better than "
                  "a naive-Bayes model on the same counts, and misses no emergency. Every conclusion is explained. "
                  "Reading certainty factors as likelihood ratios fixed a saturation failure, and the rule-out phase "
                  "improved consultation safety. An optional language-model intake reads free text in English or "
                  "Indonesian; it only maps the words onto the questionnaire. Next steps: clinicians and real data, "
                  "calibrated rule CFs, an early red-flag screen, SHACL validation, a triple store, an Indonesian "
                  "questionnaire, and an evaluation of the intake on descriptions written by patients.")
    concl = [f"Knowledge representation: DDXPlus + {len(RULES)} rules from {len(CITED)} sources in one RDF/OWL 2 "
             "graph; the OWL 2 RL closure adds facts the engine uses; SPARQL audits rules and provenance.",
             f"Accuracy: {pct(es['top1'], 2)} top-1 on {fmt(MAIN['patients'])} test patients without "
             f"training, above naive Bayes on the same counts ({pct(nb['top1'], 2)}); no emergency under-triaged.",
             "Explanation facilities: HOW, WHY, WHY NOT and PROV-O provenance, with a source for every rule.",
             f"Lessons learned: CFs as likelihood ratios avoid saturation; a rule-out phase improves consultation "
             f"safety ({pct(C0['urgent_missed'])} → {pct(C['urgent_missed'])} urgent missed).",
             f"Optional language-model intake: free text in English or Bahasa Indonesia read with "
             f"{pct(IL['en']['precision_vs_record'])} / {pct(IL['id']['precision_vs_record'])} precision; the rules "
             "still make every conclusion."]
    text(s, X0, 108, 470, 24, [[("Conclusions", {"font": BOLD, "color": BLUE, "size": 15})]], after=0)
    for i, c in enumerate(concl):
        y = 144 + i * 66
        badge(s, X0, y + 2, 24, str(i + 1), size=10.5)
        text(s, X0 + 34, y, 440, 64, [c], size=12.5, after=0, spacing=1.05)
    box(s, 556, 106, X1 - 556, 368, fill=TINT)
    fut = ["Validate the rules with clinicians and on real, de-identified consultations",
           "Calibrate the rule CFs from data, keeping their structure",
           "An explicit red-flag screening step early in the consultation",
           "Validate the rule base inside the graph with SHACL; serve it from a triple store (GraphDB)",
           "Questionnaire and explanations in Bahasa Indonesia; evaluate the free-text intake on descriptions "
           "written by patients"]
    text(s, 572, 118, X1 - 588, 350, [[("Future work", {"font": BOLD, "color": BLUE, "size": 15})]], after=8)
    text(s, 572, 150, X1 - 588, 320, fut, size=12, after=8, bullets=True, spacing=1.0)

    # 24 ------------------------------------------------------------------ how to run
    s = d.content("Repository and reproducibility",
                  "Everything is in the public repository on GitHub, github.com/zidanarsyad/ddx-expert-system. The "
                  "README explains how to install and run "
                  "it: a virtual environment, two pip commands, then the web interface (ddxes serve) or the "
                  "ddxes command line. The knowledge graph and the processed data are committed, so the system runs "
                  "immediately; the scripts rebuild everything from the raw DDXPlus files, including the figures, the "
                  "report and this deck. There are 120 automated tests. The optional free-text intake needs an API key "
                  "for Groq, Gemini or OpenRouter in a local .env file, which is never committed.")
    code(s, X0, 106, 470, 300, """git clone https://github.com/zidanarsyad/ddx-expert-system.git
cd ddx-expert-system
python -m venv .venv          # then activate it
pip install -r requirements.txt
pip install -e .

ddxes serve                   # http://127.0.0.1:8000

ddxes diagnose --patient T002456 --trace
ddxes explain --patient T002456 --why-not Pneumonia
ddxes consult                 # why / how / triage
ddxes sparql queries/q08-provenance.rq
ddxes intake "Bapak 39 tahun, sesak napas"   # key in .env
pytest                        # 120 tests""", size=11.5)
    layout = [("knowledge/rules.yaml", "the rule base and its sources"),
              ("kg/*.ttl", "ontology, knowledge base, rules, statistics, cases, closure"),
              ("src/ddxes/", "engine, certainty factors, explanation, provenance, consultation"),
              ("src/ddxes/web/", "web interface (Starlette, SVG proof trees)"),
              ("queries/", "12 SPARQL queries, results with and without the closure"),
              ("scripts/", "data → graph → evaluation → figures, report, slides"),
              ("results/, docs/", "evaluation outputs; report and slides"),
              (".env.example", "template for the optional language-model key")]
    text(s, X0 + 490, 104, X1 - X0 - 490, 24, [[("github.com/zidanarsyad/ddx-expert-system", {"font": CODE, "color": BLUE})]],
         size=12.5)
    text(s, X0 + 490, 140, X1 - X0 - 490, 336,
         [[(a + "  ", {"font": CODE, "color": BLUE, "size": 11}), (b, {})] for a, b in layout],
         size=11.5, after=7, spacing=1.0)
    box(s, X0, 416, 470, 58, fill=CREAM)
    text(s, X0 + 14, 420, 442, 50, ["Rebuild pipeline from the raw data: prepare_data.py → build_kg.py → evaluate.py → "
                                    "make_figures.py → build_report.py → build_deck.py"], size=11.5,
         anchor=MSO_ANCHOR.MIDDLE)

    # 25 --------------------------------------------------------------------- closing
    shapes = {sh.name: sh for sh in d.closing.shapes}
    set_lines(shapes["TextBox 380"], ["Each conclusion is traced to its", "rule, facts and source."])
    text(d.closing, 87, 400, 333, 36, ["Thank you · Terima kasih"], size=20, font=BOLD, color=BLUE)
    text(d.closing, 87, 438, 420, 22, ["github.com/zidanarsyad/ddx-expert-system"], size=12.5, font=CODE, color=BLUE)
    notes(d.closing, "Thank you. The report, this deck and the repository with its README, on GitHub at "
                     "github.com/zidanarsyad/ddx-expert-system, are submitted together. "
                     "We are happy to show the live system or answer questions.")
    return d


def main():
    kb = KnowledgeBase.load()
    d = build(kb)
    d.save(OUT)
    print(f"wrote {OUT} ({len(d.prs.slides)} slides)")
    if "--copy-to" in sys.argv:
        dest = pathlib.Path(sys.argv[sys.argv.index("--copy-to") + 1])
        shutil.copyfile(OUT, dest)
        print(f"copied to {dest}")


if __name__ == "__main__":
    main()
