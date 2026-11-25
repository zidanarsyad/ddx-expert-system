"""Writing runs into GraphDB, without a GraphDB: the requests are checked, not sent.

The queries of the SPARQL tab must give the same rows in GraphDB as in the web interface. GraphDB reasons over the
provenance as well (OWL 2 RL), so the rows are also checked on a graph that holds what that reasoning adds.
"""
import json
import os
import pathlib
import subprocess
import sys
import urllib.parse

import pytest

from ddxes import graphdb
from ddxes.paths import DDX, PROCESSED

DEMO = "T002456"
SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
needs_demo = pytest.mark.skipif(not (PROCESSED / "sample_patients.json").exists(), reason="demo patients not built")
ROWS = """
import json, sys
sys.path.insert(0, sys.argv[1])
from ddxes.web import views as V
svc = V.Services.get()
print(json.dumps([q["rows"] for q in V.result_queries(svc, svc.case_result(sys.argv[2]))["queries"]]))
"""


def test_put_replaces_the_named_graph(monkeypatch):
    seen = []

    class Done:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(graphdb, "_open", lambda req, timeout: seen.append(req) or Done())
    monkeypatch.delenv("DDXES_GRAPHDB_URL", raising=False)
    monkeypatch.delenv("DDXES_GRAPHDB_REPO", raising=False)
    graphdb.put_turtle(b"<a> <b> <c> .", graphdb.graph_iri("C0123abcd"))
    req = seen[0]
    url = urllib.parse.urlsplit(req.full_url)
    assert req.get_method() == "PUT" and req.get_header("Content-type") == "text/turtle"
    assert url.netloc == "localhost:7200" and url.path == "/repositories/ddx-es/statements"
    assert urllib.parse.parse_qs(url.query)["context"] == ["<http://example.org/ddx/graph/provenance-C0123abcd>"]


def test_unreachable_graphdb_is_reported(monkeypatch):
    monkeypatch.setenv("DDXES_GRAPHDB_URL", "http://127.0.0.1:9")          # nothing listens on the discard port
    with pytest.raises(graphdb.GraphDBError, match="not reachable at http://127.0.0.1:9"):
        graphdb.put_turtle(b"", graphdb.graph_iri("x"), timeout=5)


def test_switched_off(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("DDXES_GRAPHDB", raising=False)
    assert graphdb.enabled()
    monkeypatch.setenv("DDXES_GRAPHDB", "off")
    assert not graphdb.enabled()
    monkeypatch.setenv("DDXES_GRAPHDB", "on")
    monkeypatch.setenv("VERCEL", "1")
    assert not graphdb.enabled()


def test_a_downloaded_run_goes_into_the_graph_of_its_case(tmp_path, monkeypatch):
    puts = []
    monkeypatch.setattr(graphdb, "put_turtle", lambda data, graph, **k: puts.append(graph))
    f = tmp_path / "consultation-provenance.ttl"
    f.write_text("@prefix : <http://example.org/ddx#> .\n"
                 ":Inf_C0123abcd_Triage :aboutCase :Patient_C0123abcd ; :inferredTriage :NonUrgent .\n", encoding="utf-8")
    out = graphdb.push_file(f)
    assert puts == ["http://example.org/ddx/graph/provenance-C0123abcd"] and out["case"] == ":Patient_C0123abcd"
    f.write_text("@prefix : <http://example.org/ddx#> .\n:a :b :c .\n", encoding="utf-8")
    with pytest.raises(graphdb.GraphDBError, match="describes 0 cases"):
        graphdb.push_file(f)
    f.write_text("not turtle at all", encoding="utf-8")
    with pytest.raises(graphdb.GraphDBError, match="not a Turtle file"):
        graphdb.push_file(f)


@needs_demo
def test_result_queries_are_stable_under_graphdb_reasoning():
    """The rows do not change when the graph also holds `?firing prov:used ?rule`, which GraphDB entails from
    `:firedRule rdfs:subPropertyOf prov:used`; the unexplained symptoms of an exclusion come sorted."""
    from rdflib.namespace import PROV

    from ddxes.provenance import result_to_graph
    from ddxes.web import views as V
    svc = V.Services.get()
    result = svc.case_result(DEMO)
    plain = V.result_queries(svc, result)
    entailed = svc.kb.graph + result_to_graph(result, svc.kb)
    for f, _p, r in list(entailed.triples((None, DDX.firedRule, None))):
        entailed.add((f, PROV.used, r))
    case = f":Patient_{DEMO}"
    for q in plain["queries"]:
        rows = [[("" if x is None else V._fmt_cell(x, str(DDX))) for x in row] for row in entailed.query(q["query"])]
        assert rows == q["rows"], q["key"]
        assert case in q["query"]
    exclusions = next(q for q in plain["queries"] if q["key"] == "exclusions")
    assert exclusions["count"] == 48
    for _pathology, symptoms in exclusions["rows"]:
        assert symptoms.split("; ") == sorted(symptoms.split("; "))
    assert plain["graph"] == f"http://example.org/ddx/graph/provenance-{DEMO}"


@needs_demo
def test_result_rows_do_not_depend_on_the_hash_seed():
    """Python draws a new hash seed for every process, and rdflib walks its indexes in hash order. With a plain
    GROUP_CONCAT the symptom lists of T002456 came out in another order under each of four seeds, so the page could
    change after a restart; the ordered subquery gives the same rows under every seed."""
    runs = []
    for seed in ("1", "3"):
        done = subprocess.run([sys.executable, "-c", ROWS, str(SRC), DEMO], capture_output=True, text=True, check=True,
                              env={**os.environ, "PYTHONHASHSEED": seed})
        runs.append(json.loads(done.stdout.strip().splitlines()[-1]))
    assert runs[0] == runs[1]
    assert len(runs[0][3]) == 48                       # the exclusions


def test_graph_names():
    assert graphdb.graph_iri("T002456") == "http://example.org/ddx/graph/provenance-T002456"
    url = graphdb.workbench_sparql_url("SELECT * WHERE { ?s ?p ?o }")
    assert url.startswith("http://localhost:7200/sparql?repositoryId=ddx-es&query=SELECT")
