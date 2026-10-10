import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from .safety import safe_path

from google.adk.tools import ToolContext

THREAD_GROUP_TAGS = {"ThreadGroup", "SetupThreadGroup", "PostThreadGroup"}
TIMER_TAGS = {
    "ConstantTimer", "UniformRandomTimer", "GaussianRandomTimer",
    "PoissonRandomTimer", "ConstantThroughputTimer",
    "PreciseThroughputTimer", "SyncTimer",
}
ASSERTION_TAGS = {
    "ResponseAssertion", "JSONPathAssertion", "DurationAssertion",
    "SizeAssertion", "XPathAssertion", "JSR223Assertion",
}
# Listeners that hold every sample in memory (matched on guiclass)
HEAVY_LISTENERS = {"ViewResultsFullVisualizer", "TableVisualizer", "GraphVisualizer"}


def _prop(el, name, default=None):
    """Read a JMeter property (stringProp/intProp/boolProp...) by its name attribute."""
    for child in el:
        if child.get("name") == name:
            return child.text if child.text is not None else default
    return default


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None  # e.g. ${__P(threads,50)} expressions


def analyze_jmx_file(jmx_path: str) -> dict:
    """Pure function: parse a JMX file and apply rule-based checks. Easy to unit test."""
    try:
        path = safe_path(jmx_path)   # jtl_path in the JTL tool
    except PermissionError as e:
        return {"status": "error", "error_message": str(e)}
    if not path.exists():
        return {"status": "error", "error_message": f"File not found: {jmx_path}"}
    if not path.exists():
        return {"status": "error", "error_message": f"File not found: {jmx_path}"}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        return {"status": "error", "error_message": f"Invalid XML: {e}"}

    thread_groups, samplers, heavy = [], [], []
    timers = assertions = 0
    hosts = set()

    for el in root.iter():
        if el.get("enabled", "true") != "true":
            continue
        guiclass = el.get("guiclass", "")

        if el.tag in THREAD_GROUP_TAGS:
            loop = el.find("elementProp[@name='ThreadGroup.main_controller']")
            thread_groups.append({
                "name": el.get("testname"),
                "type": el.tag,
                "threads": _prop(el, "ThreadGroup.num_threads"),
                "ramp_up_s": _prop(el, "ThreadGroup.ramp_time"),
                "loops": _prop(loop, "LoopController.loops") if loop is not None else None,
                "scheduler": _prop(el, "ThreadGroup.scheduler") == "true",
                "duration_s": _prop(el, "ThreadGroup.duration"),
            })
        elif el.tag == "HTTPSamplerProxy":
            domain = _prop(el, "HTTPSampler.domain")
            samplers.append({
                "name": el.get("testname"),
                "method": _prop(el, "HTTPSampler.method", "GET"),
                "protocol": _prop(el, "HTTPSampler.protocol", "http"),
                "domain": domain,
                "path": _prop(el, "HTTPSampler.path"),
            })
            if domain:
                hosts.add(domain)
        elif el.tag in TIMER_TAGS:
            timers += 1
        elif el.tag in ASSERTION_TAGS:
            assertions += 1
        elif guiclass in HEAVY_LISTENERS:
            heavy.append(el.get("testname"))

    findings = []

    def add(severity, issue):
        findings.append({"severity": severity, "issue": issue})

    if not samplers:
        add("high", "No enabled HTTP samplers found, so the plan sends no requests.")
    for tg in thread_groups:
        name, threads = tg["name"], _to_int(tg["threads"])
        if threads and threads > 10 and _to_int(tg["ramp_up_s"]) == 0:
            add("high", f"Thread group '{name}': {threads} users start at once (ramp-up 0s). "
                        "This is a spike, not a realistic load profile.")
        if tg["loops"] == "-1" and not tg["scheduler"]:
            add("high", f"Thread group '{name}': infinite loops with no scheduler duration, "
                        "so the test never stops on its own.")
    if samplers and timers == 0:
        add("medium", "No timers: requests fire back-to-back with no think time, "
                      "which overstates real user throughput.")
    if samplers and assertions == 0:
        add("medium", "No assertions: an HTTP 200 with an error body counts as a success.")
    if heavy:
        add("medium", f"Memory-heavy listeners enabled: {', '.join(heavy)}. "
                      "Disable them for real load runs and use CLI + JTL output.")
    hardcoded = sorted(h for h in hosts if not h.startswith("${"))
    if hardcoded:
        add("low", f"Hardcoded host(s): {', '.join(hardcoded)}. Use HTTP Request Defaults "
                   "or a variable so the plan runs across environments.")

    return {
        "status": "success",
        "summary": {
            "thread_groups": thread_groups,
            "samplers": samplers,
            "timer_count": timers,
            "assertion_count": assertions,
        },
        "findings": findings,
    }


def analyze_jmx(jmx_path: str, tool_context: ToolContext) -> dict:
    """Analyzes a JMeter .jmx test plan file and returns its structure and design issues.

    Args:
        jmx_path: File path to the .jmx file, for example samples/sample.jmx.

    Returns:
        A dict with status, a summary (thread groups, samplers, timers, assertions)
        and a list of findings, each with a severity and an issue description.
    """
    result = analyze_jmx_file(jmx_path)
    if result["status"] == "success":
        tool_context.state["jmx_analysis"] = {**result, "source_path": jmx_path}  # reused by later agents
    return result


if __name__ == "__main__":
    # Test without the LLM: python -m fury_fleet.tools.jmx_tools samples/sample.jmx
    print(json.dumps(analyze_jmx_file(sys.argv[1]), indent=2))