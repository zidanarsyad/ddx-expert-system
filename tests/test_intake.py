"""The optional free-text intake: the language model's reading is validated against the knowledge base and the
description, and the consultation never asks again what the description answered. No network: the model is replaced
by fixed answers."""
import pytest

from ddxes import intake, llm
from ddxes.consult import Consultation

TEXT = ("I'm a 39-year-old man. Since this morning I've been really short of breath. My right calf has been swollen "
        "for a few days, and I had knee surgery two weeks ago. No fever.")
READING = {"age": 39, "sex": "M", "chief_complaint": "E66", "findings": [
    {"code": "E66", "present": True, "quote": "really short of breath"},
    {"code": "E_151", "present": True, "quote": "right calf has been swollen", "where": [{"part": "calf", "side": "R"}]},
    {"code": "E196", "present": True, "quote": "knee surgery two weeks ago"},
    {"code": "E91", "present": False, "quote": "No fever"},
    {"code": "E45", "present": True, "quote": "coughing up blood"},       # not in the description
    {"code": "E999", "present": True, "quote": "short of breath"},        # not a code of the questionnaire
    {"code": "E55", "present": True, "quote": "right calf"},              # a follow-up question
]}


def value(kb, evidence, label):
    return next(v for v in kb.evidences[evidence].values if kb.label("value", v) == label)


def test_reading_is_checked_against_the_knowledge_base_and_the_text(kb):
    res = intake.parse(kb, TEXT, READING)
    got = {f.evidence: f.value for f in res.findings}
    assert got == {"E_66": True, "E_151": True, "E_152": {value(kb, "E_152", "calf(R)")}, "E_196": True, "E_91": False}
    assert (res.age, res.sex, res.chief_complaint) == (39, "M", "E_66")
    reasons = {d.what: d.reason for d in res.dropped}
    assert "not in the description" in reasons["E_45"]
    assert "not a question" in reasons["E999"]
    assert "follow-up" in reasons["E_55"]
    assert res.prefill() == [("E_151", True), ("E_152", got["E_152"]), ("E_196", True), ("E_91", False)]


def test_details_travel_and_contradictions(kb):
    text = "Nyeri dada kiri seperti ditusuk pisau, skala 7. Baru pulang dari Afrika Barat. Batuk, eh tidak batuk."
    data = {"findings": [
        {"code": "E53", "present": True, "quote": "Nyeri dada kiri seperti ditusuk pisau",
         "where": [{"part": "side of the chest", "side": "L"}], "character": ["knife stroke"], "intensity": 7},
        {"code": "E204", "present": True, "quote": "pulang dari Afrika Barat", "region": "West Africa"},
        {"code": "E201", "present": True, "quote": "Batuk"},
        {"code": "E201", "present": False, "quote": "tidak batuk"}]}
    res = intake.parse(kb, text, data)
    got = {f.evidence: f.value for f in res.findings}
    assert got["E_55"] == {value(kb, "E_55", "side of the chest(L)")}
    assert got["E_54"] == {value(kb, "E_54", "a knife stroke")}
    assert got["E_56"] == 7
    assert got["E_204"] == {value(kb, "E_204", "West Africa")}
    assert "E_201" not in got and any(d.what == "E_201" for d in res.dropped)   # said both ways
    assert res.chief_complaint == "E_53"                  # none given: the first present symptom of the text
    guessed = intake.parse(kb, "Gatal-gatal di lengan", {"findings": [
        {"code": "E129", "present": True, "quote": "Gatal-gatal di lengan", "itching": 6}]})
    assert "E_136" not in {f.evidence for f in guessed.findings}            # no number in the text: not taken
    lips = intake.vocabulary(kb).place("lower lips", "")
    assert lips == {value(kb, "E_152", "bottom lip(R)")}


def test_unstated_age_sex_and_code_spellings(kb):
    res = intake.parse(kb, "anything", {"age": "39.0", "sex": "female", "findings": []})
    assert (res.age, res.sex, res.chief_complaint, res.findings) == (39, "F", None, [])
    assert intake.parse(kb, "x", {"age": 300, "sex": "?"}).age is None
    assert intake._code("e_66") == intake._code("E66") == "E_66"


def test_prompt_lists_the_whole_questionnaire(kb):
    prompt = intake.messages(kb, "a description")[0]["content"]
    assert all(f"\nE{e.code[2:]} " in prompt for e in kb.evidences.values() if not e.parent)
    assert "BODY PARTS:" in prompt and "calf" in prompt and "a knife stroke" in prompt and "West Africa" in prompt


def test_read_returns_the_validated_reading(kb):
    def chat(messages, providers=None, max_tokens=0):
        assert messages[1]["content"].endswith(TEXT)
        return llm.Reply(READING, "stub", "stub-model", 0.0)
    res = intake.read(kb, TEXT, chat=chat)
    assert (res.provider, res.model, res.chief_complaint) == ("stub", "stub-model", "E_66")
    with pytest.raises(ValueError):
        intake.read(kb, "   ", chat=chat)


def test_prefilled_answers_are_never_asked_again(engine):
    pre = [("E_151", True), ("E_196", True), ("E_91", False)]
    base = Consultation(engine, age=39, sex="M", chief_complaint="E_66")
    cons = Consultation(engine, age=39, sex="M", chief_complaint="E_66", prefilled=pre)
    assert cons.asked == [] and all(cons.case.asked(e) for e, _ in pre)
    while (q := cons.next_question()) is not None:
        assert q.evidence not in {"E_151", "E_196", "E_91"}
        ev = engine.kb.evidences[q.evidence]
        cons.answer(q.evidence, False if ev.dtype == "B" else 0 if ev.is_scale else {ev.default})
    assert 0 < len(cons.asked) < len(base.asked) + 30
    pe = "PulmonaryEmbolism"
    assert Consultation(engine, age=39, sex="M", chief_complaint="E_66", prefilled=pre).result.hypothesis(pe).probability \
        > base.result.hypothesis(pe).probability


def test_env_file_and_provider_order(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# comment\nGROQ_API_KEY=g\nOPENROUTER_API_KEY='o'\nOPENROUTER_MODEL=m:free\n", encoding="utf-8")
    env = llm.read_env_file(f)
    assert env == {"GROQ_API_KEY": "g", "OPENROUTER_API_KEY": "o", "OPENROUTER_MODEL": "m:free"}
    pairs = [(p.name, m) for p, m, _ in llm.configured(env=env)]
    assert pairs[0][0] == "groq" and ("openrouter", "m:free") in pairs and all(n != "gemini" for n, _ in pairs)
    assert llm.configured(env={}) == [] and not llm.available(env={})
    with pytest.raises(llm.LLMError):
        llm.chat_json([], env={})


def test_model_answers_in_wrappers_are_parsed():
    assert llm.parse_json('<think>hm</think>```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.parse_json('Here it is: {"a": [1]} done') == {"a": [1]}
    with pytest.raises(ValueError):
        llm.parse_json("no json here")
