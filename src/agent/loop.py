"""
loop.py - the investigator. run_investigation(question) is a generator of trace events for the UI.

    for event in run_investigation("Why did revenue drop between 2011-09-26 and 2011-11-20?"):
        ...   # event["type"] in: start, model_text, tool_call, tool_result, validation, report, error

Flow: the model gets the question + tool schemas (Runner.tool_schemas() + submit_report). Every tool call goes
through Runner.call() (evidence id, contract check, structured errors). On submit_report the report is checked
against the contract and by validator.check_report(); on a mismatch the model gets the problems and ONE retry.
The confidence score is then computed by code from the evidence (compute_confidence), never by the model.
Limits: MAX_TOOL_CALLS analytic calls, MAX_TURNS model turns.

Event shapes (all JSON-serialisable):
  start        {question, provider, model, prompt_version}
  model_text   {step, text}                              free text the model wrote alongside its calls
  tool_call    {step, name, args}
  tool_result  {step, name, evidence_id|None, result}    result is the tool payload or {"error": {...}}
  validation   {attempt, ok, unmatched, bad_citations, schema_errors}
  report       {report, confidence, validated, validation, evidence, tool_calls, model}   last event on success
  error        {message}                                  always the last event on failure

Run:  python src/agent/loop.py "Why did revenue drop between 2011-09-26 and 2011-11-20?"   (set WHYBOT_DB first)
"""
import argparse
import copy
import json
import sys
from pathlib import Path

from jsonschema import Draft7Validator

sys.path.insert(0, str(Path(__file__).resolve().parent))
from confidence import compute_confidence  # noqa: E402
from llm import LLMError, make_client  # noqa: E402
from runner import CONTRACT, Runner  # noqa: E402
from validator import check_report  # noqa: E402

PROMPT_VERSION = "system_v1"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / f"{PROMPT_VERSION}.md"
MAX_TOOL_CALLS = 12
MAX_TURNS = 20
REPORT_TOOL = {
    "name": "submit_report",
    "description": "Submit the final report. Call exactly once, when the investigation is finished. Every number "
                   "must come from a tool result and every finding must cite evidence ids (E1, E2, ...).",
    "input_schema": copy.deepcopy(CONTRACT["report"]),
}


def system_prompt():
    vals = {"data_start": "2009-12-01", "data_through": "2011-12-09", "last_full_day": "2011-12-04"}
    try:
        from common import bounds, d, get_con
        ds, dt, lf = bounds(get_con())
        vals = {"data_start": d(ds), "data_through": d(dt), "last_full_day": d(lf)}
    except Exception:  # noqa: BLE001  (no database: the documented defaults are still correct for this dataset)
        pass
    return PROMPT_PATH.read_text(encoding="utf-8").format(**vals)


def _feedback(v, schema_errors):
    lines = ["The report was REJECTED. Fix it and call submit_report again."]
    if schema_errors:
        lines.append("Format problems: " + "; ".join(schema_errors))
    if v["unmatched"]:
        lines.append("These numbers do not match any tool result (copy them from the tool outputs, or remove "
                     "them): " + ", ".join(v["unmatched"]))
    if v["bad_citations"]:
        lines.append("Citation problems: " + "; ".join(v["bad_citations"]))
    return " ".join(lines)


def run_investigation(question, client=None, runner=None, max_tool_calls=MAX_TOOL_CALLS):
    runner = runner or Runner()
    try:
        client = client or make_client()
        conv = client.start(system_prompt(), runner.tool_schemas() + [REPORT_TOOL])
    except (LLMError, ImportError) as e:
        yield {"type": "error", "message": str(e)}
        return
    yield {"type": "start", "question": question, "provider": type(client).__name__,
           "model": getattr(client, "model", None), "prompt_version": PROMPT_VERSION}

    report_validator = Draft7Validator(CONTRACT["report"])
    calls_made, attempts, nudged = 0, 0, False
    pending = {"user_text": question}
    for turn_no in range(1, MAX_TURNS + 1):
        try:
            turn = conv.send(**pending)
        except LLMError as e:
            yield {"type": "error", "message": str(e)}
            return
        if turn.text.strip():
            yield {"type": "model_text", "step": turn_no, "text": turn.text.strip()}
        if not turn.tool_calls:
            if nudged:
                yield {"type": "error", "message": "the model stopped without submitting a report"}
                return
            nudged = True
            pending = {"user_text": "Continue the investigation with the tools, then call submit_report."}
            continue

        results = []
        for call in turn.tool_calls:
            if call.name == REPORT_TOOL["name"]:
                attempts += 1
                report = call.args
                schema_errors = [e.message for e in report_validator.iter_errors(report)]
                v = check_report(report, runner.evidence)
                ok = v["ok"] and not schema_errors
                yield {"type": "validation", "attempt": attempts, "ok": ok, "unmatched": v["unmatched"],
                       "bad_citations": v["bad_citations"], "schema_errors": schema_errors}
                if ok or attempts >= 2:
                    yield {"type": "report", "report": report, "confidence": compute_confidence(runner.evidence),
                           "validated": ok, "validation": {**v, "schema_errors": schema_errors},
                           "evidence": runner.evidence, "tool_calls": calls_made,
                           "model": getattr(client, "model", None)}
                    return
                results.append((call, {"accepted": False, "feedback": _feedback(v, schema_errors)}))
                continue

            yield {"type": "tool_call", "step": turn_no, "name": call.name, "args": call.args}
            if calls_made >= max_tool_calls:
                payload, eid = {"error": {"code": "NO_DATA", "message": "tool-call limit reached; "
                                                                         "call submit_report now"}}, None
            else:
                calls_made += 1
                payload, eid = runner.call(call.name, call.args)
            yield {"type": "tool_result", "step": turn_no, "name": call.name, "evidence_id": eid, "result": payload}
            results.append((call, payload))
        pending = {"tool_results": results}
    yield {"type": "error", "message": f"no report after {MAX_TURNS} model turns"}


def _print_event(ev):
    t = ev["type"]
    if t == "start":
        print(f"[start] {ev['provider']} {ev['model']} ({ev['prompt_version']})\n  Q: {ev['question']}")
    elif t == "model_text":
        print(f"[model] {ev['text'][:300]}")
    elif t == "tool_call":
        print(f"[call]  {ev['name']} {json.dumps(ev['args'])[:200]}")
    elif t == "tool_result":
        r = ev["result"]
        print(f"[{ev['evidence_id'] or 'ERR'}]    " + (json.dumps(r["error"]) if "error" in r else "ok"))
    elif t == "validation":
        print(f"[check] attempt {ev['attempt']}: {'ok' if ev['ok'] else 'REJECTED'} "
              f"{ev['unmatched'] or ''} {ev['bad_citations'] or ''} {ev['schema_errors'] or ''}")
    elif t == "report":
        print(f"[report] model={ev['model']} validated={ev['validated']} tool_calls={ev['tool_calls']} "
              f"confidence={json.dumps(ev['confidence'])}")
        print(json.dumps(ev["report"], indent=2))
    elif t == "error":
        print(f"[error] {ev['message']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Run one WhyBot investigation")
    ap.add_argument("question")
    ap.add_argument("--save", help="write all events to this JSON file (for replay)")
    a = ap.parse_args()
    events = []
    for event in run_investigation(a.question):
        events.append(event)
        _print_event(event)
    if a.save:
        Path(a.save).write_text(json.dumps(events, indent=2, default=str), encoding="utf-8")
