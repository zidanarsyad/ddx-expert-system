"""File locations and RDF namespaces shared by the package."""
import pathlib

from rdflib import Namespace

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
PROCESSED = DATA / "processed"
RAW = DATA / "raw"
KG = ROOT / "kg"
KNOWLEDGE = ROOT / "knowledge"
RESULTS = ROOT / "results"
QUERIES = ROOT / "queries"

# Turtle files that make up the knowledge graph, in load order
KG_FILES = ["ontology.ttl", "kb.ttl", "rules.ttl", "stats.ttl", "inferred.ttl"]

DDX = Namespace("http://example.org/ddx#")
PROV = Namespace("http://www.w3.org/ns/prov#")
DCT = Namespace("http://purl.org/dc/terms/")
BIBO = Namespace("http://purl.org/ontology/bibo/")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")


def local(iri) -> str:
    """'http://example.org/ddx#E_91' -> 'E_91'"""
    s = str(iri)
    return s.rsplit("#", 1)[-1] if "#" in s else s.rsplit("/", 1)[-1]
