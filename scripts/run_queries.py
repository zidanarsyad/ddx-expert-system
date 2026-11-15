"""Run queries/*.rq over the knowledge graph and save the results as CSV.

The graph = ontology + kb + rules + stats + inferred closure + the 980 demo patients + the provenance of one
consultation (patient T002456, pulmonary embolism) written back by the engine. Each query also runs on the same
graph WITHOUT kg/inferred.ttl, into queries/results-no-inference/, to show what OWL 2 RL reasoning contributes.

Usage (from the repository root):  python -I scripts/run_queries.py
"""
import csv
import pathlib
import sys

from rdflib import Graph

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from ddxes.case import Case  # noqa: E402
from ddxes.engine import InferenceEngine  # noqa: E402
from ddxes.evaluate import read_split  # noqa: E402,F401
from ddxes.kb import KnowledgeBase  # noqa: E402
from ddxes.paths import KG, PROCESSED, QUERIES  # noqa: E402
from ddxes.provenance import result_to_graph  # noqa: E402
import json  # noqa: E402

DEMO = "T002456"


def run(g: Graph, out_dir: pathlib.Path) -> dict:
    out_dir.mkdir(exist_ok=True)
    counts = {}
    for q in sorted(QUERIES.glob("q*.rq")):
        res = g.query(q.read_text(encoding="utf-8"))
        with open(out_dir / (q.stem + ".csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if res.type == "ASK":
                w.writerow(["ask"])
                w.writerow([res.askAnswer])
                counts[q.stem] = str(res.askAnswer)
                continue
            w.writerow([str(v) for v in res.vars])
            n = 0
            for row in res:
                w.writerow(["" if x is None else str(x).replace("http://example.org/ddx#", ":") for x in row])
                n += 1
            counts[q.stem] = n
    return counts


def main():
    kb = KnowledgeBase.load()
    rec = next(r for r in json.loads((PROCESSED / "sample_patients.json").read_text(encoding="utf-8")) if r["id"] == DEMO)
    prov = result_to_graph(InferenceEngine(kb).run(Case.from_ddxplus(rec, kb)), kb, include_case=False)
    full = Graph()
    for name in ("ontology.ttl", "kb.ttl", "rules.ttl", "stats.ttl", "cases.ttl", "inferred.ttl"):
        full.parse(KG / name, format="turtle")
    without = Graph()
    for t in full:
        without.add(t)
    for t in Graph().parse(KG / "inferred.ttl", format="turtle"):
        without.remove(t)
    for t in prov:
        full.add(t)
        without.add(t)
    with_inf = run(full, QUERIES / "results")
    no_inf = run(without, QUERIES / "results-no-inference")
    print(f"{'query':34s} {'with inference':>15s} {'without':>10s}")
    for k in with_inf:
        print(f"{k:34s} {str(with_inf[k]):>15s} {str(no_inf[k]):>10s}")


if __name__ == "__main__":
    main()
