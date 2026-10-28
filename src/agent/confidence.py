"""
confidence.py - the confidence score, computed from the evidence store by code (the LLM cannot change it).
  confidence = 100 * (0.35*C + 0.30*S + 0.20*P + 0.15*R)      bands: 75+ High | 50-74 Medium | <50 Low
  C coverage    : share of the gross decline carried by the top slice of the most informative dimension
                  (country, customer_type, rfm_tier; uk_vs_international and tenure are mechanical splits and skipped)
  S significance: 0.5 * (significance_test strength: supporting 1, weak 0.5, none 0) + 0.5 * min(1, |z| / 3) from detect_anomaly
  P specificity : concentration (Herfindahl index of the gross-decline shares) in that same dimension
  R robustness  : share of detect_anomaly cross-checks (yoy, stl) that agree in sign with |z| >= 1 (a single check caps at 0.5)
Weights are a starting point: tune on the evaluation set, then freeze.
"""
W = {"coverage": 0.35, "significance": 0.30, "specificity": 0.20, "robustness": 0.15}
SKIP_DIMS = ("uk_vs_international", "tenure")


def _results(ev, tool):
    return [e["result"] for e in ev if e["tool"] == tool]


def compute_confidence(evidence):
    flags = []
    best_c, best_p = 0.0, 0.0
    for r in _results(evidence, "drill_down"):
        for dim, rows in r["contributors"].items():
            if dim in SKIP_DIMS:
                continue
            shares = [x["share_of_gross_decline"] for x in rows if x["value"] != "(other)"]
            top = max(shares, default=0.0)
            if top > best_c:
                best_c = top
                best_p = sum(s * s for s in [x["share_of_gross_decline"] for x in rows])
    C, P = min(1.0, best_c), min(1.0, best_p)

    sig = _results(evidence, "significance_test")
    sig_part = max([{"supporting": 1.0, "weak": 0.5}.get(s["strength"], 0.0) for s in sig], default=0.0)
    an = _results(evidence, "detect_anomaly")
    z_part = min(1.0, abs(an[0]["z_score"]) / 3) if an else 0.0
    S = 0.5 * sig_part + 0.5 * z_part
    if not sig:
        flags.append("no significance test was run")

    passes = total = 0
    for r in an:
        sign = 1 if r["change_pct"] >= 0 else -1
        for c in r.get("checks", []):
            total += 1
            passes += int((1 if c["change_pct"] >= 0 else -1) == sign and abs(c["z_score"]) >= 1)
    R = passes / max(total, 2) if total else 0.0

    for r in _results(evidence, "customer_movement"):
        if r.get("concentrated_in_few_accounts"):
            flags.append("concentrated in few accounts")
    score = 100 * (W["coverage"] * C + W["significance"] * S + W["specificity"] * P + W["robustness"] * R)
    band = "High" if score >= 75 else "Medium" if score >= 50 else "Low"
    return {"score": round(score, 1), "band": band,
            "components": {"coverage": round(C, 3), "significance": round(S, 3),
                           "specificity": round(P, 3), "robustness": round(R, 3)}, "flags": flags}