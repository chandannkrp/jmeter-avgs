import re

from google.adk.tools import ToolContext

from .jmx_tools import analyze_jmx_file
from .openapi_tools import extract_endpoints, load_spec, server_info


def _pattern(spec_path: str) -> re.Pattern:
    """/users/{id} -> regex that also matches /users/${id} or /users/42."""
    parts = re.split(r"(\{[^}]+\})", spec_path)
    regex = "".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts)
    return re.compile(regex + "/?")


def find_coverage_gaps(spec_source: str, jmx_path: str, tool_context: ToolContext) -> dict:
    """Compares an OpenAPI/Swagger spec with a JMeter .jmx plan to find untested endpoints.

    Args:
        spec_source: File path or http(s) URL of the OpenAPI/Swagger spec.
        jmx_path: File path of the .jmx test plan.

    Returns:
        A dict with coverage percent, covered endpoints, uncovered endpoints, and
        JMX requests that do not exist in the spec.
    """
    cached = tool_context.state.get("jmx_analysis")
    analysis = cached if cached and cached.get("source_path") == jmx_path else analyze_jmx_file(jmx_path)
    if analysis["status"] != "success":
        return analysis
    try:
        spec = load_spec(spec_source)
        endpoints = extract_endpoints(spec)
    except Exception as e:
        return {"status": "error", "error_message": f"Could not read spec: {e}"}

    base = server_info(spec, spec_source)["base_path"]
    samplers = analysis["summary"]["samplers"]
    covered, uncovered, matched = [], [], set()
    for ep in endpoints:
        pattern, hit = _pattern(ep["path"]), None
        for i, s in enumerate(samplers):
            p = (s["path"] or "").split("?")[0]
            if base and p.startswith(base):
                p = p[len(base):]
            if s["method"].upper() == ep["method"] and pattern.fullmatch(p):
                hit = i
                break
        label = f'{ep["method"]} {ep["path"]}'
        if hit is None:
            uncovered.append(label)
        else:
            covered.append(label)
            matched.add(hit)

    total = len(endpoints)
    return {
        "status": "success",
        "coverage_percent": round(100 * len(covered) / total, 1) if total else 0.0,
        "covered": covered,
        "uncovered": uncovered,
        "in_jmx_but_not_in_spec": [f'{s["method"]} {s["path"]}'
                                   for i, s in enumerate(samplers) if i not in matched],
    }