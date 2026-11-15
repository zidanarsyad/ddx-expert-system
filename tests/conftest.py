import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ddxes.engine import InferenceEngine  # noqa: E402
from ddxes.kb import KnowledgeBase  # noqa: E402


@pytest.fixture(scope="session")
def kb():
    return KnowledgeBase.load()


@pytest.fixture(scope="session")
def engine(kb):
    return InferenceEngine(kb)


@pytest.fixture(scope="session")
def sample():
    return json.loads((ROOT / "data" / "processed" / "sample_patients.json").read_text(encoding="utf-8"))
