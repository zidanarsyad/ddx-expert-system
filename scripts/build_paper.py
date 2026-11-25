"""Build the compact paper: docs/paper.md + results/*.json -> docs/paper.pdf (IEEE conference layout).

The paper summarises the report in six pages: the description, how the system works (concepts, workflow and
algorithms), the results and the conclusion. Its numbers are the placeholders <<name>> of the report, filled by
build_report.values(), so the paper and the report cannot disagree. Pandoc turns the Markdown into LaTeX with citeproc
(docs/references.json, the report's Harvard style); build_report.polish_tex makes the tables floats, since a
two-column page has no room for longtable; XeLaTeX makes the PDF.

Usage (from the repository root):  python -I scripts/build_paper.py [--out PATH]
Needs pandoc >= 3 and a TeX distribution with xelatex and the IEEEtran class.
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_report import DOCS, polish_tex, values  # noqa: E402


def main():
    out_pdf = pathlib.Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else DOCS / "paper.pdf"
    V = values()
    text = (DOCS / "paper.md").read_text(encoding="utf-8")
    missing = sorted(set(re.findall(r"<<(\w+)>>", text)) - set(V))
    if missing:
        sys.exit(f"unfilled placeholders: {missing}")
    text = re.sub(r"<<(\w+)>>", lambda m: V[m.group(1)], text)
    build = DOCS / "build"
    build.mkdir(exist_ok=True)
    (build / "paper.md").write_text(text, encoding="utf-8")
    cmd = ["pandoc", "build/paper.md", "-s", "-o", "build/paper.tex", "--citeproc", "--bibliography", "references.json",
           "--csl", "harvard-fmipa-ugm.csl", "-H", "paper-header.tex", "--number-sections", "-M", "link-citations=true",
           "-V", "documentclass=IEEEtran", "-V", "classoption=conference", "-V", "classoption=a4paper", "-V", "indent",
           "-V", "mainfont=Times New Roman", "-V", "monofont=Consolas", "-V", "monofontoptions=Scale=0.88",
           "-V", "monofontoptions=HyphenChar=None", "-V", "mathfont=Cambria Math",
           "-V", "colorlinks=true", "-V", "linkcolor=black", "-V", "citecolor=black", "-V", "urlcolor=black",
           "--syntax-highlighting=none"]
    subprocess.run(cmd, check=True, cwd=DOCS)
    tex_file = build / "paper.tex"
    tex_file.write_text(polish_tex(tex_file.read_text(encoding="utf-8")), encoding="utf-8")
    for _ in range(2):   # references to figures, tables and algorithms
        run = subprocess.run(["xelatex", "-interaction=nonstopmode", "-halt-on-error", "-output-directory=build",
                              "build/paper.tex"], cwd=DOCS, capture_output=True, text=True, encoding="utf-8",
                             errors="replace")
        if run.returncode != 0:
            print(run.stdout[-3000:])
            sys.exit("xelatex failed")
    log = (build / "paper.log").read_text(encoding="utf-8", errors="replace")
    missing_chars = sorted(set(re.findall(r"Missing character: There is no (.) ", log)))
    if missing_chars:
        print("WARNING: glyphs missing in the fonts:", missing_chars)
    out_pdf.write_bytes((build / "paper.pdf").read_bytes())
    print(f"wrote {out_pdf}")


if __name__ == "__main__":
    main()
