"""pytest tests/test_loop.py -q     agent loop with a scripted fake LLM (no network, no API key, no database)"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "agent"))
sys.path.insert(0, str(ROOT / "src" / "tools"))

import loop  # noqa: E402
from llm import LLMError, ToolCall, Turn, gemini_schema  # noqa: E402
from runner import Runner  # noqa: E402

STUBS = ROOT / "src" / "tools" / "stubs"
P = {"start": "2011-09-26", "end": "2011-11-20"}


def stub(name):
    s = json.loads((STUBS / f"{name}.json").read_text())
    s.pop("evidence_id", None)
    return s


class FakeClient:
    """Plays back scripted turns; records everything the loop sends."""
    model = "fake"

    def __init__(self, turns):
        self.turns, self.sent, self.tools = list(turns), [], None

    def start(self, system, tools):
        self.system, self.tools = system, tools
        return self

    def send(self, user_text=None, tool_results=None):
        self.sent.append({"user_text": user_text, "tool_results": tool_results})
        if not self.turns:
            raise LLMError("script exhausted")
        t = self.turns.pop(0)
        if isinstance(t, Exception):
            raise t
        return t


@pytest.fixture
def runner(monkeypatch):
    import runner as runner_mod
    for name in ("detect_anomaly", "drill_down"):
        monkeypatch.setitem(runner_mod.TOOL_FUNCS, name, lambda _args, n=name: stub(n))
    return Runner()


def call(name, args=None, cid="c1"):
    return Turn(tool_calls=[ToolCall(cid, name, args or {"metric": "net_revenue", "period": P})])


def report(text, ids=("E1",)):
    return Turn(tool_calls=[ToolCall("r", "submit_report", {
        "headline": text, "findings": [{"text": text, "evidence_ids": list(ids)}], "actions": [],
        "hypotheses": ["A competitor may have targeted these accounts"]})])


def good_report():
    a = stub("detect_anomaly")
    return report(f"Net revenue fell {abs(a['change_pct'])}% year over year")


def run(client, runner, **kw):
    return list(loop.run_investigation("Why did revenue drop?", client=client, runner=runner, **kw))


def test_happy_path_events_and_confidence(runner):
    fake = FakeClient([call("detect_anomaly"), call("drill_down", cid="c2"), good_report()])
    ev = run(fake, runner)
    assert [e["type"] for e in ev] == ["start", "tool_call", "tool_result", "tool_call", "tool_result",
                                       "validation", "report"]
    assert [e["evidence_id"] for e in ev if e["type"] == "tool_result"] == ["E1", "E2"]
    final = ev[-1]
    assert final["validated"] is True and final["tool_calls"] == 2
    assert {"score", "band", "components", "flags"} <= set(final["confidence"])
    assert json.dumps(ev, default=str)                       # the UI can serialise every event


def test_tools_offered_include_submit_report(runner):
    fake = FakeClient([good_report()])
    run(fake, runner)
    names = [t["name"] for t in fake.tools]
    assert "submit_report" in names and "detect_anomaly" in names
    assert "2011-12-04" in fake.system or "{last_full_day}" not in fake.system


def test_invented_number_gets_one_retry_then_passes(runner):
    fake = FakeClient([call("detect_anomaly"), report("Revenue fell $9.9M"), good_report()])
    ev = run(fake, runner)
    checks = [e for e in ev if e["type"] == "validation"]
    assert [c["ok"] for c in checks] == [False, True] and "$9.9M" in checks[0]["unmatched"]
    feedback = fake.sent[2]["tool_results"][0][1]
    assert feedback["accepted"] is False and "$9.9M" in feedback["feedback"]
    assert ev[-1]["type"] == "report" and ev[-1]["validated"] is True


def test_second_failure_returns_unvalidated_report(runner):
    fake = FakeClient([call("detect_anomaly"), report("Revenue fell $9.9M"), report("Revenue fell $8.8M")])
    ev = run(fake, runner)
    assert ev[-1]["type"] == "report" and ev[-1]["validated"] is False
    assert set(ev[-1]["validation"]["unmatched"]) == {"$8.8M"}      # headline + finding both carry it


def test_unknown_citation_is_rejected(runner):
    fake = FakeClient([call("detect_anomaly"), report("Revenue fell", ids=("E7",)), good_report()])
    ev = run(fake, runner)
    assert "unknown evidence id E7" in [e for e in ev if e["type"] == "validation"][0]["bad_citations"]


def test_report_missing_required_fields_is_rejected(runner):
    bad = Turn(tool_calls=[ToolCall("r", "submit_report", {"headline": "x"})])
    fake = FakeClient([bad, good_report()])
    first = [e for e in run(fake, runner) if e["type"] == "validation"][0]
    assert not first["ok"] and first["schema_errors"]


def test_tool_error_is_passed_back_not_raised(runner):
    fake = FakeClient([call("no_such_tool"), good_report()])
    ev = run(fake, runner)
    res = [e for e in ev if e["type"] == "tool_result"][0]
    assert res["evidence_id"] is None and res["result"]["error"]["code"] == "NO_DATA"
    assert fake.sent[1]["tool_results"][0][1]["error"]


def test_tool_call_limit(runner):
    fake = FakeClient([call("detect_anomaly"), call("detect_anomaly", cid="c2"), good_report()])
    ev = run(fake, runner, max_tool_calls=1)
    res = [e for e in ev if e["type"] == "tool_result"]
    assert res[0]["evidence_id"] == "E1" and "limit" in res[1]["result"]["error"]["message"]
    assert ev[-1]["tool_calls"] == 1


def test_text_only_reply_is_nudged_once_then_errors(runner):
    fake = FakeClient([Turn(text="Let me think."), Turn(text="Still thinking.")])
    ev = run(fake, runner)
    assert [e["type"] for e in ev] == ["start", "model_text", "model_text", "error"]
    assert "Continue" in fake.sent[1]["user_text"]


def test_llm_failure_becomes_error_event(runner):
    ev = run(FakeClient([LLMError("503 UNAVAILABLE")]), runner)
    assert ev[-1] == {"type": "error", "message": "503 UNAVAILABLE"}


def test_missing_provider_config_is_error_event(runner, monkeypatch):
    def broken():
        raise LLMError("GEMINI_API_KEY is not set")
    monkeypatch.setattr(loop, "make_client", broken)
    ev = list(loop.run_investigation("q", runner=runner))
    assert ev == [{"type": "error", "message": "GEMINI_API_KEY is not set"}]


def test_gemini_schema_drops_unsupported_keywords():
    s = gemini_schema(Runner().tool_schemas()[0]["input_schema"] | {
        "additionalProperties": False, "properties": {"c": {"oneOf": [{"type": "string"}]}}})
    text = json.dumps(s)
    assert "oneOf" not in text and "additionalProperties" not in text and "anyOf" in text
