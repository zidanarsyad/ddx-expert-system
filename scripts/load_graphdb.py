"""Load the DDX-ES knowledge graph into GraphDB, to browse it and run the example queries in the Workbench.

Optional: DDX-ES itself needs no database server, since the engine reads the graph with rdflib. This script
copies the same graph into a running GraphDB (Desktop or server, http://localhost:7200 by default).

It creates the repository `ddx-es` with the OWL 2 RL ruleset (owl2-rl-optimized) and loads, each into its own
named graph, the ontology, the knowledge base, the rule base, the calibration statistics, the 980 demo patients
and the provenance of one consultation (patient T002456) written back by the engine: the graph that
scripts/run_queries.py queries. kg/inferred.ttl, the closure computed by owlrl, is not loaded, because GraphDB
derives the entailments itself. The script then runs queries/*.rq on GraphDB with and without the inferred
statements and compares the answers with queries/results/ (rdflib + owlrl).

GraphDB reasons over every triple it holds, while kg/inferred.ttl is the closure of ontology + kb + rules only.
So GraphDB entails a little more, and three answers differ: it also types the three rule schemas of stats.ttl
as :Rule (q01, q02), and applies `:firedRule rdfs:subPropertyOf prov:used` to the provenance (q08). Without
inferred statements, every answer equals queries/results-no-inference/.

Usage (from the repository root, with GraphDB running):
    python -I scripts/load_graphdb.py              # create the repository if needed and (re)load it
    python -I scripts/load_graphdb.py --recreate   # delete the repository and build it again
    python -I scripts/load_graphdb.py --url http://localhost:7200 --repo ddx-es
"""
import argparse
import csv
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

NS = "http://example.org/ddx#"
GRAPHS = "http://example.org/ddx/graph/"
FILES = ("ontology", "kb", "rules", "stats", "cases")
DEMO = "T002456"
CONFIG = """\
@prefix rdfs:    <http://www.w3.org/2000/01/rdf-schema#> .
@prefix rep:     <http://www.openrdf.org/config/repository#> .
@prefix sr:      <http://www.openrdf.org/config/repository/sail#> .
@prefix sail:    <http://www.openrdf.org/config/sail#> .
@prefix graphdb: <http://www.ontotext.com/config/graphdb#> .

[] a rep:Repository ;
    rep:repositoryID "REPO_ID" ;
    rdfs:label "DDX-ES knowledge graph (differential diagnosis and triage on DDXPlus)" ;
    rep:repositoryImpl [
        rep:repositoryType "graphdb:SailRepository" ;
        sr:sailImpl [
            sail:sailType "graphdb:Sail" ;
            graphdb:ruleset "owl2-rl-optimized" ;
            graphdb:disable-sameAs "true" ;
            graphdb:check-for-inconsistencies "true" ;
            graphdb:base-URL "http://example.org/ddx#" ;
            graphdb:enable-context-index "true" ;
            graphdb:enablePredicateList "true" ;
            graphdb:enable-literal-index "true" ;
            graphdb:in-memory-literal-properties "true"
        ]
    ] .
"""
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never route localhost through a proxy


def call(method: str, url: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with OPENER.open(req, timeout=900) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {url}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:800]}")
    except urllib.error.URLError as e:
        sys.exit(f"{method} {url}: {e.reason}. Is GraphDB running? Start GraphDB Desktop first.")


def sparql(base: str, repo: str, query: str, infer: bool = True) -> dict:
    body = urllib.parse.urlencode({"query": query, "infer": str(infer).lower()}).encode()
    return json.loads(call("POST", f"{base}/repositories/{repo}", body,
                           {"Content-Type": "application/x-www-form-urlencoded",
                            "Accept": "application/sparql-results+json"}))


def upload(base: str, repo: str, data: bytes, graph: str) -> None:
    t = time.perf_counter()
    ctx = urllib.parse.quote(f"<{GRAPHS}{graph}>", safe="")
    call("POST", f"{base}/repositories/{repo}/statements?context={ctx}", data, {"Content-Type": "text/turtle"})
    print(f"  loaded {graph:20s} into <{GRAPHS}{graph}>  ({time.perf_counter() - t:.1f} s)")


def provenance_ttl() -> bytes:
    """The engine's conclusions for the demo patient, as PROV-O (the same graph as scripts/run_queries.py)."""
    from ddxes.case import Case
    from ddxes.engine import InferenceEngine
    from ddxes.kb import KnowledgeBase
    from ddxes.paths import PROCESSED
    from ddxes.provenance import result_to_graph
    kb = KnowledgeBase.load()
    patients = json.loads((PROCESSED / "sample_patients.json").read_text(encoding="utf-8"))
    rec = next(r for r in patients if r["id"] == DEMO)
    g = result_to_graph(InferenceEngine(kb).run(Case.from_ddxplus(rec, kb)), kb, include_case=False)
    return g.serialize(format="turtle").encode("utf-8")


