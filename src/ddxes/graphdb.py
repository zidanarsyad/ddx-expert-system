"""Write a case's run into the GraphDB copy of the knowledge graph, so that GraphDB Desktop shows what the web shows.

The web interface and the engine read the Turtle files with rdflib and never need GraphDB. GraphDB holds a copy of
the graph (scripts/load_graphdb.py) for browsing, SPARQL and its chat agent. A run of the engine (a demo patient or a
consultation) exists there only after it is written in: this module puts the run's PROV-O graph into the named graph
<http://example.org/ddx/graph/provenance-<case id>> of the repository, replacing an earlier copy of the same case. The
SPARQL tab of the web interface then returns the same rows in GraphDB.

GraphDB is a local server: `DDXES_GRAPHDB_URL` (default http://localhost:7200) and `DDXES_GRAPHDB_REPO` (default ddx-es)
say where it is. On a hosted deployment such as Vercel there is no GraphDB, so writing is switched off there; the
Turtle file of a run can be loaded instead with `scripts/load_graphdb.py --add-file`.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GRAPHS = "http://example.org/ddx/graph/"
_LOCAL = {"localhost", "127.0.0.1", "::1"}


class GraphDBError(RuntimeError):
    pass


def settings() -> tuple[str, str]:
    return (os.environ.get("DDXES_GRAPHDB_URL", "http://localhost:7200").rstrip("/"),
            os.environ.get("DDXES_GRAPHDB_REPO", "ddx-es"))


def enabled() -> bool:
    """Writing to GraphDB is offered only where a local GraphDB can exist: not on Vercel, and not when switched off."""
    return not os.environ.get("VERCEL") and os.environ.get("DDXES_GRAPHDB", "on").lower() != "off"


def graph_iri(case_id: str) -> str:
    return f"{GRAPHS}provenance-{case_id.replace(' ', '_')}"


def workbench_sparql_url(query: str) -> str:
    """The GraphDB Workbench SPARQL editor with the query filled in (it opens in a new editor tab)."""
    base, repo = settings()
    return f"{base}/sparql?" + urllib.parse.urlencode({"repositoryId": repo, "query": query, "infer": "true"})


def _open(req: urllib.request.Request, timeout: float):
    """A GraphDB on this computer is reached directly, never through a proxy set for the web."""
    host = urllib.parse.urlsplit(req.full_url).hostname or ""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if host in _LOCAL else urllib.request.build_opener()
    return opener.open(req, timeout=timeout)


def put_turtle(data: bytes, graph: str, *, url: str | None = None, repo: str | None = None, timeout: float = 30) -> None:
    """Replace the named graph `graph` of the repository with the Turtle `data` (RDF4J REST: PUT .../statements?context=)."""
    base, rep = settings()
    base, rep = (url or base).rstrip("/"), repo or rep
    ctx = urllib.parse.quote(f"<{graph}>", safe="")
    req = urllib.request.Request(f"{base}/repositories/{rep}/statements?context={ctx}", data,
                                 {"Content-Type": "text/turtle"}, method="PUT")
    try:
        with _open(req, timeout):
            pass
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise GraphDBError(f"GraphDB at {base} refused the data for repository '{rep}' (HTTP {e.code}): {detail}") from e
    except (urllib.error.URLError, OSError) as e:
        raise GraphDBError(f"GraphDB is not reachable at {base}: {getattr(e, 'reason', e)}. Start GraphDB Desktop "
                           f"and load the repository with scripts/load_graphdb.py.") from e


def push_result(result, kb, *, include_case: bool = True, url: str | None = None, repo: str | None = None) -> dict:
    """Write one run into GraphDB. Demo patients are already in the graph `cases`, so their answers are left out
    (include_case=False); a consultation brings its own (include_case=True)."""
    from .provenance import result_to_graph
    g = result_to_graph(result, kb, include_case=include_case)
    graph = graph_iri(result.case.id)
    put_turtle(g.serialize(format="turtle").encode("utf-8"), graph, url=url, repo=repo)
    base, rep = settings()
    return {"graph": graph, "triples": len(g), "case": f":Patient_{result.case.id.replace(' ', '_')}",
            "url": (url or base).rstrip("/"), "repo": repo or rep}


def push_file(path: Path, *, url: str | None = None, repo: str | None = None) -> dict:
    """Write a run saved as Turtle (the download on the Provenance tab) into the named graph of its case."""
    from rdflib import Graph
    from .paths import DDX
    data = Path(path).read_bytes()
    try:
        g = Graph().parse(data=data, format="turtle")
    except Exception as e:  # noqa: BLE001 - any parse error means the same thing here
        raise GraphDBError(f"{path} is not a Turtle file: {e}") from e
    prefix = f"{DDX}Patient_"
    cases = sorted({str(o)[len(prefix):] for o in g.objects(None, DDX.aboutCase) if str(o).startswith(prefix)})
    if len(cases) != 1:
        raise GraphDBError(f"{path} describes {len(cases)} cases ({', '.join(cases) or 'none'}); a provenance file "
                           f"from DDX-ES describes exactly one.")
    graph = graph_iri(cases[0])
    put_turtle(data, graph, url=url, repo=repo)
    base, rep = settings()
    return {"graph": graph, "triples": len(g), "case": f":Patient_{cases[0]}", "url": (url or base).rstrip("/"),
            "repo": repo or rep}
