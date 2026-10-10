import json
import sys
from pathlib import Path

import pandas as pd
from google.adk.tools import ToolContext
from .safety import safe_path

REQUIRED = {"timeStamp", "elapsed", "label", "success"}


def _r(x) -> float:
    return float(round(x, 1))


def _stats(df: pd.DataFrame) -> dict:
    e = df["elapsed"]
    return {
        "samples": int(len(df)),
        "error_pct": float(round(100 * (~df["ok"]).mean(), 2)),
        "avg_ms": _r(e.mean()),
        "p50_ms": _r(e.quantile(0.50)),
        "p90_ms": _r(e.quantile(0.90)),
        "p95_ms": _r(e.quantile(0.95)),
        "p99_ms": _r(e.quantile(0.99)),
        "max_ms": int(e.max()),
    }


def analyze_jtl_file(jtl_path: str, p95_sla_ms: int, max_error_pct: float) -> dict:
    """Pure function: compute stats and rule-based findings from a CSV JTL file."""
    try:
        path = safe_path(jtl_path)   # jtl_path in the JTL tool
    except PermissionError as e:
        return {"status": "error", "error_message": str(e)}
    if not path.exists():
        return {"status": "error", "error_message": f"File not found: {jtl_path}"}
    if not path.exists():
        return {"status": "error", "error_message": f"File not found: {jtl_path}"}
    try:
        df = pd.read_csv(path)
    except Exception as e:
        return {"status": "error", "error_message":
                f"Could not read as a CSV JTL (XML JTL is not supported): {e}"}
    missing = REQUIRED - set(df.columns)
    if missing:
        return {"status": "error", "error_message":
                f"Missing JTL columns: {sorted(missing)}. Save results as CSV with a header row."}

    df["elapsed"] = pd.to_numeric(df["elapsed"], errors="coerce")
    df["timeStamp"] = pd.to_numeric(df["timeStamp"], errors="coerce")
    df = df.dropna(subset=["elapsed", "timeStamp"]).copy()
    if df.empty:
        return {"status": "error", "error_message": "The JTL file has no usable samples."}
    df["ok"] = df["success"].astype(str).str.lower().eq("true")

    start = df["timeStamp"].min()
    end = (df["timeStamp"] + df["elapsed"]).max()
    duration_s = max((end - start) / 1000, 0.001)
    overall = _stats(df)
    overall["duration_s"] = _r(duration_s)
    overall["throughput_rps"] = float(round(len(df) / duration_s, 2))

    per_label = []
    for label, g in df.groupby("label"):
        s = {"label": label, **_stats(g)}
        fails = g[~g["ok"]]
        if "responseCode" in g.columns and not fails.empty:
            top = fails["responseCode"].astype(str).value_counts().head(3)
            s["top_errors"] = {k: int(v) for k, v in top.items()}
        per_label.append(s)
    per_label.sort(key=lambda s: s["p95_ms"], reverse=True)

    timeline = []
    if duration_s >= 10 and len(df) >= 50:
        df["window"] = pd.cut(df["timeStamp"], bins=10, labels=False)
        for w, g in df.groupby("window"):
            timeline.append({
                "window": int(w) + 1,
                "samples": int(len(g)),
                "p95_ms": _r(g["elapsed"].quantile(0.95)),
                "error_pct": float(round(100 * (~g["ok"]).mean(), 2)),
            })

    findings = []

    def add(severity, issue):
        findings.append({"severity": severity, "issue": issue})

    if overall["samples"] < 100:
        add("medium", f"Only {overall['samples']} samples: percentiles are unreliable. Run longer.")
    if overall["p95_ms"] > p95_sla_ms:
        add("high", f"Overall p95 {overall['p95_ms']} ms exceeds the {p95_sla_ms} ms SLA.")
    if overall["error_pct"] > max_error_pct:
        add("high", f"Overall error rate {overall['error_pct']}% exceeds the {max_error_pct}% limit.")
    slow = [s for s in per_label if s["p95_ms"] > p95_sla_ms][:3]
    if slow:
        add("high", "Slowest endpoints over SLA: " +
            ", ".join(f"{s['label']} (p95 {s['p95_ms']} ms)" for s in slow))
    bad = [s for s in per_label if s["error_pct"] > max_error_pct][:3]
    if bad:
        add("high", "Endpoints over the error limit: " +
            ", ".join(f"{s['label']} ({s['error_pct']}%)" for s in bad))
    if len(timeline) == 10:
        first = sorted(w["p95_ms"] for w in timeline[:3])[1]
        last = sorted(w["p95_ms"] for w in timeline[-3:])[1]
        if last > 1.5 * first and last - first > 100:
            add("high", f"Latency degrades under sustained load: p95 rose from about {first} ms "
                        f"(early run) to about {last} ms (late run).")
        spikes = [w["window"] for w in timeline if w["error_pct"] > max(5.0, 3 * overall["error_pct"])]
        if spikes:
            add("high", f"Error spike in time windows {spikes} (of 10 equal windows).")

    return {
        "status": "success",
        "sla": {"p95_ms": p95_sla_ms, "max_error_pct": max_error_pct},
        "overall": overall,
        "per_label": per_label[:15],
        "timeline": timeline,
        "findings": findings,
    }


def analyze_jtl(jtl_path: str, p95_sla_ms: int, max_error_pct: float,
                tool_context: ToolContext) -> dict:
    """Analyzes JMeter results (a CSV .jtl file): response-time percentiles, error rates,
    per-endpoint breakdown, degradation over time, and SLA findings.

    Args:
        jtl_path: File path of the CSV .jtl results file.
        p95_sla_ms: The p95 response-time SLA in milliseconds (use 1000 if the user gave none).
        max_error_pct: The maximum acceptable error percentage (use 1.0 if the user gave none).

    Returns:
        A dict with overall stats, per-endpoint stats, a 10-window timeline and findings.
    """
    result = analyze_jtl_file(jtl_path, p95_sla_ms, max_error_pct)
    if result["status"] == "success":
        tool_context.state["jtl_analysis"] = result
    return result


if __name__ == "__main__":
    print(json.dumps(analyze_jtl_file(sys.argv[1], 1000, 1.0), indent=2))