def answer(res: dict):
    """ASK -> 'true' / 'false'; SELECT -> rows, with the ddx namespace shortened to ':' as in run_queries.py."""
    if "boolean" in res:
        return str(res["boolean"]).lower()
    names = res["head"]["vars"]
    return [[b[v]["value"].replace(NS, ":") if v in b else "" for v in names] for b in res["results"]["bindings"]]


def saved(path: pathlib.Path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    return rows[1][0].lower() if rows[0] == ["ask"] else rows[1:]


def norm(x):
    """Order-free comparison: numbers by value, GROUP_CONCAT lists as sorted items."""
    if isinstance(x, str):
        return x

    def cell(c: str) -> str:
        try:
            return f"{float(c):.6g}"
        except ValueError:
            return ", ".join(sorted(c.split(", ")))
    return sorted(tuple(cell(c) for c in row) for row in x)


def show(x) -> str:
    return x if isinstance(x, str) else str(len(x))


def main() -> int:
    ap = argparse.ArgumentParser(description="Load the DDX-ES knowledge graph into GraphDB.")
    ap.add_argument("--url", default="http://localhost:7200", help="GraphDB address (default %(default)s)")
    ap.add_argument("--repo", default="ddx-es", help="repository id (default %(default)s)")
    ap.add_argument("--recreate", action="store_true", help="delete the repository and create it again")
    a = ap.parse_args()
    base, repo = a.url.rstrip("/"), a.repo
    accept = {"Accept": "application/json"}

    version = json.loads(call("GET", f"{base}/rest/info/version", headers=accept))
    print(f"GraphDB {version.get('productVersion')} ({version.get('productType')}) at {base}")
    repos = {r["id"] for r in json.loads(call("GET", f"{base}/rest/repositories", headers=accept))}
    if repo in repos and a.recreate:
        call("DELETE", f"{base}/rest/repositories/{repo}")
        print(f"deleted repository '{repo}'")
        repos.discard(repo)
    if repo not in repos:
        b = uuid.uuid4().hex
        body = (f'--{b}\r\nContent-Disposition: form-data; name="config"; filename="{repo}-config.ttl"\r\n'
                f"Content-Type: text/turtle\r\n\r\n{CONFIG.replace('REPO_ID', repo)}\r\n--{b}--\r\n").encode("utf-8")
        call("POST", f"{base}/rest/repositories", body, {"Content-Type": f"multipart/form-data; boundary={b}"})
        print(f"created repository '{repo}' (ruleset owl2-rl-optimized)")

    # Start from an empty repository: re-importing blank-node OWL axioms would duplicate them.
    call("DELETE", f"{base}/repositories/{repo}/statements")
    t0 = time.perf_counter()
    for name in FILES:
        upload(base, repo, (ROOT / "kg" / f"{name}.ttl").read_bytes(), name)
    upload(base, repo, provenance_ttl(), f"provenance-{DEMO}")

    def count(graph: str) -> int:
        res = sparql(base, repo, f"SELECT (COUNT(*) AS ?n) FROM <{graph}> WHERE {{ ?s ?p ?o }}")
        return int(res["results"]["bindings"][0]["n"]["value"])
    explicit, implicit = count("http://www.ontotext.com/explicit"), count("http://www.ontotext.com/implicit")
    print(f"{explicit:,} explicit triples, {implicit:,} inferred by GraphDB, {explicit + implicit:,} in total "
          f"({time.perf_counter() - t0:.0f} s)")

    print(f"\n{'':33s} {'GraphDB':^24s}   {'rdflib + owlrl':^16s}")
    print(f"{'query':33s} {'with inference':>14s} {'explicit':>9s}   {'queries/results':>16s}")
    differs = stale = 0
    for q in sorted((ROOT / "queries").glob("q*.rq")):
        text = q.read_text(encoding="utf-8")
        g_inf, g_exp = answer(sparql(base, repo, text)), answer(sparql(base, repo, text, infer=False))
        r_inf = saved(ROOT / "queries" / "results" / f"{q.stem}.csv")
        r_exp = saved(ROOT / "queries" / "results-no-inference" / f"{q.stem}.csv")
        mark = ""
        if norm(g_exp) != norm(r_exp):          # same explicit triples, so this means the files changed
            mark, stale = " (!)", stale + 1
        elif norm(g_inf) != norm(r_inf):
            mark, differs = " *", differs + 1
        print(f"{q.stem:33s} {show(g_inf):>14s} {show(g_exp):>9s}   {show(r_inf):>16s}{mark}")
    if differs:
        print("\n* GraphDB entails more than kg/inferred.ttl, the closure of ontology + kb + rules only: see the note at "
              "the top of this script.")
    if stale:
        print("(!) The explicit triples answer differently from queries/results-no-inference/: run "
              "scripts/run_queries.py again.")
    print(f"\nWorkbench: {base}  (choose the repository '{repo}' at the top right)")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
