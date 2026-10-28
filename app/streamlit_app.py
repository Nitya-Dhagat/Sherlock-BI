"""
WhyBot — app/streamlit_app.py

Run:  streamlit run app/streamlit_app.py

Three sources, selected in the sidebar:
  Fixtures     canned event stream from src/tools/stubs/demo_run.json. No backend,
               no API key, no network. Works today.
  Replay       a cached real run from cache/*.json. The demo safety net.
  Live agent   imports agent.loop.run_investigation and streams it.

The invariant that makes the safety net real: all three go through the SAME
render path. What you rehearse in Replay is pixel-identical to what Live shows.

Design constraints from the playbook:
  - The trace is the product: every tool call is a step with a plain-English
    title and a small result table. Judges watch the reasoning unfold.
  - Any number on screen must exist in the evidence store. The UI formats
    tool output; it never computes.
  - Projector: large type, high contrast, at most three clicks, no scrolling
    during the live demo.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

HERE = Path(__file__).resolve().parent


def _find_root(start: Path) -> Path:
    """Walk up until we find the repo root.

    The playbook's tree puts this file at src/app/, the repo has it at app/.
    Both work: the root is whichever ancestor actually holds src/tools/stubs
    (or failing that, a .git directory).
    """
    for cand in (start, *start.parents):
        if (cand / "src" / "tools" / "stubs").is_dir() or (cand / ".git").is_dir():
            return cand
    return start.parent


ROOT = _find_root(HERE)
for p in (str(HERE), str(ROOT), str(ROOT / "src")):     # src/ holds the agent package (live mode)
    if p not in sys.path:
        sys.path.insert(0, p)

import adapters as ad          # noqa: E402
import charts                  # noqa: E402

STUBS = ROOT / "src" / "tools" / "stubs"
CACHE = ROOT / "cache"

st.set_page_config(page_title="WhyBot", page_icon="🔍", layout="wide",
                   initial_sidebar_state="collapsed")

# --------------------------------------------------------------------------
# styling — projector first
# --------------------------------------------------------------------------
CSS = """
<style>
  .block-container {padding-top: 1.4rem; padding-bottom: 2rem; max-width: 1560px;}
  html, body, [class*="css"], .stMarkdown {font-size: 17px;}
  h1 {font-size: 2.1rem !important; margin-bottom: .1rem;}
  [data-testid="stHeader"] {background: transparent;}

  .wb-sub {color:#6B7280; font-size:1rem; margin-bottom:1rem;}

  .wb-strip {display:flex; gap:.5rem; flex-wrap:wrap; margin:.4rem 0 1rem 0;}
  .wb-pill {border:1px solid #E7E9EE; border-radius:999px; padding:.3rem .85rem;
            font-size:.92rem; color:#334155; background:#fff; white-space:nowrap;}
  .wb-pill b {color:#17181C;}
  .wb-pill-hi {background:#ECFDF5; border-color:#A7F3D0; color:#065F46;}
  .wb-pill-md {background:#FFFBEB; border-color:#FDE68A; color:#92400E;}
  .wb-pill-lo {background:#FEF2F2; border-color:#FECACA; color:#991B1B;}

  .wb-step {border-left:3px solid #E7E9EE; padding:.45rem 0 .55rem .85rem;
            margin-bottom:.35rem;}
  .wb-step-run {border-left-color:#1D4ED8; background:
                linear-gradient(90deg, rgba(29,78,216,.05), transparent 60%);}
  .wb-step-ok  {border-left-color:#0B7A4B;}
  .wb-step-err {border-left-color:#B45309; background:
                linear-gradient(90deg, rgba(180,83,9,.05), transparent 60%);}
  .wb-step-msg {border-left-color:#CBD5E1; font-style:italic; color:#475569;}
  .wb-title {font-weight:600; font-size:1.02rem; color:#17181C; line-height:1.35;}
  .wb-summary {color:#374151; font-size:.95rem; margin-top:.12rem; line-height:1.4;}
  .wb-meta {font-family:ui-monospace, SFMono-Regular, Menlo, monospace;
            font-size:.78rem; color:#94A3B8; margin-top:.25rem;}
  .wb-kv {margin-top:.35rem; font-size:.88rem; color:#334155;
          display:grid; grid-template-columns:minmax(6rem,10rem) 1fr;
          gap:.08rem .7rem; align-items:baseline;}
  .wb-kv span.k {color:#6B7280; overflow-wrap:anywhere;}
  .wb-kv span.v {font-weight:600; overflow-wrap:anywhere;}

  .wb-headline {font-size:1.5rem; font-weight:650; line-height:1.35;
                color:#17181C; margin:.2rem 0 .9rem 0;}
  .wb-finding {margin-bottom:.5rem; line-height:1.45;}
  .wb-eid {font-family:ui-monospace, Menlo, monospace; font-size:.74rem;
           background:#F1F5F9; color:#475569; border-radius:4px;
           padding:.08rem .34rem; margin-left:.3rem;}
  .wb-hyp {color:#6B7280;}
  .wb-caveat {color:#6B7280; font-size:.88rem; line-height:1.45;
              border-left:2px solid #E7E9EE; padding-left:.7rem; margin-top:.5rem;}
  .wb-sec {font-size:.78rem; letter-spacing:.09em; text-transform:uppercase;
           color:#94A3B8; font-weight:600; margin:1.1rem 0 .45rem 0;}
  div[data-testid="stMetricValue"] {font-size:2.3rem;}
  .stTabs [data-baseweb="tab"] {font-size:1rem; padding:.5rem .9rem;}
  .wb-loader {display:flex; align-items:center; gap:.7rem; padding:.7rem 1rem; margin:.2rem 0 1rem 0;
              border:1px solid #E7E9EE; border-radius:10px; background:#F8FAFC; color:#334155;}
  .wb-spin {width:18px; height:18px; border:3px solid #CBD5E1; border-top-color:#2563EB;
            border-radius:50%; animation:wb-rot .8s linear infinite;}
  @keyframes wb-rot {to {transform:rotate(360deg);}}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

def _fmt_score(v):
    """74.5 stays 74.5; 78.0 shows as 78."""
    return f"{v:.0f}" if abs(v - round(v)) < 0.05 else f"{v:.1f}"


STRENGTH_WORD = {"supporting": "Supported by the data", "weak": "Weak evidence",
                 "none": "Not distinguishable from normal variation"}

# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------
DEFAULTS = {"events": [], "evidence": {}, "norm": {}, "raw": {}, "report": None,
            "confidence": None, "run_meta": {}, "running": False, "source_label": "", "run_error": ""}
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v if not isinstance(v, (list, dict)) else type(v)())


def reset_run():
    st.session_state.events = []
    st.session_state.evidence = {}
    st.session_state.norm = {}
    st.session_state.raw = {}
    st.session_state.report = None
    st.session_state.confidence = None
    st.session_state.run_meta = {}
    st.session_state.run_error = ""


def absorb(ev: dict):
    """Build the evidence store. Every number the UI shows comes from here."""
    st.session_state.events.append(ev)
    t = ev.get("type")
    if t == "run_started":
        st.session_state.run_meta.update(
            run_id=ev.get("run_id"), question=ev.get("question"),
            prompt_version=ev.get("prompt_version"),
            data_through=ev.get("data_through"))
    elif t == "step_finished":
        tool = ev.get("tool")
        eid = ev.get("evidence_id")
        if ev.get("status") == "ok":
            result = ev.get("result") or {}
            if eid:
                st.session_state.evidence[eid] = {"tool": tool, "result": result}
            st.session_state.raw[tool] = result
            n = ad.normalise(tool, result)
            if n is not None:
                st.session_state.norm[tool] = n
    elif t == "report":
        st.session_state.report = ad.norm_report(ev.get("report") or {})
        st.session_state.confidence = ad.norm_confidence(ev.get("confidence") or {})
    elif t == "run_finished":
        st.session_state.run_meta.update(
            status=ev.get("status"), steps=ev.get("steps"),
            duration_ms=ev.get("duration_ms"))


# --------------------------------------------------------------------------
# event sources
# --------------------------------------------------------------------------
# The three fixture runs. The two backups exist because the playbook wants the
# awkward cases demoed: an agent that says "no drop" or "cannot be determined"
# earns more trust than one that always finds a story.
FIXTURE_RUNS = {
    "Main scenario": "demo_run.json",
    "Backup: nothing actually dropped": "demo_run_no_anomaly.json",
    "Backup: data cannot answer it": "demo_run_unanswerable.json",
}
FIXTURE_QUESTIONS = {
    "demo_run.json": "Why did revenue drop in the last two months of 2011?",
    "demo_run_no_anomaly.json": "Why did revenue drop in May and June 2011?",
    "demo_run_unanswerable.json": "Did a competitor cause our revenue to drop?",
}


def events_from_fixtures(filename="demo_run.json"):
    path = STUBS / filename
    if not path.exists():
        st.error(f"No fixture run at {path}\n\n"
                 "Run:  python src/tools/stubs/build_demo_run.py")
        return []
    return json.loads(path.read_text())


def events_from_cache(name):
    return json.loads((CACHE / name).read_text())


def events_live(question):
    from agent.loop import run_investigation   # lazy: UI must work without it
    from agent.ui_events import to_ui_events    # loop events -> this app's event names
    yield from to_ui_events(run_investigation(question))


def save_to_cache(events, question):
    """Every live run becomes a replayable artefact. This is the demo safety net:
    if the API or the venue Wi-Fi dies, the last good run still plays."""
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        rid = st.session_state.run_meta.get("run_id") or \
            datetime.now().strftime("r_%Y%m%d_%H%M%S")
        path = CACHE / f"{rid}.json"
        path.write_text(json.dumps(events, indent=1, default=str))
        return path.name
    except Exception:
        return None


# --------------------------------------------------------------------------
# trace rendering — the product
# --------------------------------------------------------------------------
def render_step(ev, box):
    t = ev.get("type")
    if t == "step_started":
        box.markdown(
            f"<div class='wb-step wb-step-run'>"
            f"<div class='wb-title'>◴ {ev.get('title') or ev.get('tool')}</div>"
            f"<div class='wb-meta'>{ev.get('tool')} · running</div></div>",
            unsafe_allow_html=True)
        return

    if t == "agent_message":
        # collapsed like a chat 'thinking' block: the trace stays scannable, reasoning is one click away
        text = ev.get("text", "")
        preview = text if len(text) <= 70 else text[:70].rsplit(" ", 1)[0] + "…"
        with box.expander(f"💭 Thinking — {preview}", expanded=False):
            st.markdown(text)
        return

    if t != "step_finished":
        return

    ok = ev.get("status") == "ok"
    tool = ev.get("tool")
    title = ev.get("title") or tool
    eid = ev.get("evidence_id") or "—"
    dur = ev.get("duration_ms") or 0

    if ok:
        n = ad.normalise(tool, ev.get("result") or {})
        rows = ad.step_rows(tool, n)
        kv = "".join(f"<span class='k'>{k}</span><span class='v'>{v}</span>"
                     for k, v in rows)
        box.markdown(
            f"<div class='wb-step wb-step-ok'>"
            f"<div class='wb-title'>✓ {title}</div>"
            f"<div class='wb-summary'>{ev.get('summary','')}</div>"
            f"<div class='wb-kv'>{kv}</div>"
            f"<div class='wb-meta'>{eid} · {tool} · {dur} ms</div></div>",
            unsafe_allow_html=True)
    else:
        err = ev.get("error") or {}
        box.markdown(
            f"<div class='wb-step wb-step-err'>"
            f"<div class='wb-title'>⚠ {title}</div>"
            f"<div class='wb-summary'><b>{err.get('code','ERROR')}</b> — "
            f"{err.get('message','')}</div>"
            f"<div class='wb-meta'>{tool} · {dur} ms · the agent adapts and "
            f"continues</div></div>",
            unsafe_allow_html=True)


# --------------------------------------------------------------------------
# chart selection
# --------------------------------------------------------------------------
CHART_FOR_TOOL = {
    "detect_anomaly": ("Is it real?", lambda n: charts.timeseries_expected(n["detect_anomaly"])),
    "drill_down": ("Where?", None),       # needs a dimension picker, handled separately
    "driver_decomposition": ("What moved it", lambda n: charts.revenue_bridge(
        n["driver_decomposition"], n.get("detect_anomaly"))),
    "customer_movement": ("Who?", lambda n: charts.customer_movement_bridge(
        n["customer_movement"])),
    "check_external_factors": ("What else?", lambda n: charts.external_factors_bars(
        n["check_external_factors"])),
    "forecast_scenario": ("What next?", lambda n: charts.forecast_chart(
        n["forecast_scenario"])),
}


def live_chart_for(tool, norm):
    """The one chart to show full-size as this step completes."""
    if tool == "drill_down" and "drill_down" in norm:
        dims = norm["drill_down"]["dimensions"]
        if dims:
            return charts.contributor_bars(norm["drill_down"], dims[0])
        return None
    entry = CHART_FOR_TOOL.get(tool)
    if entry and entry[1] and tool in norm:
        try:
            return entry[1](norm)
        except Exception:
            return None
    return None


def render_evidence_tabs():
    norm = st.session_state.norm
    if not norm:
        st.info("Charts appear as the investigation produces evidence.")
        return

    specs = []
    if "detect_anomaly" in norm:
        specs.append(("Is it real?", lambda: charts.timeseries_expected(norm["detect_anomaly"])))
    if "driver_decomposition" in norm:
        specs.append(("What moved it", lambda: charts.revenue_bridge(
            norm["driver_decomposition"], norm.get("detect_anomaly"))))
    if "drill_down" in norm and norm["drill_down"]["dimensions"]:
        specs.append(("Where?", None))
    if "driver_decomposition" in norm:
        specs.append(("How?", lambda: charts.driver_bars(norm["driver_decomposition"])))
    if "customer_movement" in norm:
        specs.append(("Who?", lambda: charts.customer_movement_bridge(
            norm["customer_movement"])))
    if "check_external_factors" in norm:
        specs.append(("What else?", lambda: charts.external_factors_bars(
            norm["check_external_factors"])))
    if "forecast_scenario" in norm:
        specs.append(("What next?", lambda: charts.forecast_chart(norm["forecast_scenario"])))

    tabs = st.tabs([s[0] for s in specs])
    for tab, (label, build) in zip(tabs, specs):
        with tab:
            if label == "Where?":
                d = norm["drill_down"]
                dim = st.radio("dimension", d["dimensions"], horizontal=True,
                               key="drill_dim", label_visibility="collapsed")
                st.plotly_chart(charts.contributor_bars(d, dim),
                                use_container_width=True, config={"displayModeBar": False})
            else:
                st.plotly_chart(build(), use_container_width=True,
                                config={"displayModeBar": False})
            if label == "What next?" and norm["forecast_scenario"].get("caveat"):
                st.markdown(f"<div class='wb-caveat'>"
                            f"{norm['forecast_scenario']['caveat']}</div>",
                            unsafe_allow_html=True)
            if label == "What else?":
                nd = norm["check_external_factors"]["no_data"]
                if nd:
                    items = " · ".join(f"<b>{x['series']}</b> ({x['reason']})"
                                       for x in nd)
                    st.markdown(f"<div class='wb-caveat'>Could not be checked: "
                                f"{items}</div>", unsafe_allow_html=True)


# --------------------------------------------------------------------------
# report card
# --------------------------------------------------------------------------
def render_report():
    rep = st.session_state.report
    conf = st.session_state.confidence
    if not rep:
        return

    left, right = st.columns([3, 2], gap="large")

    with left:
        st.markdown(f"<div class='wb-headline'>{rep['headline']}</div>",
                    unsafe_allow_html=True)

        if rep["actions"]:
            st.markdown("<div class='wb-sec'>Recommended action</div>",
                        unsafe_allow_html=True)
            for a in rep["actions"]:
                st.markdown(f"<div class='wb-finding'>• {a}</div>",
                            unsafe_allow_html=True)

        if rep["findings"]:
            st.markdown("<div class='wb-sec'>Findings</div>", unsafe_allow_html=True)
            for f in rep["findings"]:
                eids = "".join(f"<span class='wb-eid'>{e}</span>"
                               for e in f["evidence_ids"])
                st.markdown(f"<div class='wb-finding'>• {f['text']}{eids}</div>",
                            unsafe_allow_html=True)

        if rep["hypotheses"]:
            st.markdown("<div class='wb-sec'>Hypotheses — not verified, "
                        "not scored</div>", unsafe_allow_html=True)
            for h in rep["hypotheses"]:
                why = f" <span class='wb-hyp'>({h['why']})</span>" if h["why"] else ""
                st.markdown(f"<div class='wb-finding wb-hyp'>• {h['text']}{why}</div>",
                            unsafe_allow_html=True)

    with right:
        if conf and conf["score"] is not None:
            # not st.metric: its delta arrow implies a trend that does not exist
            cls = {"high": "wb-pill-hi", "medium": "wb-pill-md",
                   "low": "wb-pill-lo"}.get(str(conf["band"]).lower(), "")
            st.markdown(
                f"<div class='wb-sec'>Confidence</div>"
                f"<div style='display:flex; align-items:baseline; gap:.7rem;'>"
                f"<span style='font-size:2.9rem; font-weight:680; line-height:1;'>"
                f"{_fmt_score(conf['score'])}</span>"
                f"<span style='color:#6B7280;'>/ 100</span>"
                f"<span class='wb-pill {cls}'>{conf['band']}</span></div>",
                unsafe_allow_html=True)
            if conf["components"]:
                st.plotly_chart(charts.confidence_components(conf),
                                use_container_width=True,
                                config={"displayModeBar": False})
            for flag in conf["flags"]:
                st.warning(ad.pretty_label(flag))

        sig = st.session_state.norm.get("significance_test")
        if sig:
            word = STRENGTH_WORD.get(str(sig["strength"]).lower(), sig["strength"])
            # the contract reports the effect as a fraction, so render the
            # interval as percentages unless it is genuinely currency
            if sig["effect_is_fraction"]:
                iv = f"[{ad.pct(sig['ci_low'])}, {ad.pct(sig['ci_high'])}]"
            else:
                iv = f"[{ad.money(sig['ci_low'])}, {ad.money(sig['ci_high'])}]"
            st.markdown(
                f"<div class='wb-sec'>Statistical check</div>"
                f"<div class='wb-finding'><b>{word}</b><br>"
                f"{int(sig['ci_level']*100)}% interval {iv}"
                + (f" · p = {sig['p_value']:.4f}" if sig['p_value'] is not None else "")
                + "</div>", unsafe_allow_html=True)
            if sig["caveat"]:
                st.markdown(f"<div class='wb-caveat'>{sig['caveat']}</div>",
                            unsafe_allow_html=True)


def render_answer_banner():
    """The headline answer, directly under the question.

    Playbook: no scrolling during the live demo. Without this, the answer sits
    below the whole trace and the presenter has to scroll to it on stage.
    """
    rep = st.session_state.report
    conf = st.session_state.confidence
    if not rep or not rep["headline"]:
        return
    cls = ""
    badge = ""
    # the accent colour tracks the confidence band: a green bar beside a Low-
    # confidence answer would overstate it
    accent = "#94A3B8"
    if conf and conf["score"] is not None:
        band = str(conf["band"]).lower()
        cls = {"high": "wb-pill-hi", "medium": "wb-pill-md",
               "low": "wb-pill-lo"}.get(band, "wb-pill")
        accent = {"high": "#0B7A4B", "medium": "#B45309",
                  "low": "#94A3B8"}.get(band, "#94A3B8")
        badge = (f"<span class='wb-pill {cls}' style='font-size:1rem;'>"
                 f"Confidence <b>{_fmt_score(conf['score'])}</b> · "
                 f"{conf['band']}</span>")
    st.markdown(
        f"<div style='border:1px solid #E7E9EE; border-left:4px solid {accent};"
        f" border-radius:10px; padding:1rem 1.2rem; background:#FCFDFE;"
        f" margin:.2rem 0 1.1rem 0; display:flex; gap:1.2rem;"
        f" align-items:flex-start; justify-content:space-between;'>"
        f"<div class='wb-headline' style='margin:0;'>{rep['headline']}</div>"
        f"<div style='flex:0 0 auto;'>{badge}</div></div>",
        unsafe_allow_html=True)
    if rep["actions"]:                       # second thing the reader sees: what to do about it
        st.markdown("<div class='wb-sec'>Recommended action</div>"
                    + "".join(f"<div class='wb-finding'>• {a}</div>" for a in rep["actions"]),
                    unsafe_allow_html=True)


def render_status_strip():
    m = st.session_state.run_meta
    conf = st.session_state.confidence
    pills = []
    if st.session_state.source_label:
        pills.append(f"<span class='wb-pill'>{st.session_state.source_label}</span>")
    if m.get("steps") is not None:
        pills.append(f"<span class='wb-pill'><b>{m['steps']}</b> tool calls</span>")
    if m.get("duration_ms"):
        pills.append(f"<span class='wb-pill'><b>{m['duration_ms']/1000:.1f}s</b></span>")
    if st.session_state.evidence:
        pills.append(f"<span class='wb-pill'><b>{len(st.session_state.evidence)}</b>"
                     f" evidence records</span>")
    if m.get("data_through"):
        pills.append(f"<span class='wb-pill'>data through "
                     f"<b>{m['data_through']}</b></span>")
    if m.get("status") and m["status"] != "ok":
        pills.append(f"<span class='wb-pill wb-pill-lo'>run {m['status']}</span>")
    if pills:
        st.markdown(f"<div class='wb-strip'>{''.join(pills)}</div>",
                    unsafe_allow_html=True)


# --------------------------------------------------------------------------
# sidebar
# --------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Demo controls")
    cached = sorted((p.name for p in CACHE.glob("*.json")), reverse=True) \
        if CACHE.exists() else []
    mode = st.radio("Source", ["Fixtures", "Replay cached", "Live agent"], index=0,
                    help="Fixtures and Replay need no network and no API key.")
    cache_pick = None
    fixture_pick = "demo_run.json"
    if mode == "Fixtures":
        label = st.selectbox("Scenario", list(FIXTURE_RUNS.keys()))
        fixture_pick = FIXTURE_RUNS[label]
    if mode == "Replay cached":
        cache_pick = st.selectbox("Cached run", cached) if cached else None
        if not cached:
            st.caption("No cached runs yet. A live run is saved here automatically.")
    pace = st.slider("Step pacing (seconds)", 0.0, 1.5, 0.5, 0.05,
                     help="Slow the replay so the trace is readable on a projector.")
    st.divider()
    show_raw = st.checkbox("Show raw tool JSON", value=False,
                           help="For the 'can the LLM make up numbers?' question.")
    st.caption("Fixtures → works with no backend. Replay → the safety net. "
               "Live → the real agent.")

# --------------------------------------------------------------------------
# header
# --------------------------------------------------------------------------
st.markdown("# WhyBot")
st.markdown("<div class='wb-sub'>Ask why. Watch the investigation, not just the "
            "answer.</div>", unsafe_allow_html=True)

qcol, bcol = st.columns([5, 1], gap="small")
default_q = (FIXTURE_QUESTIONS.get(fixture_pick) if mode == "Fixtures"
             else "Why did revenue drop in the last two months of 2011?")
question = qcol.text_input(
    "Question", value=default_q, label_visibility="collapsed",
    placeholder="Why did revenue drop?", key=f"q_{fixture_pick}_{mode}")
run = bcol.button("Investigate", type="primary", use_container_width=True)
if run:
    reset_run()               # flush the old answer, banner and trace BEFORE anything below is drawn
    st.session_state.source_label = mode
loader = st.empty()

render_status_strip()
render_answer_banner()

trace_col, eve_col = st.columns([2, 3], gap="large")
trace_col.markdown("<div class='wb-sec'>Investigation trace</div>",
                   unsafe_allow_html=True)
trace_box = trace_col.container()
eve_col.markdown("<div class='wb-sec'>Evidence</div>", unsafe_allow_html=True)
eve_slot = eve_col.empty()

# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------
if st.session_state.run_error and not run:
    st.error(st.session_state.run_error)

if run:
    if mode == "Fixtures":
        source = iter(events_from_fixtures(fixture_pick))
    elif mode == "Replay cached":
        source = iter(events_from_cache(cache_pick)) if cache_pick else iter([])
    else:
        source = events_live(question)      # a generator: import errors surface in the loop below

    loader.markdown("<div class='wb-loader'><span class='wb-spin'></span>"
                    "<span>WhyBot is investigating… the steps appear below as they finish"
                    + (" (about 25 s)" if mode == "Live agent" else "") + "</span></div>",
                    unsafe_allow_html=True)
    collected = []
    try:
        for ev in source:
            collected.append(ev)
            absorb(ev)
            render_step(ev, trace_box)
            if ev.get("type") == "step_finished" and ev.get("status") == "ok":
                fig = live_chart_for(ev.get("tool"), st.session_state.norm)
                if fig is not None:
                    eve_slot.plotly_chart(fig, use_container_width=True,
                                          config={"displayModeBar": False})
            if mode != "Live agent" and pace:
                time.sleep(pace)
    except Exception as e:
        # kept in session state: st.rerun() below would otherwise wipe an st.error immediately
        st.session_state.run_error = (f"The run stopped: {type(e).__name__}: {e}. "
                                      "Switch the sidebar to Fixtures or Replay cached.")
    loader.empty()
    if not st.session_state.run_error:
        fin = next((e for e in collected if e.get("type") == "run_finished"), {})
        if fin.get("status") == "error":
            st.session_state.run_error = f"The agent stopped: {fin.get('message', 'unknown error')}"

    if mode == "Live agent" and collected:
        saved = save_to_cache(collected, question)
        if saved:
            st.toast(f"Run cached as {saved} — replayable with no network.")
    st.rerun()

# --------------------------------------------------------------------------
# steady-state render (after a run, or on any widget interaction)
# --------------------------------------------------------------------------
if st.session_state.events:
    # Skip step_started in the steady-state render: during the run it is the
    # "working…" placeholder, but once the step has finished, replaying it would
    # show every step twice.
    for ev in st.session_state.events:
        if ev.get("type") == "step_started":
            continue
        render_step(ev, trace_box)
    with eve_col:
        eve_slot.empty()
        render_evidence_tabs()

    if st.session_state.report:
        st.divider()
        st.markdown("<div class='wb-sec'>Full report</div>", unsafe_allow_html=True)
        render_report()

    if show_raw and st.session_state.evidence:
        st.divider()
        st.markdown("<div class='wb-sec'>Evidence store — every number on this "
                    "page came from here</div>", unsafe_allow_html=True)
        for eid, rec in st.session_state.evidence.items():
            with st.expander(f"{eid} · {rec['tool']}"):
                st.json(rec["result"])
else:
    trace_box.info("Press **Investigate** to begin.")
    with eve_col:
        eve_slot.info("Charts appear as the investigation produces evidence.")