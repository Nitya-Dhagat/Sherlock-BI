"""
validator.py - the hallucination guard. Every number in the report must match a number returned by a tool.
Rules: dates, evidence ids, years and small counts (integers <= 20 without a unit) are ignored. Rounding is tolerated
to the precision written ("$2.2M" matches 2,207,758.64; "14%" matches 13.7). Fractions in tool output also match as
percentages (0.742 matches "74%"). Citations must point at existing evidence ids.
"""
import re

DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
EID_RE = re.compile(r"\bE\d+\b")
NUM_RE = re.compile(r"(?P<cur>[$£€])?\s?(?P<num>\d[\d,]*(?:\.\d+)?)\s?(?P<unit>%|percent\b|[kKmM]\b|million\b|thousand\b)?")


def _walk(x, out):
    if isinstance(x, bool):
        return
    if isinstance(x, (int, float)):
        out.append(abs(float(x)))
    elif isinstance(x, dict):
        for v in x.values():
            _walk(v, out)
    elif isinstance(x, (list, tuple)):
        for v in x:
            _walk(v, out)


def evidence_numbers(evidence):
    raw = []
    for e in evidence:
        _walk(e["result"], raw)
    return raw + [v * 100 for v in raw if v <= 10]       # fractions double as percentages


def _texts(report):
    yield report.get("headline", "")
    for f in report.get("findings", []):
        yield f.get("text", "")
    for a in report.get("actions", []):
        yield a.get("text", "")
    for h in report.get("hypotheses", []):
        yield h if isinstance(h, str) else str(h)


def check_report(report, evidence):
    cands, ids = evidence_numbers(evidence), {e["id"] for e in evidence}
    unmatched, checked = [], 0
    for text in _texts(report):
        t = EID_RE.sub(" ", DATE_RE.sub(" ", text))
        for m in NUM_RE.finditer(t):
            raw, unit, cur = m.group("num"), (m.group("unit") or "").lower(), m.group("cur")
            val = float(raw.replace(",", ""))
            has_dec = "." in raw
            if not unit and not cur and not has_dec and (val <= 20 or (1990 <= val <= 2035 and "," not in raw)):
                continue
            scale = 1e3 if unit in ("k", "thousand") else 1e6 if unit in ("m", "million") else 1.0
            decimals = len(raw.split(".")[1]) if has_dec else 0
            x, tol = val * scale, 0.5 * 10 ** (-decimals) * scale + 1e-9
            checked += 1
            if not any(abs(c - x) <= tol * 1.0001 for c in cands):
                unmatched.append(m.group(0).strip())
    bad = []
    for f in report.get("findings", []):
        fid = f.get("evidence_ids") or []
        if not fid:
            bad.append(f"finding without evidence ids: {f.get('text', '')[:60]}")
        bad += [f"unknown evidence id {i}" for i in fid if i not in ids]
    for a in report.get("actions", []):
        bad += [f"unknown evidence id {i}" for i in (a.get("evidence_ids") or []) if i not in ids]
    return {"ok": not unmatched and not bad, "unmatched": unmatched, "bad_citations": bad, "checked": checked}