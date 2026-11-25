"""What each Turtle file of the knowledge graph holds: classes, blank-node classes and predicates, counted.

The report and the deck read these counts when they are built, so their description of the files cannot drift from
the files themselves.
"""
from __future__ import annotations

from collections import Counter

from rdflib import BNode, Graph
from rdflib.namespace import RDF

from .paths import KG

FILES = ("ontology", "kb", "rules", "stats", "cases", "inferred")


def _local(iri) -> str:
    s = str(iri)
    return s.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def file_counts(kg_dir=KG) -> dict:
    """{file: {"triples": n, "types": Counter, "blank_types": Counter, "predicates": Counter}} for kg/<file>.ttl.

    "types" counts the named resources of each class and "blank_types" the blank nodes (for example the condition
    nodes of the rules); "predicates" counts the triples of each predicate.
    """
    out = {}
    for name in FILES:
        g = Graph().parse(kg_dir / f"{name}.ttl", format="turtle")
        named, blank = Counter(), Counter()
        for s, o in g.subject_objects(RDF.type):
            (blank if isinstance(s, BNode) else named)[_local(o)] += 1
        out[name] = {"triples": len(g), "types": named, "blank_types": blank,
                     "predicates": Counter(_local(p) for _, p, _ in g)}
    return out
