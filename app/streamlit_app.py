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
  /* ---------------------------------------------------------------
     Design tokens. Every rule below refers to these, never raw hex,
     so the whole app can be retuned from one block.

     The app PINS its own light surface on the Streamlit container.
     Without this, a laptop in system dark mode gets Streamlit's dark
     background while this stylesheet paints near-black text on it, and
     the trace titles disappear. The demo must not depend on whose
     laptop it runs on. .streamlit/config.toml pins the theme too; this
     is the belt to that pair of braces.
  --------------------------------------------------------------- */
  :root {
    --bg:        #F6F7F9;
    --surface:   #FFFFFF;
    --surface-2: #FBFCFD;
    --line:      #E3E7EC;
    --line-soft: #EDF0F3;

    --ink:       #0F172A;   /* 15.8:1 on --bg  */
    --ink-2:     #3F4D60;   /*  8.9:1 on --bg  */
    --ink-3:     #667385;   /*  4.9:1 on --bg  — the lightest text we allow */

    --accent:    #1D4ED8;
    --good:      #0B7A4B;
    --good-bg:   #ECFDF5;
    --good-line: #A7F3D0;
    --warn:      #B45309;
    --warn-bg:   #FFFBEB;
    --warn-line: #FDE68A;
    --bad:       #C8102E;
    --bad-bg:    #FEF2F2;
    --bad-line:  #FECACA;

    --radius: 12px;
    --shadow: 0 1px 2px rgba(15,23,42,.04), 0 1px 8px rgba(15,23,42,.04);
    --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  }

  .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"],
  [data-testid="stHeader"], [data-testid="stSidebar"] > div,
  section.main, body, html {
    background: var(--bg) !important;
    color: var(--ink) !important;
  }
  /* headings are painted from Streamlit's own theme, which may be dark */
  h1, h2, h3, h4, h5, h6,
  .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {color: var(--ink) !important;}
  [data-testid="stHeader"] {border-bottom:1px solid var(--line-soft);}

  /* Demo chrome. Hide the Deploy button and the hamburger, but NOT the whole
     toolbar: the sidebar opener lives inside it, and hiding the toolbar took
     the demo controls away with it. */
  [data-testid="stAppDeployButton"], [data-testid="stDecoration"],
  #MainMenu, footer {display:none !important;}
  [data-testid="stToolbar"] {background:transparent !important;}
  [data-testid="stExpandSidebarButton"],
  [data-testid="stSidebarCollapsedControl"], [data-testid="collapsedControl"] {
      display:flex !important; visibility:visible !important; opacity:1 !important;
      width:auto !important; height:auto !important; z-index:999 !important;}
  [data-testid="stExpandSidebarButton"] button,
  [data-testid="stSidebarCollapsedControl"] button,
  [data-testid="collapsedControl"] button {
      background:var(--surface) !important; border:1px solid var(--line) !important;
      border-radius:8px !important; box-shadow:var(--shadow) !important;
      color:var(--ink-2) !important;}
  [data-testid="stExpandSidebarButton"] svg,
  [data-testid="stSidebarCollapsedControl"] svg,
  [data-testid="collapsedControl"] svg,
  [data-testid="stSidebarCollapseButton"] svg {
      fill:var(--ink-2) !important; color:var(--ink-2) !important;}

  .block-container {padding-top:2.9rem; padding-bottom:1.5rem; max-width:1580px;}
  html, body, [class*="css"], .stMarkdown, p, li, span, div {
    font-size:17px; color:var(--ink);
  }

  /* ---------------- masthead ---------------- */
  .wb-head {display:flex; align-items:center; gap:.85rem; margin:.1rem 0 1.15rem 0;}
  .wb-mark {width:38px; height:38px; border-radius:10px; flex:0 0 auto;
            background:linear-gradient(135deg, var(--accent), #4F46E5);
            display:flex; align-items:center; justify-content:center;
            color:#fff; font-size:1.15rem; font-weight:700;
            box-shadow:0 2px 8px rgba(29,78,216,.28);}
  .wb-name {font-size:1.85rem; font-weight:700; letter-spacing:-.02em;
            line-height:1.05; color:var(--ink);}
  .wb-sub {color:var(--ink-3); font-size:.98rem; line-height:1.2; margin-top:.12rem;}

  /* ---------------- status pills ---------------- */
  .wb-strip {display:flex; gap:.45rem; flex-wrap:wrap; margin:.2rem 0 1rem 0;}
  .wb-pill {border:1px solid var(--line); border-radius:999px;
            padding:.3rem .85rem; font-size:.89rem; color:var(--ink-2);
            background:var(--surface); white-space:nowrap;}
  .wb-pill b {color:var(--ink); font-weight:650;}
  .wb-pill-hi {background:var(--good-bg); border-color:var(--good-line); color:#065F46;}
  .wb-pill-hi b {color:#065F46;}
  .wb-pill-md {background:var(--warn-bg); border-color:var(--warn-line); color:#92400E;}
  .wb-pill-md b {color:#92400E;}
  .wb-pill-lo {background:var(--bad-bg); border-color:var(--bad-line); color:#991B1B;}
  .wb-pill-lo b {color:#991B1B;}

  /* ---------------- section labels ---------------- */
  .wb-sec {font-size:.74rem; letter-spacing:.1em; text-transform:uppercase;
           color:var(--ink-3); font-weight:700; margin:.85rem 0 .4rem 0;}

  /* ---------------- answer banner ---------------- */
  .wb-answer {background:var(--surface); border:1px solid var(--line);
              border-left:5px solid var(--accent); border-radius:var(--radius);
              box-shadow:var(--shadow); padding:.9rem 1.2rem;
              margin:.1rem 0 .9rem 0; display:flex; gap:1.4rem;
              align-items:flex-start; justify-content:space-between;}
  .wb-headline {font-size:1.3rem; font-weight:660; line-height:1.35;
                letter-spacing:-.01em; color:var(--ink); margin:0;}

  /* ---------------- trace steps ---------------- */
  .wb-step {background:var(--surface); border:1px solid var(--line);
            border-left:4px solid var(--line); border-radius:10px;
            padding:.55rem .8rem .6rem; margin-bottom:.45rem;
            box-shadow:var(--shadow);}
  .wb-step-run {border-left-color:var(--accent); background:var(--surface-2);}
  .wb-step-ok  {border-left-color:var(--good);}
  .wb-step-err {border-left-color:var(--warn); background:var(--warn-bg);}
  .wb-title {font-weight:650; font-size:1.02rem; line-height:1.35;
             color:var(--ink); display:flex; align-items:baseline; gap:.45rem;}
  .wb-badge {font-family:var(--mono); font-size:.7rem; color:var(--ink-3);
             border:1px solid var(--line); border-radius:5px;
             padding:.04rem .3rem; flex:0 0 auto;}
  .wb-summary {color:var(--ink-2); font-size:.92rem; margin-top:.18rem;
               line-height:1.4;}
  .wb-meta {font-family:var(--mono); font-size:.73rem; color:var(--ink-3);
            margin-top:.35rem; padding-top:.3rem;
            border-top:1px dashed var(--line-soft);}

  .wb-kv {margin-top:.4rem; font-size:.85rem; color:var(--ink-2);
          display:grid; grid-template-columns:minmax(5.5rem,9.5rem) 1fr;
          gap:.1rem .7rem; align-items:baseline;}
  .wb-kv span.k {color:var(--ink-3); overflow-wrap:anywhere;}
  .wb-kv span.v {font-weight:650; color:var(--ink); overflow-wrap:anywhere;
                 font-variant-numeric:tabular-nums;}

  /* ---------------- report ---------------- */
  .wb-card {background:var(--surface); border:1px solid var(--line);
            border-radius:var(--radius); box-shadow:var(--shadow);
            padding:1.1rem 1.25rem;}
  .wb-finding {margin-bottom:.55rem; line-height:1.5; color:var(--ink);}
  .wb-eid {font-family:var(--mono); font-size:.72rem; background:var(--bg);
           color:var(--ink-2); border:1px solid var(--line); border-radius:5px;
           padding:.06rem .34rem; margin-left:.3rem; white-space:nowrap;}
  .wb-hyp {color:var(--ink-3);}
  .wb-caveat {color:var(--ink-3); font-size:.87rem; line-height:1.5;
              border-left:2px solid var(--line); padding-left:.75rem;
              margin-top:.6rem;}
  .wb-score {font-size:3rem; font-weight:720; line-height:1;
             letter-spacing:-.03em; color:var(--ink);
             font-variant-numeric:tabular-nums;}

  /* ---------------- loader ---------------- */
  .wb-loader {display:flex; align-items:center; gap:.75rem;
              padding:.75rem 1.1rem; margin:.2rem 0 1rem 0;
              border:1px solid var(--line); border-radius:var(--radius);
              background:var(--surface); color:var(--ink-2);
              box-shadow:var(--shadow);}
  .wb-spin {width:17px; height:17px; border:3px solid var(--line);
            border-top-color:var(--accent); border-radius:50%;
            animation:wb-rot .8s linear infinite; flex:0 0 auto;}
  @keyframes wb-rot {to {transform:rotate(360deg);}}

  /* ---------------- streamlit widgets ---------------- */
  .stTabs [data-baseweb="tab-list"] {gap:.15rem; border-bottom:1px solid var(--line);}
  .stTabs [data-baseweb="tab"] {font-size:.97rem; padding:.55rem .95rem;
                                color:var(--ink-2);}
  .stTabs [aria-selected="true"] {color:var(--accent) !important; font-weight:650;}
  [data-testid="stExpander"] {border:1px solid var(--line) !important;
                              border-radius:10px !important;
                              background:var(--surface) !important;
                              box-shadow:var(--shadow);}
  [data-testid="stExpander"] summary {color:var(--ink-2) !important;
                                      font-size:.92rem;}
  .stTextInput input {font-size:1.02rem !important; padding:.62rem .85rem !important;
                      background:var(--surface) !important; color:var(--ink) !important;
                      border:1px solid var(--line) !important; border-radius:10px !important;}
  /* the primary button inherits textColor from the theme, which is near-black
     on a near-blue fill: force white so the label is readable */
  .stButton button {border-radius:10px !important; font-weight:650 !important;
                    padding:.62rem 1rem !important;}
  .stButton button[kind="primary"],
  .stButton button[data-testid="baseButton-primary"] {
      background:var(--accent) !important; border-color:var(--accent) !important;
      color:#FFFFFF !important;}
  .stButton button[kind="primary"] *,
  .stButton button[data-testid="baseButton-primary"] * {color:#FFFFFF !important;}
  .stButton button[kind="primary"]:hover {background:#1A43BC !important;}
  [data-testid="stPlotlyChart"] {background:var(--surface);
                                 border:1px solid var(--line);
                                 border-radius:var(--radius);
                                 box-shadow:var(--shadow);
                                 padding:.5rem .6rem;}
  hr {border-color:var(--line) !important;}

  /* ---------------------------------------------------------------
     Widget layer.

     Streamlit paints its widgets from its OWN theme, which follows the
     viewer's system preference when .streamlit/config.toml is absent.
     The rules above repaint our surfaces light, so without this block a
     dark-mode laptop gets a light page with dark dropdowns and
     invisible options. These overrides make the app self-sufficient:
     the config pins the theme, and this guarantees it.

     The select popover is rendered in a portal attached to <body>, not
     inside the sidebar, so it needs body-level selectors.
  --------------------------------------------------------------- */
  [data-testid="stSidebar"],
  [data-testid="stSidebar"] > div,
  [data-testid="stSidebarContent"],
  section[data-testid="stSidebar"] * {
      background-color:transparent; color:var(--ink);
  }
  [data-testid="stSidebar"] {background:var(--surface) !important;
                             border-right:1px solid var(--line);}
  [data-testid="stSidebar"] label,
  [data-testid="stSidebar"] .stMarkdown,
  [data-testid="stSidebar"] p,
  [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
  [data-testid="stSidebar"] h3 {color:var(--ink) !important;}
  [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] * {
      color:var(--ink-3) !important;}

  /* select / multiselect control */
  [data-baseweb="select"] > div, [data-baseweb="select"] input,
  [data-baseweb="input"] > div {
      background-color:var(--surface) !important; color:var(--ink) !important;
      border-color:var(--line) !important;}
  [data-baseweb="select"] svg {fill:var(--ink-2) !important;}

  /* dropdown popover: lives in a body-level portal */
  [data-baseweb="popover"] [data-baseweb="menu"],
  [data-baseweb="popover"] ul, div[data-baseweb="menu"],
  ul[role="listbox"] {
      background-color:var(--surface) !important; color:var(--ink) !important;
      border:1px solid var(--line) !important;
      box-shadow:0 8px 24px rgba(15,23,42,.14) !important;}
  li[role="option"], [data-baseweb="menu"] li {
      background-color:var(--surface) !important; color:var(--ink) !important;}
  li[role="option"]:hover, li[role="option"][aria-selected="true"],
  [data-baseweb="menu"] li:hover {
      background-color:#EEF2FF !important; color:var(--accent) !important;}

  /* radio, checkbox, slider */
  [data-testid="stRadio"] label, [data-testid="stCheckbox"] label,
  [data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] * {
      color:var(--ink) !important;}
  [data-baseweb="radio"] div[aria-checked="false"] > div {
      border-color:var(--ink-3) !important; background:var(--surface) !important;}
  [data-testid="stSlider"] [data-baseweb="slider"] div {color:var(--ink) !important;}

  /* expander — the collapsed "thinking" blocks */
  [data-testid="stExpander"] details {background:var(--surface) !important;
                                      border-radius:10px !important;}
  [data-testid="stExpander"] summary {background:var(--surface-2) !important;
                                      border-radius:10px !important;
                                      padding:.5rem .8rem !important;}
  [data-testid="stExpander"] summary:hover {background:#EEF2FF !important;}
  [data-testid="stExpander"] summary p,
  [data-testid="stExpander"] summary span,
  [data-testid="stExpander"] summary svg {color:var(--ink-2) !important;
                                          fill:var(--ink-2) !important;}
  [data-testid="stExpander"] [data-testid="stExpanderDetails"] {
      background:var(--surface) !important; color:var(--ink-2) !important;
      padding:.1rem .9rem .7rem !important; font-size:.93rem; line-height:1.5;}

  /* accent: without config.toml Streamlit's default primary is red, which
     fights the blue the rest of the app uses */
  [data-baseweb="radio"] div[aria-checked="true"] > div,
  [data-testid="stCheckbox"] label span[aria-checked="true"],
  [data-testid="stCheckbox"] [data-baseweb="checkbox"] span[data-checked="true"] {
      background-color:var(--accent) !important;
      border-color:var(--accent) !important;}
  [data-testid="stCheckbox"] [data-baseweb="checkbox"] span {
      border:1px solid var(--ink-3) !important; background:var(--surface) !important;}
  [data-testid="stSlider"] [role="slider"] {background-color:var(--accent) !important;}
  [data-testid="stSlider"] div[data-baseweb="slider"] > div > div > div:first-child {
      background:var(--accent) !important;}
  [data-testid="stSlider"] [data-testid="stThumbValue"] {color:var(--accent) !important;}

  /* info/empty states: neutral, not a blue slab */
  [data-testid="stAlert"] {border-radius:10px !important;
      background:var(--surface) !important; border:1px dashed var(--line) !important;
      color:var(--ink-2) !important; box-shadow:none !important;}
  [data-testid="stAlert"] * {color:var(--ink-2) !important;}
  [data-testid="stAlert"] svg {display:none;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

def _fmt_score(v):
    """74.5 stays 74.5; 78.0 shows as 78."""
    return f"{v:.0f}" if abs(v - round(v)) < 0.05 else f"{v:.1f}"


BUILD = "ui-2026-10-28f"   # shown in the sidebar; bump when this file changes
TRACE_HEIGHT = 640          # px; the trace scrolls inside this, the page does not

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

    # The answer goes first so it is the tab selected when a run finishes.
    specs = [("Answer", "ANSWER")] if st.session_state.report else []
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
            if build == "ANSWER":
                render_answer_panel()
            elif label == "Where?":
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
def render_answer_panel():
    """The report, stacked for a narrow column.

    This lives in the first tab beside the trace instead of below it: the old
    layout put the answer a full screen-scroll under an eight-step trace, which
    is exactly the scroll the playbook says not to do on stage.
    """
    rep = st.session_state.report
    conf = st.session_state.confidence
    if not rep:
        st.info("The report appears when the investigation finishes.")
        return

    if rep["actions"]:
        st.markdown("<div class='wb-sec' style='margin-top:.2rem;'>"
                    "Recommended action</div>"
                    + "".join(f"<div class='wb-finding'>• {a}</div>"
                              for a in rep["actions"]), unsafe_allow_html=True)

    if rep["findings"]:
        st.markdown("<div class='wb-sec'>Findings</div>"
                    + "".join(
                        f"<div class='wb-finding'>• {f['text']}"
                        + "".join(f"<span class='wb-eid'>{e}</span>"
                                  for e in f["evidence_ids"]) + "</div>"
                        for f in rep["findings"]), unsafe_allow_html=True)

    if rep["hypotheses"]:
        st.markdown("<div class='wb-sec'>Hypotheses — not verified, not scored</div>"
                    + "".join(
                        f"<div class='wb-finding wb-hyp'>• {h['text']}"
                        + (f" ({h['why']})" if h["why"] else "") + "</div>"
                        for h in rep["hypotheses"]), unsafe_allow_html=True)

    sig = st.session_state.norm.get("significance_test")
    if sig:
        word = STRENGTH_WORD.get(str(sig["strength"]).lower(), sig["strength"])
        iv = (f"[{ad.pct(sig['ci_low'])}, {ad.pct(sig['ci_high'])}]"
              if sig["effect_is_fraction"]
              else f"[{ad.money(sig['ci_low'])}, {ad.money(sig['ci_high'])}]")
        st.markdown(
            f"<div class='wb-sec'>Statistical check</div>"
            f"<div class='wb-finding'><b>{word}</b> · "
            f"{int(sig['ci_level']*100)}% interval {iv}"
            + (f" · p = {sig['p_value']:.4f}" if sig["p_value"] is not None else "")
            + "</div>"
            + (f"<div class='wb-caveat'>{sig['caveat']}</div>"
               if sig["caveat"] else ""), unsafe_allow_html=True)

    if conf and conf["components"]:
        st.markdown("<div class='wb-sec'>How confidence was computed</div>",
                    unsafe_allow_html=True)
        st.plotly_chart(charts.confidence_components(conf),
                        use_container_width=True,
                        config={"displayModeBar": False})
        for flag in conf["flags"]:
            st.warning(ad.pretty_label(flag))


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
        f"<div class='wb-answer' style='border-left-color:{accent};'>"
        f"<div class='wb-headline'>{rep['headline']}</div>"
        f"<div style='flex:0 0 auto;'>{badge}</div></div>",
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
    st.caption(f"build {BUILD}")

# a labelled opener, in case the chevron is missed on a projector
st.markdown("""
<style>
  [data-testid="stSidebarCollapsedControl"]::after,
  [data-testid="collapsedControl"]::after {
      content:"Demo controls"; font-size:.78rem; color:var(--ink-3);
      margin-left:.45rem; align-self:center; white-space:nowrap;}
</style>""", unsafe_allow_html=True)

# --------------------------------------------------------------------------
# header
# --------------------------------------------------------------------------
st.markdown(
    "<div class='wb-head'><div class='wb-mark'>?</div><div>"
    "<div class='wb-name'>WhyBot</div>"
    "<div class='wb-sub'>Ask why. Watch the investigation, not just the answer."
    "</div></div></div>", unsafe_allow_html=True)

qcol, bcol = st.columns([5, 1], gap="small")
# The question box keeps ONE stable widget key.
#
# It previously used key=f"q_{fixture_pick}_{mode}", so changing the source
# created a different widget every time. Streamlit then held state for a widget
# that no longer existed, and the next rerun raised
#   KeyError: st.session_state has no key "$$WIDGET_ID-...-Live agent"
# on the radio above it. A stable key plus an explicit value update does the
# same job without orphaning state, and it preserves a question the user typed.
QKEY = "wb_question"
default_q = (FIXTURE_QUESTIONS.get(fixture_pick) if mode == "Fixtures"
             else "Why did revenue drop in the last two months of 2011?")
if st.session_state.get("_wb_q_source") != (mode, fixture_pick):
    st.session_state[QKEY] = default_q
    st.session_state["_wb_q_source"] = (mode, fixture_pick)
question = qcol.text_input(
    "Question", key=QKEY, label_visibility="collapsed",
    placeholder="Why did revenue drop?")
run = bcol.button("Investigate", type="primary", use_container_width=True)
if run:
    reset_run()               # flush the old answer, banner and trace BEFORE anything below is drawn
    st.session_state.source_label = mode
loader = st.empty()

render_status_strip()
render_answer_banner()

# Layout: the answer and the charts lead; the trace is opt-in.
#
# The trace is still the thing that proves the system reasoned rather than
# guessed, so it is forced open WHILE a run streams -- watching it unfold is the
# demo. Afterwards it collapses to whatever the toggle says, which keeps the
# charts full width for the rest of the pitch.
_t1, _t2 = st.columns([3, 1], gap="small")
with _t2:
    show_trace = st.toggle("Investigation trace", value=False, key="show_trace",
                           help="The step-by-step reasoning. Open it when a judge "
                                "asks where a number came from.")
trace_visible = bool(show_trace) or bool(run)

if trace_visible:
    trace_col, eve_col = st.columns([2, 3], gap="large")
    trace_col.markdown("<div class='wb-sec'>Investigation trace</div>",
                       unsafe_allow_html=True)
    try:
        # scrolls internally: an 8-step trace no longer stretches the page
        trace_box = trace_col.container(height=TRACE_HEIGHT, border=False)
    except TypeError:                              # older Streamlit
        trace_box = trace_col.container()
    eve_col.markdown("<div class='wb-sec'>Evidence</div>", unsafe_allow_html=True)
else:
    eve_col = st.container()
    eve_col.markdown("<div class='wb-sec'>Evidence</div>", unsafe_allow_html=True)
    trace_box = None          # nothing is drawn; the render loop checks for this
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
            if trace_box is not None:
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
    if trace_box is not None:
        for ev in st.session_state.events:
            if ev.get("type") == "step_started":
                continue
            render_step(ev, trace_box)
    with eve_col:
        eve_slot.empty()
        render_evidence_tabs()

    if show_raw and st.session_state.evidence:
        st.divider()
        st.markdown("<div class='wb-sec'>Evidence store — every number on this "
                    "page came from here</div>", unsafe_allow_html=True)
        for eid, rec in st.session_state.evidence.items():
            with st.expander(f"{eid} · {rec['tool']}"):
                st.json(rec["result"])
else:
    if trace_box is not None:
        trace_box.info("Press **Investigate** to begin.")
    with eve_col:
        eve_slot.info("Charts appear as the investigation produces evidence.")