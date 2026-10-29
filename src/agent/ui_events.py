"""
ui_events.py - translate run_investigation() events into the Streamlit app's event stream (app/streamlit_app.py).

  loop event     ->  UI event
  start          ->  run_started    {run_id, question, prompt_version, data_through, ts}
  model_text     ->  agent_message  {step, text}
  tool_call      ->  step_started   {step, tool, title, input}
  tool_result    ->  step_finished  {step, tool, title, status ok|error, evidence_id, result|error, summary, duration_ms}
  validation     ->  agent_message  (only when a report was rejected and retried)
  report         ->  report         {report, confidence, validated, validation}  then run_finished {status ok}
  error          ->  agent_message + run_finished {status error, message}

Streaming: wraps a generator, so the UI draws each step as it happens. Summaries quote only tool numbers.
    for ui_ev in to_ui_events(run_investigation(question)): ...
Convert a saved run:  python src/agent/ui_events.py saved_loop_run.json cache/<run_id>.json
"""
import json
import sys
import time
from datetime import datetime

TITLES = {
    "detect_anomaly": "Checking whether the change is real",
    "drill_down": "Finding where the change happened",
    "driver_decomposition": "Splitting the change into customers, frequency and basket size",
    "customer_movement": "Finding which customers were retained, lost or new",
    "significance_test": "Testing whether the segment's change is statistically solid",
    "check_external_factors": "Checking outside explanations",
    "forecast_scenario": "Projecting the next weeks",
}


def _money(v):
    return f"£{abs(v):,.0f}"


def summarise(tool, r):
    """One plain-English line from a tool result (numbers copied from the result)."""
    try:
        if tool == "detect_anomaly":
            return (f"{r['metric']} is {abs(r['change_pct'])}% {'below' if r['change_pct'] < 0 else 'above'} "
                    f"expected ({r['method']}, z = {r['z_score']}); severity {r['severity']}.")
        if tool == "drill_down":
            dim, rows = next(((k, v) for k, v in r["contributors"].items() if k != "tenure"),
                             next(iter(r["contributors"].items())))
            top = next((x for x in rows if x["value"] != "(other)"), rows[0])   # rows are sorted by the net's sign
            sign = "-" if r["total_change"] < 0 else "+"
            share = (f" ({top['share_of_gross_decline']:.0%} of the gross decline)"
                     if top["change"] < 0 and top["share_of_gross_decline"] else "")
            return (f"Net change {sign}{_money(r['total_change'])}; biggest {dim}: {top['value']} "
                    f"{'-' if top['change'] < 0 else '+'}{_money(top['change'])}{share}.")
        if tool == "driver_decomposition":
            name, eff = max(r["effects"].items(), key=lambda kv: abs(kv[1]["change"]))
            gross = sum(abs(v["change"]) for v in r["effects"].values())
            share = (f" ({eff['share']:.0%} of the change)" if abs(r["total_change"]) >= 0.1 * gross
                     else " (the effects largely offset each other)")
            return (f"Largest effect: {name.replace('_', ' ')} "
                    f"{'-' if eff['change'] < 0 else '+'}{_money(eff['change'])}{share}.")
        if tool == "customer_movement":
            return (f"{r['lost']['customers']} lost customers ({_money(r['lost']['revenue_change'])}), "
                    f"{r['new']['customers']} new ({_money(r['new']['revenue_change'])}).")
        if tool == "significance_test":
            seg = r["segment"]
            return (f"{seg['value']}: effect {r['effect_size']:.1%}, 95% interval {r['ci_low']:.1%} to "
                    f"{r['ci_high']:.1%}; {r['strength']}.")
    except (KeyError, TypeError, ValueError, StopIteration):
        pass
    return ""


def to_ui_events(loop_events):
    run_id = datetime.now().strftime("r_%Y%m%d_%H%M%S")
    t0, started, titles, steps = time.time(), {}, {}, 0
    for ev in loop_events:
        t = ev["type"]
        if t == "start":
            yield {"type": "run_started", "run_id": run_id, "question": ev.get("question"), "ts": 0.0,
                   "prompt_version": ev.get("prompt_version"), "model": ev.get("model"),
                   "data_through": ev.get("data_through", "2011-12-09")}
        elif t == "model_text":
            yield {"type": "agent_message", "step": steps, "text": ev["text"]}
        elif t == "tool_call":
            steps += 1
            started[ev["name"]] = time.time()
            titles[ev["name"]] = TITLES.get(ev["name"], ev["name"])
            yield {"type": "step_started", "step": steps, "tool": ev["name"], "title": titles[ev["name"]],
                   "input": ev["args"]}
        elif t == "tool_result":
            name, res = ev["name"], ev["result"]
            out = {"type": "step_finished", "step": steps, "tool": name, "title": titles.get(name, name),
                   "evidence_id": ev.get("evidence_id"),
                   "duration_ms": int((time.time() - started.get(name, time.time())) * 1000)}
            if "error" in res:
                out.update(status="error", error=res["error"])
            else:
                out.update(status="ok", result=res, summary=summarise(name, res))
            yield out
        elif t == "validation" and not ev["ok"]:
            problems = ev["unmatched"] + ev["bad_citations"] + ev["schema_errors"]
            yield {"type": "agent_message", "step": steps,
                   "text": "Validator rejected the draft report (" + "; ".join(problems[:5]) + "). Retrying."}
        elif t == "report":
            yield {"type": "report", "report": ev["report"], "confidence": ev["confidence"],
                   "validated": ev["validated"], "validation": ev["validation"], "model": ev.get("model")}
            yield {"type": "run_finished", "run_id": run_id, "status": "ok", "steps": steps,
                   "duration_ms": int((time.time() - t0) * 1000)}
        elif t == "error":
            yield {"type": "agent_message", "step": steps, "text": f"Investigation stopped: {ev['message']}"}
            yield {"type": "run_finished", "run_id": run_id, "status": "error", "message": ev["message"],
                   "steps": steps, "duration_ms": int((time.time() - t0) * 1000)}


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    with open(src, encoding="utf-8") as f:
        events = list(to_ui_events(json.load(f)))
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(events, f, indent=1, default=str)
    print(f"wrote {len(events)} UI events to {dst}")
