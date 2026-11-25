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
from ddxes.kgstats import file_counts  # noqa: E402

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
        self.part = None        # the part of the talk, shown above the title of each content slide

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
        if self.part:
            text(s, X0, 9, 600, 16, [self.part], size=9.5, font=BOLD, color=AMBER, after=0)
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
INTAKE_Q = " and ".join(dict.fromkeys(f"{IL[lang]['questions']:.1f}" for lang in ("en", "id")))  # one number if equal
ASSERTED = sum(KG[k] for k in ("ontology", "kb", "rules", "stats", "cases"))
FC = file_counts()                                   # what each Turtle file contains (slide 6)
CAT, FID = Counter(r.category for r in RULES), Counter(r.fidelity for r in RULES)
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
                    "Muhammad Zidan Arsyad",
                    "Muhammad Fikry Rizal",
                    "Aditya Wahyu Wijanarko",
                    "Sistem Pakar Lanjut · Magister Kecerdasan Artifisial",
                    "Departemen Ilmu Komputer dan Elektronika · UGM · 2026"], fonts={2: BOLD, 3: BOLD, 4: BOLD})
    notes(d.cover, "Good morning. Our final project is DDX-ES, an expert system that takes a patient's answers to a "
                   "symptom questionnaire and returns a differential diagnosis, a triage level and recommended next "
                   "steps, and explains every one of these conclusions with the rules that fired, the facts they "
                   "matched and the published guideline each rule encodes. The knowledge, the rules and the reasoning "
                   "trail are all stored in one RDF/OWL knowledge graph.")

    # 2 ------------------------------------------------------------------------ agenda
    s = d.content("Structure and assignment components",
                  "The presentation has four parts. The description states the problem, the objectives and the domain. "
                  "The second part explains how the system works: the concepts (the knowledge graph, IF-THEN rules, "
                  "certainty factors and set covering), the workflow from the DDXPlus files to an explained conclusion, "
                  "and the algorithms of an inference run and of the consultation. The third part gives the results "
                  "and the fourth the conclusions. Below, each card maps one component of the assignment to the "
                  "slides that answer it. The fourth component, machine learning or a language model, is optional. We "
                  "use a naive-Bayes classifier as a baseline and in a hybrid mode, and a language model only to read "
                  "a free-text description into the questionnaire, so the expert system remains the core. The report, "
                  "this deck and the repository with its README are the deliverables.")
    parts = [("1", "Description", "Slide 3 · problem, objectives, input and output, domain"),
             ("2", "How it works", "Slides 4–11 · concepts, workflow, algorithms, explanations"),
             ("3", "Results", "Slides 12–13 · accuracy, triage, ablation, consultation, rules"),
             ("4", "Conclusion", "Slides 14–15 · conclusions, limitations, reproducibility")]
    pw = (X1 - X0 - 3 * 12) / 4
    for i, (num, head, sub_) in enumerate(parts):
        x = X0 + i * (pw + 12)
        box(s, x, 102, pw, 56, fill=CODEBG, line=LINE, radius=0.12)
        badge(s, x + 10, 117, 26, num, fill=DARKGOLD, size=11)
        text(s, x + 44, 108, pw - 52, 46, [[(head, {"font": BOLD, "color": BLUE, "size": 13})],
                                           [(sub_, {"size": 9.5, "color": MUTED})]], after=0, spacing=1.0)
    cards = [("Ontology + knowledge graph",
              f"Hand-written OWL 2 ontology; {fmt(ASSERTED)} asserted triples in six Turtle files; the OWL 2 RL "
              f"closure adds {fmt(KG['inferred'])}.", "Slides 4, 6"),
             ("IF–THEN rules with references",
              f"{len(RULES)} rules from {len(CITED)} published sources — guidelines, case definitions, criteria — "
              "each with its DOI and mapping fidelity.", "Slides 4–5"),
             ("Reasoning and explanations",
              "Forward chaining, abduction, certainty factors, safety-first triage and a consultation; HOW, WHY, "
              "WHY NOT and PROV-O provenance.", "Slides 7–11"),
             ("Machine learning and LLM (optional)",
              f"A naive-Bayes baseline and a hybrid mode on the same counts ({fmt(MAIN['patients'])} test "
              "patients); a language-model intake that reads free text in English or Bahasa Indonesia.",
              "Slides 9, 12–13")]
    cw = (X1 - X0 - 3 * 16) / 4
    for i, (head, body, ref) in enumerate(cards):
        x = X0 + i * (cw + 16)
        box(s, x, 168, cw, 246)
        badge(s, x + 14, 180, 30, str(i + 1))
        text(s, x + 54, 187, cw - 68, 18, [f"COMPONENT {i + 1}"], size=10, font=BOLD, color=MUTED)
        text(s, x + 14, 220, cw - 28, 46, [head], size=14, font=BOLD, color=BLUE, after=0)
        text(s, x + 14, 268, cw - 28, 116, [body], size=11.5, after=0, spacing=1.0)
        text(s, x + 14, 388, cw - 28, 18, [ref], size=11, font=BOLD, color=AMBER)
    box(s, X0, 424, X1 - X0, 50, fill=CODEBG, line=LINE, radius=0.08)
    text(s, X0 + 16, 428, X1 - X0 - 32, 42,
         [[("Deliverables: ", {"font": BOLD, "color": BLUE}),
           ("report (PDF, FMIPA UGM format) · this presentation · repository ddx-expert-system with a README on how "
            "to run it — 134 tests, every result reproducible from the raw data", {})]],
         size=12, anchor=MSO_ANCHOR.MIDDLE)

    # 3 ------------------------------------------------------------------- problem, objectives, domain
    d.part = "PART 1 · DESCRIPTION"
    s = d.content("Problem, objectives and domain",
                  "A symptom checker asks questions, ranks the possible diseases and says how urgently to seek care. "
                  "Learned models can be accurate, but they cannot say which facts and which guideline support a "
                  "decision, and the dangerous error is under-triage: assigning an emergency patient a lower urgency. "
                  "Classic expert systems such as MYCIN and INTERNIST-I explain by construction, but their knowledge "
                  "was hand-coded and hard to audit. Our objectives combine the two approaches: auditable knowledge in "
                  "a graph, rules with sources, explainable reasoning, evaluated on a large benchmark. The box on the "
                  "left describes DDX-ES in brief: its input is the age, sex and main complaint of a patient and the "
                  "answers to the questionnaire, or a description in plain words; its output is a differential "
                  "diagnosis with probabilities, a triage level, recommended next steps and the explanation of each "
                  "conclusion. The workflow in the same box is what the second part of the talk explains. "
                  "The domain is DDXPlus, a benchmark for automatic diagnosis. It publishes an expert knowledge base of "
                  "49 pathologies and 223 questions, and about 1.3 million synthetic patients generated from it by a "
                  "commercial rule-based system, each with answers, the true pathology and a ranked differential. We "
                  "use the knowledge base as expert knowledge, the validation split for calibration counts and the "
                  "test split only for evaluation. The patients are synthetic, which matters for the limitations.")
    probs = [("Ranking and urgency. ", "From a few answers, propose a differential diagnosis and a triage level "
              "(Emergency, Urgent, Non-urgent, Self-care)."),
             ("Limited explainability. ", "A learned model cannot say which facts and which guideline support its "
              "answer."),
             ("Patient safety. ", "Under-triage, an emergency given a lower urgency, is the most serious error."),
             ("Classic expert systems explain ", "(MYCIN, INTERNIST-I), but hand-coded knowledge without sources is "
              "hard to audit.")]
    text(s, X0, 104, 416, 140, [[(a, {"font": BOLD, "color": BLUE}), (b, {})] for a, b in probs],
         size=11.5, after=5, bullets=True, spacing=1.0)
    box(s, X0, 250, 416, 128, fill=CODEBG, line=LINE, radius=0.06)
    text(s, X0 + 12, 254, 392, 18, ["DDX-ES in brief"], size=12, font=BOLD, color=BLUE, after=0)
    parts = [("Input", "age, sex, main complaint and answers, or free text", AMBER, CREAM),
             ("DDX-ES", f"knowledge graph, {len(RULES)} cited rules, inference engine", BLUE, TINT),
             ("Output", "differential with P, triage, next steps, explanation", GREEN, GREENTINT)]
    bw, gw = 116, 24
    for i, (head, body, col, fill) in enumerate(parts):
        x = X0 + 10 + i * (bw + gw)
        box(s, x, 276, bw, 70, fill=fill, line=col, radius=0.08)
        text(s, x + 8, 280, bw - 16, 64, [[(head, {"font": BOLD, "color": col, "size": 11})], body], size=9.5,
             after=2, spacing=1.0)
        if i < len(parts) - 1:
            arrow(s, x + bw + 3, 311, x + bw + gw - 3, 311, color=DARKGOLD, width=2)
    text(s, X0 + 12, 351, 392, 26,
         [[("Workflow: ", {"font": BOLD, "color": AMBER}),
           ("DDXPlus files → Turtle graph → rule engine (4 phases) → consultation → HOW, WHY, WHY NOT → PROV-O",
            {})]], size=9.5, after=0, spacing=1.0)
    ox = X0 + 432
    box(s, ox, 104, X1 - ox, 274, fill=TINT)
    text(s, ox + 14, 112, X1 - ox - 28, 22, ["Objectives of DDX-ES"], size=14, font=BOLD, color=BLUE)
    objs = ["Represent the domain, the rules (as data, each with its source), the cases and the provenance in one "
            "RDF/OWL 2 knowledge graph",
            f"Encode {len(RULES)} IF–THEN rules from {len(CITED)} published sources, with certainty factors and "
            "mapping fidelity",
            "Reason: forward chaining, abduction, certainty factors, safety-first triage, interactive consultation",
            "Explain every conclusion: HOW, WHY, WHY NOT, PROV-O",
            f"Evaluate on the {fmt(MAIN['patients'])} DDXPlus test patients against ML and published agents"]
    for i, o in enumerate(objs):
        y = 142 + i * 47
        badge(s, ox + 14, y, 20, str(i + 1), size=9.5)
        text(s, ox + 42, y, X1 - ox - 56, 46, [o], size=11, after=0, spacing=1.0)
    text(s, X0, 384, X1 - X0, 30,
         [[("Domain: DDXPlus ", {"font": BOLD, "color": BLUE}),
           (f"(Fansi Tchango et al., NeurIPS 2022, data CC BY 4.0): an expert knowledge base and synthetic patients "
            f"generated from it; the validation split ({fmt(SUMMARY['validation_patients'])}) gives the calibration "
            "counts, the test split is used only for evaluation.", {"color": MUTED})]], size=10.5, after=0,
         spacing=1.0)
    tiles = [("49", "pathologies: respiratory, cardiac, emergencies"), ("223", "questions: 110 symptoms, 113 antecedents"),
             ("1.3 M", "synthetic patients in three splits"),
             (fmt(SUMMARY["test_patients"]), "test patients, for evaluation only")]
    tw = (X1 - X0 - 3 * 12) / 4
    for i, (num, lab) in enumerate(tiles):
        x = X0 + i * (tw + 12)
        box(s, x, 420, tw, 56, fill=TINT)
        text(s, x + 10, 420, 88, 56, [num], size=20, font=BOLD, color=BLUE, anchor=MSO_ANCHOR.MIDDLE, after=0)
        text(s, x + 100, 420, tw - 108, 56, [lab], size=9.5, color=MUTED, anchor=MSO_ANCHOR.MIDDLE, after=0,
             spacing=1.0)

    # 4 ------------------------------------------------------------ from the data to Turtle
    d.part = "PART 2 · HOW IT WORKS: CONCEPTS, WORKFLOW, ALGORITHMS"
    s = d.content("From the DDXPlus files to Turtle",
                  "The knowledge graph is built from three inputs: the files of the DDXPlus knowledge base, the rule "
                  "base we wrote from the published criteria, and the patient files of the validation and test splits. "
                  "The first script, prepare_data.py, aligns the French codes of the conditions file with the English "
                  "question codes through the shared French question text, builds the records of the pathologies and "
                  "the questions, parses the answer tokens of every patient, counts how often each feature occurs with "
                  "each pathology in the validation split, draws twenty test patients per pathology and runs nine "
                  "checks. The second script, build_kg.py, gives every record an IRI and converts it into RDF triples, "
                  "turns the counts into likelihood ratios and certainty factors, converts the rules from YAML into RDF "
                  "and checks that this conversion is lossless, and computes the OWL 2 RL closure with owlrl. The "
                  "result is six Turtle files, which the engine reads back at start-up. The bottom line shows two "
                  "examples: an answer token of a patient and the count behind one likelihood ratio.")
    colw = [176, 210, 214, 158]                    # source files, step 1, step 2, Turtle files
    gap = (X1 - X0 - sum(colw)) / 3
    xs = [X0]
    for w in colw[:-1]:
        xs.append(xs[-1] + w + gap)
    top, bottom = 104, 398
    srcs = [("DDXPlus knowledge base", ["release_evidences.json", "release_conditions.json"],
             f"223 questions · 49 pathologies · {SUMMARY['kb_links']} profile links"),
            ("Published criteria", ["knowledge/rules.yaml"], f"{len(RULES)} rules written from {len(CITED)} sources"),
            ("DDXPlus patients", ["release_validate_patients", "release_test_patients"],
             f"{fmt(SUMMARY['validation_patients'])} and {fmt(SUMMARY['test_patients'])} patients (CSV)")]
    text(s, xs[0], top, colw[0], 20, ["Source files"], size=12.5, font=BOLD, color=BLUE)
    bh = (bottom - top - 26 - 2 * 8) / 3
    for i, (head, files, desc) in enumerate(srcs):
        y = top + 26 + i * (bh + 8)
        box(s, xs[0], y, colw[0], bh, fill=TINT)
        text(s, xs[0] + 10, y + 7, colw[0] - 20, bh - 10,
             [[(head, {"font": BOLD, "color": BLUE, "size": 11.5})]] + [[(f, {"font": CODE, "size": 9.5})] for f in files]
             + [[(desc, {"size": 10, "color": MUTED})]], after=2, spacing=1.0)
    steps = [("1", "prepare_data.py", "raw files → checked records",
              ["Align the French codes of the conditions file with the E_xx question codes (223 of 223)",
               "Pathologies: identifier, ICD-10 chapter, severity, profile links",
               f"Questions: short label, type, parent; {SUMMARY['values']} answer values",
               "Patient tokens: E_45 = yes; E_55_@_V_127 = value V_127",
               f"Count {fmt(SUMMARY['feature_pairs'])} (feature, pathology) pairs on the validation split",
               "Draw 20 test patients per pathology (980); run 9 checks"]),
             ("2", "build_kg.py", "records and rules → RDF triples",
              ["Each record → an IRI from its code, with its classes and English and French labels",
               "Profile links → :hasSymptom, :hasAntecedent",
               "Counts → :EvidenceStrength with P(f|d), P(f|¬d), λ and CF",
               "rules.yaml → RDF condition trees; references and round trip checked",
               "Demo patients → :Patient with :Answer nodes",
               "owlrl: OWL 2 RL closure of ontology, kb and rules"])]
    for k, (num, name, sub_, items) in enumerate(steps):
        x, w = xs[1 + k], colw[1 + k]
        box(s, x, top, w, bottom - top, fill=CODEBG, line=LINE)
        badge(s, x + 10, top + 10, 24, num, size=11)
        text(s, x + 42, top + 8, w - 50, 40, [[(name, {"font": CODE, "color": BLUE, "size": 12})],
                                             [(sub_, {"font": LIGHT_IT, "color": AMBER, "size": 10.5})]], after=1)
        text(s, x + 10, top + 50, w - 20, bottom - top - 56, items, size=10, after=4, bullets=True, spacing=1.0)
    x, w = xs[3], colw[3]
    text(s, x, top, w, 20, ["Turtle files (triples)"], size=12.5, font=BOLD, color=BLUE)
    files = [["ontology.ttl", fmt(KG["ontology"])], ["kb.ttl", fmt(KG["kb"])], ["rules.ttl", fmt(KG["rules"])],
             ["stats.ttl", fmt(KG["stats"])], ["cases.ttl", fmt(KG["cases"])], ["inferred.ttl", fmt(KG["inferred"])]]
    table(s, x, top + 26, [94, 64], [["File", "Triples"]] + files, size=10, row_h=24, fonts={0: CODE},
          aligns=[PP_ALIGN.LEFT, PP_ALIGN.RIGHT])
    text(s, x, top + 26 + 7 * 24 + 10, w, bottom - top - 26 - 7 * 24 - 10,
         ["ontology.ttl is written by hand; the other five are generated.",
          f"{fmt(ASSERTED)} asserted triples; OWL 2 RL entails {fmt(KG['inferred'])} more."],
         size=10, color=MUTED, after=4, spacing=1.0)
    ym = (top + bottom) / 2 + 8
    for k in range(3):
        arrow(s, xs[k] + colw[k] + 3, ym, xs[k + 1] - 3, ym, color=DARKGOLD, width=2)
    box(s, X0, 410, X1 - X0, 64, fill=CODEBG, line=LINE, radius=0.08)
    text(s, X0 + 14, 414, X1 - X0 - 28, 56,
         [[("Patient answer  ", {"font": BOLD, "color": BLUE, "size": 11}),
           ("T002456, token E_55_@_V_127  →  ", {"size": 10.5}),
           (":Answer_T002456_E_55_V_127 :ofEvidence :E_55 ; :answerValue :V_127 .", {"font": CODE, "size": 9.5})],
          [("Calibration  ", {"font": BOLD, "color": BLUE, "size": 11}),
           ("2,345 of 3,725 PE patients cough up blood  →  ", {"size": 10.5}),
           (":S_PulmonaryEmbolism_E_45 :likelihoodRatio 7.44 ; :certaintyFactor 0.87 .", {"font": CODE, "size": 9.5})]],
         after=6, anchor=MSO_ANCHOR.MIDDLE)

    # 5 ------------------------------------------------------------- the rule base and its mapping
    s = d.content(f"The rule base: {len(RULES)} rules from {len(CITED)} sources",
                  "The rules come in five categories. Abstraction rules turn raw answers into clinical concepts, "
                  "scoring rules add points to clinical scores, diagnostic rules add belief to pathologies, triage "
                  "rules propose urgency levels and recommendation rules propose next steps. The categories form "
                  "strata: a rule only fires after the rules that produce the concepts and scores it reads. At the "
                  "bottom left is the Wells rule exactly as the engine renders it from the graph. "
                  "Each criterion was translated item by item into DDXPlus questions. The Wells score for pulmonary "
                  "embolism shows the typical situation: most items map to a question, some only approximately, and "
                  "some cannot be asked at all, such as the heart rate. Of its seven items, four map faithfully "
                  "(immobilisation or recent surgery, previous DVT or PE, haemoptysis, malignancy), the clinical signs "
                  "of DVT only by proxy through the swelling of the calf, and two cannot be asked: the heart rate and "
                  "'PE is the most likely diagnosis', which would be circular for a diagnostic system. Every rule "
                  "records this as its mapping fidelity, with a note on what is missing, so the knowledge base "
                  "documents how closely each rule follows its source. Certainty factors follow a fixed scale tied to "
                  "the strength of the source.")
    ex = [("Abstraction", "abstraction", "IF chest pain AND pain increased by deep breathing THEN pleuritic chest pain"),
          ("Scoring", "scoring", "IF surgery in the last month OR immobile > 3 days THEN Wells score += 1.5"),
          ("Diagnostic", "diagnostic", "IF allergen contact AND at least 2 of {skin, respiratory, circulatory, GI} "
           "THEN anaphylaxis (CF 0.9)"),
          ("Triage", "triage", "IF P(anaphylaxis) ≥ 0.5 THEN Emergency (CF 0.95)"),
          ("Recommendation", "recommendation", "IF P(PE) ≥ 0.4 AND Wells score > 4 THEN CT pulmonary angiography")]
    rows = [["Category", "Rules", "Example"]] + [[a, str(CAT[k]), c] for a, k, c in ex]
    table(s, X0, 104, [118, 50, 374], rows, size=10.5, row_h=34, fonts={0: BOLD}, colors={0: BLUE},
          aligns=[PP_ALIGN.LEFT, PP_ALIGN.CENTER, PP_ALIGN.LEFT])
    pe = BY_ID["D-PE-1"]
    code(s, X0, 316, 542, 104,
         f"# {pe.id} as the engine renders it ({kb.short_citation(pe.source)}; fidelity: {pe.fidelity})\n"
         + kb.rule_text(pe).replace(" AND (", "\n  AND (").replace(" THEN ", "\nTHEN "), size=10.5)
    text(s, X0, 428, 542, 48,
         [[("Every rule: ", {"font": BOLD, "color": BLUE}),
           ("identifier · premise (AND, OR, at least n of, NOT, scales, age, sex, concepts, scores, beliefs) · "
            "consequents · certainty factor · source with DOI · fidelity · note; validated on load.", {})]],
         size=10.5, spacing=1.0)
    rx = X0 + 558
    text(s, rx, 104, X1 - rx, 20, ["Mapping fidelity of all rules"], size=12.5, font=BOLD, color=BLUE)
    for i, (k, lab, col, tint) in enumerate([("faithful", "faithful: every item has a question", GREEN, GREENTINT),
                                              ("partial", "partial: some items cannot be asked", AMBER, CREAM),
                                              ("proxy", "proxy: an item approximated by a question", BLUE, TINT)]):
        y = 130 + i * 52
        box(s, rx, y, X1 - rx, 46, fill=tint)
        text(s, rx + 10, y, 56, 46, [str(FID[k])], size=20, font=BOLD, color=col, anchor=MSO_ANCHOR.MIDDLE, after=0)
        text(s, rx + 66, y, X1 - rx - 76, 46, [lab], size=10.5, anchor=MSO_ANCHOR.MIDDLE, after=0, spacing=1.0)
    box(s, rx, 292, X1 - rx, 82, fill=CODEBG, line=LINE)
    text(s, rx + 10, 296, X1 - rx - 20, 76,
         [[("The Wells score for PE: ", {"font": BOLD, "color": BLUE}),
           ("4 of its 7 items map to questions, the signs of DVT only by proxy; the heart rate and 'PE most likely' "
            "cannot be asked.", {})]], size=10.5, spacing=1.0, anchor=MSO_ANCHOR.MIDDLE)
    box(s, rx, 382, X1 - rx, 94, fill=CODEBG, line=LINE)
    text(s, rx + 10, 387, X1 - rx - 20, 86,
         [[("Certainty factors from the source", {"font": BOLD, "color": BLUE})],
          "0.8–0.9  case definition or criteria met", "0.5–0.7  strongly suggestive pattern",
          "0.2–0.4  supportive feature or risk factor", "< 0  evidence against"], size=10.5, after=1, spacing=1.0)

    # 6 ------------------------------------------------------------------ the knowledge graph
    s = d.content("The knowledge graph: ontology, rules, OWL 2 RL",
                  "All the knowledge is stored in one RDF graph with a hand-written OWL 2 ontology in three parts: the "
                  "domain (pathologies, evidences, answer values, patients), the rule vocabulary (rules are prov:Plan, "
                  "with condition trees, consequents, certainty factors and sources) and the provenance model. The "
                  "rules are represented as data: they are written in YAML, stored as RDF, and the engine reads them "
                  "back from the graph; the build checks that the round trip is lossless. The engine depends on the "
                  "OWL 2 RL closure: the red-flag rules, the disease families and the 888 disease profiles it uses are "
                  "entailed, not asserted. Query q11 illustrates this: with the closure, pulmonary embolism is the "
                  "only pathology that explains every symptom of patient T002456; without it, none is found. The table "
                  "says what each Turtle file contains. The ontology is the only file written by hand. kb.ttl holds the "
                  "DDXPlus knowledge base: pathologies, questions, answer values and the profile links. rules.ttl holds "
                  "the rules with their condition trees, and the concepts, scores, value sets and sources they refer "
                  "to. stats.ttl holds one evidence-strength node per pathology and feature, with the count, the two "
                  "conditional probabilities, the likelihood ratio and the certainty factor. cases.ttl holds the demo "
                  "patients and their answers, and inferred.ttl the entailments of the OWL 2 RL closure.")
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
    on, kbf, ru, st, ca, inf = (FC[f] for f in ("ontology", "kb", "rules", "stats", "cases", "inferred"))
    files = [["File", "What it contains", "Triples"],
             ["ontology.ttl", f"Written by hand: {on['types']['Class']} classes, "
              f"{on['types']['ObjectProperty'] + on['types']['DatatypeProperty']} properties, "
              f"{on['blank_types']['Restriction']} OWL restrictions, triage levels", fmt(on["triples"])],
             ["kb.ttl", f"49 pathologies, 223 questions, {kbf['types']['AnswerValue']} answer values, "
              f"{kbf['predicates']['hasSymptom'] + kbf['predicates']['hasAntecedent']} profile links", fmt(kbf["triples"])],
             ["rules.ttl", f"{len(RULES)} rules as condition trees; "
              f"{ru['types']['ClinicalConcept'] + ru['types']['RedFlag']} concepts, {ru['types']['ValueSet']} value sets, "
              f"{inf['types']['Source']} sources", fmt(ru["triples"])],
             ["stats.ttl", f"{fmt(st['types']['EvidenceStrength'])} nodes: count, P(f|d), P(f|¬d), λ and CF of a "
              "(pathology, feature) pair", fmt(st["triples"])],
             ["cases.ttl", f"{ca['types']['Patient']} demo patients: {fmt(ca['predicates']['hasEvidence'])} yes answers, "
              f"{fmt(ca['types']['Answer'])} other answers", fmt(ca["triples"])],
             ["inferred.ttl", f"OWL 2 RL entailments: {inf['predicates']['characterizedBy']} profile links, inverses, "
              f"{inf['predicates']['type']} types", fmt(inf["triples"])]]
    table(s, rx, 104, [92, 248, 48], files, size=10, row_h=34, fonts={0: CODE},
          aligns=[PP_ALIGN.LEFT, PP_ALIGN.LEFT, PP_ALIGN.RIGHT])
    text(s, rx, 350, X1 - rx, 22, ["What OWL 2 RL reasoning adds"], size=13.5, font=BOLD, color=BLUE)
    rows = [["SPARQL query", "With", "Without"]]
    for q, label in (("q05-red-flag-rules", "q05  rules entailed as :RedFlagRule"),
                     ("q06-entailed-pathology-classes", "q06  diseases with entailed classes"),
                     ("q11-set-covering", "q11  pathologies explaining all symptoms")):
        a, b = query_rows(q)
        rows.append([label, a, b])
    table(s, rx, 376, [244, 72, 72], rows, size=10.5, row_h=24, fonts={1: BOLD},
          aligns=[PP_ALIGN.LEFT, PP_ALIGN.CENTER, PP_ALIGN.CENTER])

    # 7 ------------------------------------------------------------------ how each inference is processed
    s = d.content("How each inference is processed",
                  "On the left is one inference run as pseudocode, the algorithm behind every result; the report gives "
                  "it in full as Algorithm 4.1. On the right is what each kind of inference is, phase by phase, for "
                  "patient T002456, a 40-year-old woman who reports loss of consciousness, haemoptysis, dyspnoea, "
                  "hormone use, active cancer, recent surgery and swollen calves. Each kind of inference is produced "
                  "by its own mechanism. The OWL 2 RL entailments are computed once, when the graph is built: the "
                  "disease profiles, for example, exist only as entailed characterizedBy links, and T-PE is classified "
                  "as a red-flag rule because it concludes Emergency. All the other inferences are made for each case, "
                  "in four phases. In phase one, forward chaining over strata, abstraction rules derive clinical "
                  "concepts with CF equal to the rule's CF times the premise's, scoring rules add points and "
                  "diagnostic rules contribute certainty factors; for this patient the Wells score reaches 6.5, and "
                  "D-PE-1 and D-PE-3 fire. In phase two, set covering excludes every pathology whose profile lacks a "
                  "reported symptom: 48 of the 49, pulmonary neoplasm among them, although two rules for it fired. In "
                  "phase three, the rule CFs and 29 knowledge-base links are summed as log likelihood ratios with the "
                  "prior and normalised over the candidates, which gives a probability of 1.00. In phase four, the "
                  "red-flag rules and the severity schema propose levels and the most urgent one with CF at least 0.5 "
                  "is selected: T-PE proposes Emergency, which overrides the Urgent level proposed for the loss of "
                  "consciousness, and R-PE-HIGH recommends CT pulmonary angiography. Finally the run is written back "
                  "to the graph as provenance: 793 triples with 19 rule firings and 59 inferences.")
    code(s, X0, 100, 368, 374, """input:  facts F of a case; rules in strata S0..Sm
output: P (differential), t (triage), A (advice)
# 1  forward chaining
for each stratum Si:
  C ← rules of Si with premise true, CF ≥ 0.2
  sort C by salience, specificity, order
  for r in C:                   # fires once
    c ← CF(r) · CF(premise)
    concept k: F[k] ← combine(F[k], c)
    score s:   F[s] ← F[s] + points(r)
    diagnosis d: keep c for d
# 2  abduction by set covering
D ← pathologies whose profile covers
    every reported symptom
# 3  aggregation
for d in D:
  ℓ(d) ← log prior odds + Σ log λ
P ← normalise exp(ℓ) over D
# 4  triage and advice
t ← most urgent level proposed
A ← recommendation rules that fire
write the run to the graph as PROV-O""", size=11, label="Algorithm: one inference run (engine.py)")
    tx = X0 + 384
    text(s, tx, 100, X1 - tx, 30,
         [[("Patient T002456: ", {"font": BOLD, "color": BLUE}),
           ("loss of consciousness, haemoptysis, dyspnoea, hormones, active cancer, recent surgery, swollen calves",
            {"color": MUTED})]], size=10.5, after=0, spacing=1.0)
    rows = [["Phase", "Inference", "Patient T002456"],
            ["Build time", "Entailed triples", ":PulmonaryEmbolism :characterizedBy :E_45 · :Rule_T-PE a :RedFlagRule"],
            ["1  Forward chaining", "Clinical concept", "A-DVT: swelling in the calves → signs of DVT (1.00)"],
            ["", "Score", "Wells score 1 + 1 + 3 + 1.5 = 6.5"],
            ["", "Diagnostic belief", "D-PE-1 +0.70 · D-PE-3 +0.20"],
            ["2  Abduction", "Exclusion", "48 of 49 excluded, pulmonary neoplasm too (D-LCA-1, D-LCA-2 fired)"],
            ["3  Aggregation", "Probability", "prior 0.028 · rules 0.76 · 29 links → P = 1.00"],
            ["4  Triage", "Triage level", "T-PE Emergency (0.90) over T-SYNCOPE Urgent (0.70)"],
            ["4  Advice", "Recommendation", "R-PE-HIGH: CT pulmonary angiography"],
            ["After the run", "Provenance", "793 triples: 19 firings, 59 inferences"]]
    table(s, tx, 136, [96, 104, X1 - tx - 200], rows, size=9.5, row_h=34, fonts={0: BOLD, 1: BOLD},
          colors={0: BLUE, 1: INK})

    # 8 ------------------------------------------------------------- CF as LR
    s = d.content("Certainty factors as likelihood ratios",
                  "MYCIN combines certainty factors with a simple function. Heckerman showed in 1986 that a CF can be "
                  "read as a transformed likelihood ratio, and that combining CFs then equals multiplying likelihood "
                  "ratios. This had practical consequences: with dozens of strong contributions, several hypotheses "
                  "reached CF 1.0 within floating-point precision, and then the prior alone decided — every "
                  "stable-angina patient of the demo sample was ranked as unstable angina. Computing the same calculus "
                  "as a sum of log likelihood ratios removed the problem. The chart shows the mechanism: two "
                  "hypotheses receive contributions of CF 0.7, one of them two more than the other. MYCIN's formula "
                  "brings both to exactly 1.0 in double precision from 32 contributions on, so the prior decides; "
                  "their log odds stay two log likelihood ratios apart however many contributions arrive.")
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
    image(s, FIG / "saturation_deck.png", rx, 138, w=X1 - rx, h=140)
    text(s, rx, 281, X1 - rx, 16, ["Two more contributions of CF 0.7 for one hypothesis: a tie, or 2 log λ apart."], size=9.5, color=MUTED, after=0)
    stat(s, rx, 300, (X1 - rx - 14) / 2, 110, "96.9%", "top-1, classic combination (demo patients)", fill=REDTINT,
         num_color=RED)
    stat(s, rx + (X1 - rx + 14) / 2, 300, (X1 - rx - 14) / 2, 110, "99.3%", "top-1, sum of log likelihood ratios",
         fill=GREENTINT, num_color=GREEN)
    text(s, rx, 420, X1 - rx, 54, ["In log space the calculus does not saturate: evidence still counts at high belief."],
         size=12, font=BOLD, color=BLUE)

    # 9 ------------------------------------------------------------- triage and the consultation
    s = d.content("Triage and the consultation",
                  "Triage combines two kinds of knowledge. Red-flag rules come from the guidelines and do not depend on "
                  "the diagnosis: anaphylaxis, suspected coronary syndrome, pulmonary embolism, Ebola after travel. "
                  "The severity schema is a rule over the graph: every hypothesis with probability at least 0.2 "
                  "proposes the level of its DDXPlus severity. The most urgent level proposed with CF at least 0.5 "
                  "is selected: we accept over-triage to avoid under-triage. Recommendation rules then propose next "
                  "steps, each with its source. "
                  "In a consultation the system starts, like the DDXPlus platform, from age, sex and the chief "
                  "complaint, and runs the whole inference after every answer. The next question is chosen by "
                  "backward chaining from goals: the leading hypotheses and any severe one still possible. For each "
                  "goal it collects the unknown conditions of its rules and the unasked questions of its profile, and "
                  "asks the question with the largest expected information gain. The WHY facility names the goal, "
                  "the rule and the source. The rule-out phase was added after the evaluation showed it was needed: "
                  "stopping at P of 0.95 alone left 5 percent of the urgent patients with a non-urgent triage, because "
                  "Ebola and croup were improbable at the start, so travel to West Africa or stridor was never asked, "
                  "and the red-flag rules could not help since their questions had not been asked either. "
                  "A consultation can also start from a description in ordinary sentences, in English or Bahasa "
                  "Indonesia: a language model maps it onto the questionnaire, every finding must quote the "
                  "description, the user confirms the reading, and the consultation asks only what is still open. "
                  "The six steps on the right are the consultation algorithm; the report gives it as pseudocode "
                  "(Algorithm 4.2).")
    lw = 400
    text(s, X0, 100, lw, 20, ["Triage: safety first"], size=13.5, font=BOLD, color=BLUE)
    t_ex = [BY_ID[i] for i in ("T-ANA", "T-ACS", "T-PE", "T-EBOLA") if i in BY_ID]
    box(s, X0, 126, lw, 104, fill=TINT)
    text(s, X0 + 12, 132, lw - 24, 94,
         [[(f"Red-flag rules ({CAT['triage']}) ", {"font": BOLD, "color": BLUE}),
           ("from the guidelines, independent of the diagnosis:", {})]]
         + [[(f"{r.id} ", {"font": BOLD, "color": BLUE}), (r.name, {})] for r in t_ex],
         size=10.5, after=2, spacing=1.0)
    box(s, X0, 238, lw, 112, fill=TINT)
    text(s, X0 + 12, 244, lw - 24, 20, [[("Severity schema: ", {"font": BOLD, "color": BLUE}),
                                         ("every hypothesis with P ≥ 0.2 proposes", {})]], size=10.5, after=0)
    levels = [("1", "Emergency", RED), ("2", "Urgent", AMBER), ("3–4", "Non-urgent", BLUE), ("5", "Self-care", GREEN)]
    half = (lw - 24 - 12) / 2
    for i, (sev, lev, col) in enumerate(levels):
        x, y = X0 + 12 + (i % 2) * (half + 12), 272 + (i // 2) * 38
        box(s, x, y, 54, 30, fill=WHITE, line=LINE, radius=0.15)
        text(s, x, y, 54, 30, [f"sev. {sev}"], size=10, font=BOLD, color=MUTED, align=PP_ALIGN.CENTER,
             anchor=MSO_ANCHOR.MIDDLE, after=0)
        arrow(s, x + 58, y + 15, x + 74, y + 15, color=MUTED)
        box(s, x + 78, y, half - 78, 30, fill=col, radius=0.15)
        text(s, x + 78, y, half - 78, 30, [lev], size=10.5, font=BOLD, color=WHITE, align=PP_ALIGN.CENTER,
             anchor=MSO_ANCHOR.MIDDLE, after=0)
    text(s, X0, 358, lw, 56,
         [[("Level selection: ", {"font": BOLD, "color": BLUE}),
           ("the most urgent level proposed with CF ≥ 0.5 is selected; over-triage is accepted to avoid "
            "under-triage, and the explanation names the rule or hypothesis behind the level.", {})]],
         size=10.5, spacing=1.0)
    rec = BY_ID["R-PE-HIGH"]
    box(s, X0, 418, lw, 58, fill=CODEBG, line=LINE)
    text(s, X0 + 12, 418, lw - 24, 58,
         [[(f"Recommendation rules ({CAT['recommendation']}): ", {"font": BOLD, "color": BLUE}),
           ("next steps with their sources, e.g. ", {}), (f"{rec.id} ", {"font": BOLD, "color": BLUE}),
           (f"{rec.name} — {kb.short_citation(rec.source)}", {})]], size=10.5, spacing=1.0,
         anchor=MSO_ANCHOR.MIDDLE)
    cx = X0 + lw + 20
    text(s, cx, 100, X1 - cx, 20, ["Consultation: backward chaining"], size=13.5, font=BOLD, color=BLUE)
    steps = [("Start", "age, sex and the chief complaint, or a free-text description read by a language model "
                       "(open world: the rest is unknown)"),
             ("Goals", "the five leading hypotheses + every severe one with P ≥ 0.02"),
             ("Candidates", "unknown conditions of the goals' rules; unasked questions of their profiles"),
             ("Choose", "largest expected information gain, weighted up for guideline rules and severe goals"),
             ("Ask and explain", "WHY: goal, rule and source behind the question; HOW and triage at any time"),
             ("Stop or rule out", "at P ≥ 0.95, first ask the most specific question of each severe hypothesis "
              "still possible (P ≥ 10⁻⁴)")]
    for i, (head, body) in enumerate(steps):
        y = 126 + i * 46
        last = i == len(steps) - 1
        badge(s, cx, y + 2, 22, str(i + 1), fill=RED if last else BLUE, size=10)
        if not last:
            arrow(s, cx + 11, y + 26, cx + 11, y + 48, color=LINE, width=1.5)
        text(s, cx + 32, y, X1 - cx - 32, 46, [[(head + "  ", {"font": BOLD, "color": RED if last else BLUE,
                                                               "size": 11.5}), (body, {})]],
             size=10.5, after=0, spacing=1.0)
    tw = (X1 - cx - 12) / 2
    for i, (num, lab, fill, col) in enumerate(
            [(pct(C0["urgent_missed"]), "urgent cases missed without the rule-out phase", REDTINT, RED),
             (pct(C["urgent_missed"]), f"with it, for {C['questions_mean'] - C0['questions_mean']:.1f} extra "
                                       "questions", GREENTINT, GREEN)]):
        x = cx + i * (tw + 12)
        box(s, x, 410, tw, 66, fill=fill)
        text(s, x + 10, 410, 70, 66, [num], size=19, font=BOLD, color=col, anchor=MSO_ANCHOR.MIDDLE, after=0)
        text(s, x + 82, 410, tw - 90, 66, [lab], size=10, color=INK, anchor=MSO_ANCHOR.MIDDLE, after=0,
             spacing=1.0)

    # 10 ----------------------------------------------------------------- HOW, WHY, WHY NOT
    s = d.content("Explanations: HOW, WHY and WHY NOT",
                  "This is the HOW explanation of patient T002456, drawn by the web interface from the proof tree. "
                  "It is read from left to right: the patient's answers, highlighted in yellow, feed abstraction and "
                  "scoring rules; every rule box shows the certainty factor it adds and its source. The derived facts, "
                  "in grey, include the signs of deep-vein thrombosis and the Wells score of 6.5, and the diagnostic "
                  "rules D-PE-1 and D-PE-3 conclude pulmonary embolism, so a clinician can check each step against "
                  "the guideline. "
                  "The WHY panel shows what the consultation page says under 'Why this question': which hypothesis "
                  "the system is trying to confirm or rule out, with its probability and severity, which rule needs "
                  "the answer and the source of that rule. Here the consultation is in its rule-out phase: pulmonary "
                  "embolism is almost certain, but a severe alternative, acute pulmonary oedema, must be excluded "
                  "first. Beside the question, a triage tag, the red flags and the differential are updated after "
                  "every answer. "
                  "The WHY NOT explanation addresses a frequent clinical question, namely why another disease was not "
                  "concluded. For anaphylaxis in patient T002456, set covering first says which reported symptom the "
                  "pathology cannot explain — coughing up blood. Then every guideline rule for anaphylaxis is "
                  "evaluated condition by condition, each marked as holding, failing or not asked yet, so the user "
                  "sees exactly which criterion of Sampson's definition is not met.")
    image(s, FIG / "ui-proof-graph.png", X0, 102, w=520, h=222, border=True)
    hx = X0 + 536
    text(s, hx, 104, X1 - hx, 220,
         [[("HOW  ", {"font": BOLD, "color": BLUE, "size": 13.5}),
           ("a proof from the conclusion down to the patient's answers (yellow), through the abstraction and "
            "scoring rules; every rule shows the CF it adds and its source.", {})],
          [("Patient T002456: ", {"font": BOLD, "color": BLUE}),
           ("swelling of the calf and recent surgery → signs of DVT; Wells score 6.5 → D-PE-1 and D-PE-3 → "
            "pulmonary embolism.", {})],
          [("ddxes explain --patient T002456 --how", {"font": CODE, "color": MUTED, "size": 9.5})]],
         size=11, after=9, spacing=1.0)
    pw = (X1 - X0 - 16) / 2
    box(s, X0, 336, pw, 140, fill=WHITE, line=LINE)
    text(s, X0 + 14, 342, pw - 28, 130,
         [[("WHY  ", {"font": BOLD, "color": BLUE, "size": 13}), ("the goal behind every question",
                                                                  {"font": BOLD})],
          [("Question 4 · Ruling out a dangerous diagnosis", {"font": BOLD, "color": RED, "size": 9.5})],
          [("Have you ever had fluid in your lungs?", {"font": BOLD, "size": 11.5})],
          [("Before concluding Pulmonary embolism (P = 0.97) I must rule out Acute pulmonary edema, a severity-1 "
            "condition that is still possible (P = 0.022). ‘Fluid in your lungs’ is its most specific sign "
            "(likelihood ratio > 1000).", {"italic": True, "color": MUTED})],
          [("Names the goal, the rule and its source; HOW and triage can be asked at any time.", {})]],
         size=10.5, after=3, spacing=1.0)
    wx = X0 + pw + 16
    box(s, wx, 336, pw, 140, fill=WHITE, line=LINE)
    text(s, wx + 14, 342, pw - 28, 130,
         [[("WHY NOT  ", {"font": BOLD, "color": BLUE, "size": 13}), ("the conditions that failed", {"font": BOLD})],
          [("Why not anaphylaxis for T002456?", {"font": BOLD, "size": 11.5})],
          [("Set covering: ", {"font": BOLD}), ("no link from this pathology to coughing up blood, so it cannot "
                                                "explain the reported symptoms.", {})],
          [("D-ANA-1 ", {"font": BOLD, "color": BLUE}), ("(Sampson criterion 2) did not fire:  ", {}),
           ("✗ ", {"font": BOLD, "color": RED}), ("allergen contact   ", {}),
           ("✓ ", {"font": BOLD, "color": GREEN}), ("at least two organ systems", {})],
          [("Every condition of every rule for the pathology is marked as holding, failing or not asked yet.", {})]],
         size=10.5, after=3, spacing=1.0)

    # 11 ------------------------------------------------------------------ provenance
    s = d.content("Provenance of inferences (W3C PROV-O)",
                  "After a consultation, the inferences are written back to the graph with the W3C PROV-O vocabulary: "
                  "each inference was generated by a rule firing, which used certain facts and beliefs and followed a "
                  "rule, which cites a source. So SPARQL can answer audit questions such as: why is this case an "
                  "emergency, which rule fired on which facts, and which guideline does that rule encode? Here the "
                  "answer is T-PE, on the belief in pulmonary embolism, citing the 2019 ESC guideline. "
                  "The same audit works in GraphDB, the triple store. Every consultation gets its own case "
                  "identifier, C followed by eight hexadecimal digits, kept in the browser's cookie with the answers, "
                  "and every node of its run carries that identifier, so runs never share a node. One button on the "
                  "SPARQL tab of a result does three things. First, the server rebuilds the run by replaying the "
                  "answers in the cookie and builds its PROV-O graph, 444 triples for the consultation in the report. "
                  "Second, it sends that graph with an HTTP PUT to GraphDB's RDF4J interface, into the named graph "
                  "provenance-<case>; a PUT with a context replaces exactly that graph and no other, so pressing the "
                  "button again after more answers replaces the earlier copy. Third, GraphDB adds its OWL 2 RL "
                  "inferences. Open in GraphDB then opens the same query in the Workbench, and it returns the same "
                  "rows as the page, with or without inferred data. Two details make that hold. GraphDB also reasons "
                  "over the provenance: because :firedRule is a sub-property of prov:used, it concludes that every "
                  "firing used its own rule, so the triage query leaves the rule out. And GROUP_CONCAT joins values "
                  "in an order SPARQL leaves open, which in rdflib even changes with Python's hash seed, so the "
                  "symptom lists are concatenated from a sorted subquery. Checked cell by cell on T002456 and on the "
                  "consultation 'male 25 tahun, cough berdahak, panas, meriang, ngomel2': all four queries give the "
                  "same rows in both stores. The deployed site cannot reach a GraphDB on your computer; there you "
                  "download the Turtle file and load it with load_graphdb.py --add-file. Section 5.6 of the report "
                  "describes all of this, with a sequence diagram and Algorithm 5.1.")
    code(s, X0, 106, 400, 222, """# q08: why is this case an emergency?
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
    table(s, X0, 336, [74, 46, 120, 160], rows, size=10, row_h=30)
    box(s, X0, 406, 400, 84, fill=CREAM)
    steps = [("1", "PUT the run's PROV-O graph into its own named graph, provenance-<case>"),
             ("2", "GraphDB replaces only that graph and adds its OWL 2 RL inferences"),
             ("3", "Open in GraphDB runs the same query: the same rows as on the page")]
    text(s, X0 + 12, 408, 376, 80,
         [[("In GraphDB: ", {"font": BOLD, "color": BLUE}), ("one click on the SPARQL tab of a result", {})]]
         + [[(n + "  ", {"font": BOLD, "color": AMBER}), (line, {})] for n, line in steps],
         size=10.5, after=3, spacing=1.0, anchor=MSO_ANCHOR.MIDDLE)
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

    # 12 ----------------------------------------------------------------- results: accuracy, triage, ablation
    d.part = "PART 3 · RESULTS"
    es, nb, hy = MAIN["es"], MAIN["nb"], MAIN["hybrid"]
    miss3 = round((1 - es["top3"]) * MAIN["patients"])
    s = d.content("Results: accuracy, triage and ablation",
                  f"On the full test split of {fmt(MAIN['patients'])} patients the expert system ranks the true "
                  f"pathology first for {pct(es['top1'], 2)} of them, ahead of the naive-Bayes classifier built from the "
                  f"same counts at {pct(nb['top1'], 2)}; the hybrid reaches {pct(hy['top1'], 2)}. The true pathology is "
                  f"missing from the DDX-ES top three for only {'one patient' if miss3 == 1 else f'{fmt(miss3)} patients'}. "
                  "The differential metrics are low by design, because DDX-ES "
                  "commits to a diagnosis while the reference differential lists about ten. For triage, every emergency "
                  f"is triaged as an emergency. Under-triage is {pct(T['under'], 2)}, and over-triage, about one patient "
                  "in five, is the intended safety-first trade-off: guideline red flags such as suspected pulmonary "
                  "embolism assign Emergency where the DDXPlus severity scale assigns a lower level. "
                  "The ablation on the left switches parts of the engine off on a stratified sample. The guideline "
                  "rules alone rank the true pathology first for only 30 percent of the patients: they describe "
                  "syndromes that several pathologies share and were never written to separate them. Set covering "
                  "alone reaches 79 percent, both together 88. The calibrated knowledge-base links account for the "
                  "ranking accuracy, 99 percent with covering. The guideline rules therefore contribute elsewhere: "
                  "concepts and scores, triage, recommendations and the explanations.")
    tiles = [(pct(es["top1"], 2), "top-1, DDX-ES (no training)", TINT, BLUE),
             (pct(nb["top1"], 2), "top-1, naive Bayes on the same counts", CODEBG, MUTED),
             (pct(hy["top1"], 2), "top-1, hybrid of the two", CODEBG, MUTED),
             (pct(T["emergency_recall"]), "of emergencies triaged as Emergency", GREENTINT, GREEN)]
    tw = (X1 - X0 - 3 * 14) / 4
    for i, (num, lab, fill, col) in enumerate(tiles):
        stat(s, X0 + i * (tw + 14), 102, tw, 90, num, lab, fill=fill, num_color=col, num_size=24)
    image(s, FIG / "ablation.png", X0, 202, w=486, h=213)
    text(s, X0, 420, 486, 34,
         [[("Ablation ", {"font": BOLD, "color": BLUE}),
           (f"({fmt(ABL['patients'])} stratified test patients): guideline rules alone "
            f"{pct(AB['Guideline rules only']['top1'])} top-1, set covering alone {pct(AB['Set covering only']['top1'])}, "
            f"calibrated links + covering {pct(AB['KB evidence + covering']['top1'])}.", {})]],
         size=10.5, spacing=1.0)
    image(s, FIG / "triage_matrix.png", X0 + 502, 200, w=X1 - X0 - 502, h=252)
    text(s, X0, 458, X1 - X0, 20,
         [f"Top-3 ≥ 99.99% for all three · DDF1 {pct(es['ddf1'])} by design (one committed diagnosis) · under-triage "
          f"{pct(T['under'], 2)}, over-triage {pct(T['over'])} · {MAIN['ms_per_patient']:.1f} ms per patient"],
         size=10.5, color=MUTED, align=PP_ALIGN.CENTER)

    # 13 ----------------------------------------------------------------- consultation and rule validation
    s = d.content("Consultation and rule validation",
                  "We simulated the consultation on the 980 demo patients by answering every question from the "
                  "patient's record. With a stopping threshold of 0.95, DDX-ES asks 3.8 questions on average after "
                  "the chief complaint and ranks the true pathology first for about 98 percent; at 0.99 it asks 4.2 "
                  "for 98.8 percent. The reinforcement-learning agents of the DDXPlus paper ask 6.7 and 18 questions "
                  "after training on a million patients; the comparison is indicative, because they report the whole "
                  "test split. The rule-out phase improves triage safety at the cost of 0.7 extra questions: early in "
                  "the interview almost half of the urgent patients still have a non-urgent leading hypothesis, so "
                  "stopping too early is unsafe. Red-flag rules made no difference during the consultation, because "
                  "their questions stay unasked; an early red-flag screen is future work. "
                  f"The optional free-text intake was tested on {INTAKE['patients']} demo patients ({INTAKE_SCOPE}). "
                  "A language model wrote each patient's description in English and in Indonesian; the intake read "
                  f"them with {pct(IL['en']['precision_vs_record'])} and {pct(IL['id']['precision_vs_record'])} "
                  f"precision against the records. The consultation then asked {INTAKE_Q} questions instead of "
                  f"{INTAKE['baseline']['questions']:.1f}, with a top-1 accuracy of {pct(IL['en']['top1'])} and "
                  f"{pct(IL['id']['top1'])} against {pct(INTAKE['baseline']['top1'])}: a misread finding is rarely "
                  "corrected when almost no question is asked. "
                  f"On the right, every rule was run on a stratified sample of {fmt(RV['patients'])} validation "
                  f"patients. All {len(RULES)} rules fire at least once. For the {len(POS)} positive diagnostic rules "
                  "the median precision is 100 percent. The weakest rules are broad screening criteria such as the "
                  "WHO influenza-like-illness definition or the NICE lung-cancer symptoms, which are written for "
                  "sensitivity. The certainty factors assigned from the sources agree only moderately with the "
                  "measured precision (the scatter on the right), which is a reason to keep the calibrated links in the "
                  "aggregation and to calibrate the rules from data in future work.")
    lw = 420
    text(s, X0, 100, lw, 20, ["Interactive consultation (980 simulated patients)"], size=12.5, font=BOLD, color=BLUE)
    rows = [["System", "Questions", "Top-1", "Trained on patients"],
            ["AARLC (Fansi Tchango et al., 2022)", "6.73", "99.21%", "yes (RL)"],
            ["BASD (Fansi Tchango et al., 2022)", "17.99", "97.15%", "yes (RL)"],
            ["DDX-ES, stop at P ≥ 0.95", f"{C['questions_mean']:.1f}", pct(C["top1"]), "no (counts only)"],
            ["DDX-ES, stop at P ≥ 0.99", f"{C99['questions_mean']:.1f}", pct(C99["top1"]), "no (counts only)"]]
    table(s, X0, 124, [190, 70, 62, 98], rows, size=10, row_h=28, bold_rows=(3, 4),
          aligns=[PP_ALIGN.LEFT, PP_ALIGN.CENTER, PP_ALIGN.CENTER, PP_ALIGN.LEFT])
    text(s, X0, 272, lw, 18, ["Effect of the rule-out phase"], size=11.5, font=BOLD, color=BLUE)
    rows = [["", "questions", "top-1", "under-triage", "urgent missed"],
            ["without", f"{C0['questions_mean']:.1f}", pct(C0["top1"]), pct(C0["under_triage"]), pct(C0["urgent_missed"])],
            ["with", f"{C['questions_mean']:.1f}", pct(C["top1"]), pct(C["under_triage"]), pct(C["urgent_missed"])]]
    table(s, X0, 294, [76, 82, 76, 92, 94], rows, size=10, row_h=26, bold_rows=(2,),
          aligns=[PP_ALIGN.LEFT] + [PP_ALIGN.CENTER] * 4)
    text(s, X0, 380, lw, 18, [f"Starting from free text ({INTAKE['patients']} patients)"], size=11.5, font=BOLD,
         color=BLUE)
    rows = [["Consultation starts from", "questions", "top-1", "precision"],
            ["the main complaint", f"{INTAKE['baseline']['questions']:.1f}", pct(INTAKE["baseline"]["top1"]), "—"],
            ["an English description", f"{IL['en']['questions']:.1f}", pct(IL["en"]["top1"]),
             pct(IL["en"]["precision_vs_record"])],
            ["an Indonesian description", f"{IL['id']['questions']:.1f}", pct(IL["id"]["top1"]),
             pct(IL["id"]["precision_vs_record"])]]
    table(s, X0, 400, [180, 80, 80, 80], rows, size=9.5, row_h=19,
          aligns=[PP_ALIGN.LEFT, PP_ALIGN.CENTER, PP_ALIGN.CENTER, PP_ALIGN.CENTER])
    rx = X0 + lw + 24
    text(s, rx, 100, X1 - rx, 20, [f"Every rule on {fmt(RV['patients'])} held-out patients"], size=12.5, font=BOLD,
         color=BLUE)
    tw = (X1 - rx - 24) / 3
    for i, (num, lab) in enumerate([(f"{sum(1 for r in RV['rows'] if r['fires'] > 0)}/{len(RULES)}", "rules fire"),
                                    (pct(statistics.median(r["precision"] for r in POS), 0),
                                     "median precision, diagnostic rules"),
                                    (f"{statistics.median(r['lift'] for r in POS):.1f}", "median lift")]):
        x = rx + i * (tw + 12)
        box(s, x, 124, tw, 74, fill=TINT)
        text(s, x + 10, 128, tw - 20, 34, [num], size=19, font=BOLD, color=BLUE, anchor=MSO_ANCHOR.BOTTOM, after=0)
        text(s, x + 10, 164, tw - 20, 32, [lab], size=9.5, color=MUTED, after=0, spacing=1.0)
    image(s, FIG / "rule_calibration.png", rx, 206, w=X1 - rx, h=222)
    text(s, rx, 432, X1 - rx, 44,
         ["The weakest rules (labelled) are screening criteria written for sensitivity; the assigned CFs agree only "
          "moderately with the measured precision."], size=10, color=MUTED, spacing=1.0)

    # 14 ----------------------------------------------------------------- conclusions, limitations, future work
    d.part = "PART 4 · CONCLUSION"
    s = d.content("Conclusions, limitations and future work",
                  "To conclude. One RDF/OWL graph holds the DDXPlus knowledge and 192 cited rules as data, and the "
                  "OWL closure contributes facts the engine relies on. A production-rule engine on this graph ranks "
                  "the true pathology first for 99.4 percent of the test patients without any training, better than "
                  "a naive-Bayes model on the same counts, and misses no emergency. Every conclusion is explained. "
                  "Reading certainty factors as likelihood ratios fixed a saturation failure, and the rule-out phase "
                  "improved consultation safety. An optional language-model intake reads free text in English or "
                  "Indonesian; it only maps the words onto the questionnaire. "
                  "The main limitation is that the data are synthetic: the patients were generated from the same "
                  "knowledge base we use, so set covering is exact here; on real patients, with comorbidities and "
                  "noisy answers, accuracy would be lower and the soft covering mode would be needed. Summing log "
                  "likelihood ratios assumes independent evidence, and guideline rules partly re-count answers the "
                  "links already count, so posteriors are overconfident. Some rules only approximate their criteria, "
                  "the certainty factors were assigned on a fixed scale, and covering looks for a single disorder. "
                  "Next steps: clinicians and real data, calibrated rule CFs, an early red-flag screen, SHACL "
                  "validation, an engine that queries a triple store, an Indonesian questionnaire, and an evaluation "
                  "of the intake on descriptions written by patients. Finally, DDX-ES is an educational prototype, not "
                  "a medical device.")
    concl = [f"One RDF/OWL 2 graph holds DDXPlus and {len(RULES)} rules from {len(CITED)} sources; OWL 2 RL adds "
             "facts the engine uses.",
             f"{pct(es['top1'], 2)} top-1 on {fmt(MAIN['patients'])} test patients without training (naive Bayes "
             f"{pct(nb['top1'], 2)}); no emergency under-triaged.",
             "HOW, WHY, WHY NOT and PROV-O: every conclusion is traced to its rule, facts and source.",
             f"CFs read as likelihood ratios avoid saturation; the rule-out phase cuts missed urgent cases from "
             f"{pct(C0['urgent_missed'])} to {pct(C['urgent_missed'])}.",
             f"An optional language-model intake reads English or Indonesian free text "
             f"({pct(IL['en']['precision_vs_record'])} / {pct(IL['id']['precision_vs_record'])} precision); the rules "
             "still decide."]
    lims = ["Synthetic data from the same knowledge base: covering is exact only here; the triage reference is "
            "synthetic too.",
            "Summed log LRs assume independent evidence, and rules re-count answers: posteriors are overconfident.",
            f"{FID['partial'] + FID['proxy']} rules need vital signs, tests, examination or duration that the "
            "questionnaire cannot ask.",
            "Rule CFs are assigned on a fixed scale; only moderate agreement with measured precision.",
            "Covering looks for one pathology that explains everything."]
    fut = ["Validate the rules with clinicians and on real, de-identified consultations.",
           "Calibrate the rule CFs from data, keeping their structure.",
           "An explicit red-flag screening step early in the consultation.",
           "Validate the rule base with SHACL; let the engine query a triple store such as GraphDB.",
           "Questionnaire and explanations in Bahasa Indonesia; test the intake on descriptions written by patients."]
    cw = (X1 - X0 - 2 * 14) / 3
    for i, (head, fill, col, items) in enumerate([("Conclusions", TINT, BLUE, concl), ("Limitations", CREAM, AMBER, lims),
                                                  ("Future work", GREENTINT, GREEN, fut)]):
        x = X0 + i * (cw + 14)
        box(s, x, 102, cw, 318, fill=fill)
        text(s, x + 12, 110, cw - 24, 22, [head], size=14, font=BOLD, color=col)
        text(s, x + 12, 142, cw - 24, 272, items, size=11.5, after=9, bullets=True, spacing=1.0)
    box(s, X0, 428, X1 - X0, 48, fill=REDTINT)
    text(s, X0 + 14, 428, X1 - X0 - 28, 48,
         [[("Ethics: ", {"font": BOLD, "color": RED}),
           ("an educational prototype on synthetic data: not a medical device, not validated with clinicians or real "
            "patients, not for medical decisions (the interface says so on every page). The rules paraphrase cited "
            "criteria, which remain the authority.", {})]], size=10.5, anchor=MSO_ANCHOR.MIDDLE, spacing=1.0)

    # 15 ------------------------------------------------------------------ how to run
    s = d.content("Repository and reproducibility",
                  "Everything is in the public repository on GitHub, github.com/zidanarsyad/ddx-expert-system. The "
                  "README explains how to install and run "
                  "it: a virtual environment, two pip commands, then the web interface (ddxes serve) or the "
                  "ddxes command line. The knowledge graph and the processed data are committed, so the system runs "
                  "immediately; the scripts rebuild everything from the raw DDXPlus files, including the figures, the "
                  "report and this deck. There are 134 automated tests. The optional free-text intake needs an API key "
                  "for Groq, Gemini or OpenRouter in a local .env file, which is never committed. GraphDB is optional "
                  "too: with GraphDB Desktop running, scripts/load_graphdb.py loads the graph into it, and the SPARQL "
                  "tab of each result writes that run there.")
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
python -I scripts/load_graphdb.py   # optional: GraphDB
pytest                        # 134 tests""", size=11.5)
    layout = [("knowledge/rules.yaml", "the rule base and its sources"),
              ("kg/*.ttl", "ontology, knowledge base, rules, statistics, cases, closure"),
              ("src/ddxes/", "engine, certainty factors, explanation, provenance, consultation, GraphDB"),
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

    # 16 --------------------------------------------------------------------- closing
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
