"""
WhyBot — src/tools/stubs/build_demo_run.py

Builds the event streams the Streamlit UI replays, READING the team's
contract-generated tool stubs in this folder. It never writes a per-tool stub,
so it cannot clobber anything `src/contract_init.py` produced.

    python src/tools/stubs/build_demo_run.py

Writes (only these three files):
    demo_run.json                the main investigation
    demo_run_no_anomaly.json     "why did revenue drop?" when nothing dropped
    demo_run_unanswerable.json   a question this dataset cannot answer

Why this replaces make_fixtures.py
----------------------------------
make_fixtures.py invented its own numbers AND wrote detect_anomaly.json,
drill_down.json and friends — the same filenames contract_init.py generates.
Running it now would overwrite the team's contract fixtures. Do not run it
again; use this instead.

The per-tool numbers here come from the contract stubs, so the UI demos the
agreed shapes. Only the step titles, the agent's one-line summaries and the
backup scenarios are written here — those are the agent's words, and until the
loop exists somebody has to supply them.
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_THROUGH = "2011-12-09"
PERIOD = {"start": "2011-09-26", "end": "2011-11-20"}


def load(name, required=True):
    p = HERE / f"{name}.json"
    if not p.exists():
        if required:
            raise SystemExit(
                f"missing {p}\n"
                "Run `python src/contract_init.py` first to generate the stubs.")
        return None
    return json.loads(p.read_text(encoding="utf-8"))


# (tool, plain-English title, the agent's one-line summary)
STEPS = [
    ("detect_anomaly", "Checking whether the drop is real",
     "Net revenue is 13.7% below the same period last year (z = −2.4), after "
     "adjusting for two fewer trading days."),
    ("drill_down", "Finding where it happened",
     "Wholesale-like customers explain 78% of the net change; the UK carries 71%."),
    ("driver_decomposition", "Finding what changed in customer behaviour",
     "69% of the fall is fewer active customers, not smaller orders."),
    ("customer_movement", "Separating retained, lost and new customers",
     "210 lapsed customers removed £98.0k, against £14.0k added by 150 new ones."),
    ("significance_test", "Testing whether the finding is solid",
     "Supporting: the segment is down 15.2%, and the 95% interval "
     "[−23.1%, −7.4%] excludes zero."),
    ("check_external_factors", "Checking the external explanations",
     "Two fewer trading days; prices up only 1.8%; no exchange-rate signal."),
    ("forecast_scenario", "Projecting the impact",
     "Recovering the segment is worth about £22k a week over the next four weeks."),
]

# the agent thinking aloud between steps, keyed by the step it follows
MESSAGES = {
    2: "The decline is concentrated in wholesale-like customers in the UK, so I "
       "will look at how that group's behaviour changed rather than splitting "
       "further.",
    4: "Fewer customers rather than smaller baskets. That is worth testing "
       "against normal variation before I call it a finding.",
}

# a realistic failure, so the trace shows the agent adapting rather than crashing
ERROR_AFTER = 2
ERROR_STEP = {
    "tool": "drill_down", "title": "Slicing by product category",
    "input": {"metric": "net_revenue", "period": PERIOD, "dimensions": ["category"]},
    "error": {"code": "EMPTY_SLICE",
              "message": "category is not an available dimension; "
                         "the classification has not been built yet"},
}
ERROR_FOLLOWUP = ("Category is not available, so I will not use it, and I will "
                  "say so rather than guess.")


def tool_input(tool):
    base = {"metric": "net_revenue", "period": PERIOD,
            "compare_to": "same_period_last_year"}
    if tool == "drill_down":
        return {**base, "dimensions": ["country", "customer_type"], "top_k": 5}
    if tool == "significance_test":
        return {**base, "segment": {"dimension": "customer_type",
                                    "value": "wholesale_like"}}
    if tool == "forecast_scenario":
        return {**base, "horizon_weeks": 4,
                "segment": {"dimension": "customer_type",
                            "value": "wholesale_like"}}
    return base


def build(run_id, question, steps, messages=None, error_after=None,
          report=None, confidence=None, status="ok"):
    evs = [{"type": "run_started", "run_id": run_id, "question": question,
            "ts": 0.0, "prompt_version": "stub-v1", "data_through": DATA_THROUGH}]
    step_no = 0
    total_ms = 0
    eid_no = 0
    for i, (tool, title, summary) in enumerate(steps, start=1):
        step_no += 1
        dur = 600 + 190 * i
        total_ms += dur
        evs.append({"type": "step_started", "step": step_no, "tool": tool,
                    "title": title, "input": tool_input(tool)})
        payload = dict(load(tool))
        eid_no += 1
        payload["evidence_id"] = f"E{eid_no}"       # renumber for this run
        evs.append({"type": "step_finished", "step": step_no, "tool": tool,
                    "status": "ok", "evidence_id": payload["evidence_id"],
                    "title": title, "summary": summary, "result": payload,
                    "duration_ms": dur})
        if messages and i in messages:
            evs.append({"type": "agent_message", "step": step_no,
                        "text": messages[i]})
        if error_after == i:
            step_no += 1
            total_ms += 240
            evs.append({"type": "step_started", "step": step_no,
                        "tool": ERROR_STEP["tool"], "title": ERROR_STEP["title"],
                        "input": ERROR_STEP["input"]})
            evs.append({"type": "step_finished", "step": step_no,
                        "tool": ERROR_STEP["tool"], "status": "error",
                        "evidence_id": None, "title": ERROR_STEP["title"],
                        "error": ERROR_STEP["error"], "duration_ms": 240})
            evs.append({"type": "agent_message", "step": step_no,
                        "text": ERROR_FOLLOWUP})
    if report is not None:
        evs.append({"type": "report", "report": report,
                    "confidence": confidence or {}})
    evs.append({"type": "run_finished", "run_id": run_id, "status": status,
                "steps": step_no, "duration_ms": total_ms})
    return evs


# --------------------------------------------------------------------------
# backup scenario 1 — nothing actually dropped
# --------------------------------------------------------------------------
def flat_anomaly():
    a = dict(load("detect_anomaly"))
    a.update(actual=884120.40, expected=871300.00, change_pct=1.5, z_score=0.3,
             severity="none", period={"start": "2011-05-02", "end": "2011-06-26"},
             trading_days={"actual": 40, "expected": 40})
    return a


NO_ANOMALY_REPORT = {
    "headline": "Revenue did not drop in this period. It was 1.5% above the same "
                "period last year, well inside normal variation.",
    "findings": [{"text": "Net revenue was £884.1k against £871.3k a year "
                          "earlier, up 1.5% (z = 0.3).", "evidence_ids": ["E1"]},
                 {"text": "Trading days were identical at 40, so no calendar "
                          "adjustment is needed.", "evidence_ids": ["E1"]}],
    "actions": [{"text": "No action needed. If you have a particular segment in "
                         "mind, ask again and I will test that segment directly.",
                 "evidence_ids": []}],
    "hypotheses": [],
}
NO_ANOMALY_CONF = {"score": 0.0, "band": "Low",
                   "components": {"coverage": 0.0, "significance": 0.0,
                                  "specificity": 0.0, "robustness": 0.0},
                   "flags": ["no_anomaly_detected"]}

# --------------------------------------------------------------------------
# backup scenario 2 — the data cannot answer it
# --------------------------------------------------------------------------
UNANSWERABLE_REPORT = {
    "headline": "This cannot be determined from the data available.",
    "findings": [
        {"text": "Net revenue did fall 13.7% year on year over this period.",
         "evidence_ids": ["E1"]},
        {"text": "Attributing it to a competitor needs competitor pricing, "
                 "win/loss records or market share. None are in this dataset, "
                 "which holds only this retailer's own transactions.",
         "evidence_ids": []}],
    "actions": [{"text": "Connect a competitor pricing feed or CRM win/loss data. "
                         "I can then test whether lapsed accounts cluster around "
                         "competitor activity.", "evidence_ids": []}],
    "hypotheses": ["A competitor may have undercut pricing on the affected lines "
                   "(cannot be verified from this data)."],
    "data_gaps": ["competitor pricing", "CRM win/loss", "market share"],
}
UNANSWERABLE_CONF = {"score": 22.0, "band": "Low",
                     "components": {"coverage": 0.0, "significance": 0.74,
                                    "specificity": 0.0, "robustness": 0.3},
                     "flags": ["question_not_answerable_from_available_data"]}


if __name__ == "__main__":
    report = load("report_example")
    confidence = load("compute_confidence")

    (HERE / "demo_run.json").write_text(json.dumps(build(
        "r_stub_main", "Why did revenue drop in the last two months of 2011?",
        STEPS, MESSAGES, ERROR_AFTER, report, confidence), indent=1))

    # swap in the flat anomaly for the "nothing dropped" run
    flat = flat_anomaly()
    orig = HERE / "detect_anomaly.json"
    backup = json.loads(orig.read_text(encoding="utf-8"))
    try:
        orig.write_text(json.dumps(flat, indent=2))
        (HERE / "demo_run_no_anomaly.json").write_text(json.dumps(build(
            "r_stub_no_anomaly", "Why did revenue drop in May and June 2011?",
            [("detect_anomaly", "Checking whether revenue actually dropped",
              "Revenue is 1.5% ABOVE the same period last year (z = 0.3); "
              "there is no drop to explain.")],
            report=NO_ANOMALY_REPORT, confidence=NO_ANOMALY_CONF), indent=1))
    finally:
        # always put the contract stub back exactly as it was
        orig.write_text(json.dumps(backup, indent=2))

    (HERE / "demo_run_unanswerable.json").write_text(json.dumps(build(
        "r_stub_unanswerable", "Did a competitor cause our revenue to drop?",
        STEPS[:2], report=UNANSWERABLE_REPORT,
        confidence=UNANSWERABLE_CONF), indent=1))

    print("wrote demo_run.json, demo_run_no_anomaly.json, "
          "demo_run_unanswerable.json")
    print("(per-tool contract stubs were not modified)")