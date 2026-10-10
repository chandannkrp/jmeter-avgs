import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from .safety import safe_path

from google.adk.tools import ToolContext

from .openapi_tools import extract_endpoints, has_security, load_spec, server_info


def _node(tag, testname, guiclass, testclass):
    el = ET.Element(tag, guiclass=guiclass, testclass=testclass, testname=testname, enabled="true")
    return el


def _sp(parent, kind, name, value):
    if isinstance(value, bool):
        value = str(value).lower()
    p = ET.SubElement(parent, kind, name=name)
    p.text = str(value)
    return p


def _add(hash_tree, element):
    """JMX pattern: every element is followed by a hashTree holding its children."""
    hash_tree.append(element)
    return ET.SubElement(hash_tree, "hashTree")


def _variables(variables: dict):
    el = _node("Arguments", "User Defined Variables", "ArgumentsPanel", "Arguments")
    coll = ET.SubElement(el, "collectionProp", name="Arguments.arguments")
    for k, v in variables.items():
        arg = ET.SubElement(coll, "elementProp", name=k, elementType="Argument")
        _sp(arg, "stringProp", "Argument.name", k)
        _sp(arg, "stringProp", "Argument.value", v)
        _sp(arg, "stringProp", "Argument.metadata", "=")
    return el


def _defaults(info):
    d = _node("ConfigTestElement", "HTTP Request Defaults", "HttpDefaultsGui", "ConfigTestElement")
    args = ET.SubElement(d, "elementProp", name="HTTPsampler.Arguments", elementType="Arguments",
                         guiclass="HTTPArgumentsPanel", testclass="Arguments",
                         testname="User Defined Variables", enabled="true")
    ET.SubElement(args, "collectionProp", name="Arguments.arguments")
    _sp(d, "stringProp", "HTTPSampler.domain", "${host}")
    if info["port"]:
        _sp(d, "stringProp", "HTTPSampler.port", "${port}")
    _sp(d, "stringProp", "HTTPSampler.protocol", "${protocol}")
    return d


def _headers(secured):
    h = _node("HeaderManager", "HTTP Header Manager", "HeaderPanel", "HeaderManager")
    coll = ET.SubElement(h, "collectionProp", name="HeaderManager.headers")
    pairs = [("Content-Type", "application/json"), ("Accept", "application/json")]
    if secured:
        pairs.append(("Authorization", "Bearer ${token}"))
    for k, v in pairs:
        e = ET.SubElement(coll, "elementProp", name="", elementType="Header")
        _sp(e, "stringProp", "Header.name", k)
        _sp(e, "stringProp", "Header.value", v)
    return h


def _thread_group():
    tg = _node("ThreadGroup", "Fury Fleet Users", "ThreadGroupGui", "ThreadGroup")
    _sp(tg, "stringProp", "ThreadGroup.on_sample_error", "continue")
    loop = ET.SubElement(tg, "elementProp", name="ThreadGroup.main_controller",
                         elementType="LoopController", guiclass="LoopControlPanel",
                         testclass="LoopController", testname="Loop Controller", enabled="true")
    _sp(loop, "boolProp", "LoopController.continue_forever", False)
    _sp(loop, "intProp", "LoopController.loops", -1)
    _sp(tg, "stringProp", "ThreadGroup.num_threads", "${__P(threads,10)}")
    _sp(tg, "stringProp", "ThreadGroup.ramp_time", "${__P(rampup,30)}")
    _sp(tg, "boolProp", "ThreadGroup.scheduler", True)
    _sp(tg, "stringProp", "ThreadGroup.duration", "${__P(duration,120)}")
    _sp(tg, "stringProp", "ThreadGroup.delay", "")
    _sp(tg, "boolProp", "ThreadGroup.same_user_on_next_iteration", True)
    return tg


def _timer():
    t = _node("UniformRandomTimer", "Think Time", "UniformRandomTimerGui", "UniformRandomTimer")
    _sp(t, "stringProp", "ConstantTimer.delay", "${__P(think_ms,500)}")
    _sp(t, "stringProp", "RandomTimer.range", "${__P(think_range_ms,1000)}")
    return t


def _assertion(code):
    a = _node("ResponseAssertion", "Assert status code", "AssertionGui", "ResponseAssertion")
    coll = ET.SubElement(a, "collectionProp", name="Asserion.test_strings")  # JMeter's own spelling
    _sp(coll, "stringProp", "0", code)
    _sp(a, "stringProp", "Assertion.custom_message", "")
    _sp(a, "stringProp", "Assertion.test_field", "Assertion.response_code")
    _sp(a, "boolProp", "Assertion.assume_success", False)
    _sp(a, "intProp", "Assertion.test_type", 8)  # 8 = Equals
    return a


def _sampler(ep, base_path):
    path = re.sub(r"\{([^}]+)\}", r"${\1}", ep["path"])
    s = _node("HTTPSamplerProxy", f'{ep["method"]} {ep["path"]}', "HttpTestSampleGui", "HTTPSamplerProxy")
    args = ET.SubElement(s, "elementProp", name="HTTPsampler.Arguments", elementType="Arguments",
                         guiclass="HTTPArgumentsPanel", testclass="Arguments",
                         testname="User Defined Variables", enabled="true")
    coll = ET.SubElement(args, "collectionProp", name="Arguments.arguments")
    if ep["body"] is not None:
        _sp(s, "boolProp", "HTTPSampler.postBodyRaw", True)
        a = ET.SubElement(coll, "elementProp", name="", elementType="HTTPArgument")
        _sp(a, "boolProp", "HTTPArgument.always_encode", False)
        _sp(a, "stringProp", "Argument.value", json.dumps(ep["body"], indent=2))
        _sp(a, "stringProp", "Argument.metadata", "=")
    for k, v in ep["query_params"].items():
        a = ET.SubElement(coll, "elementProp", name=k, elementType="HTTPArgument")
        _sp(a, "boolProp", "HTTPArgument.always_encode", True)
        _sp(a, "stringProp", "Argument.value", v)
        _sp(a, "stringProp", "Argument.metadata", "=")
        _sp(a, "boolProp", "HTTPArgument.use_equals", True)
        _sp(a, "stringProp", "Argument.name", k)
    _sp(s, "stringProp", "HTTPSampler.path", f"{base_path}{path}")
    _sp(s, "stringProp", "HTTPSampler.method", ep["method"])
    _sp(s, "boolProp", "HTTPSampler.follow_redirects", True)
    _sp(s, "boolProp", "HTTPSampler.use_keepalive", True)
    return s


def build_jmx(spec: dict, endpoints: list, source: str = "") -> ET.ElementTree:
    info = server_info(spec, source)
    secured = has_security(spec)
    title = spec.get("info", {}).get("title", "API")

    root = ET.Element("jmeterTestPlan", version="1.2", properties="5.0", jmeter="5.6.3")
    top = ET.SubElement(root, "hashTree")
    plan = _node("TestPlan", f"{title} - Load Test", "TestPlanGui", "TestPlan")
    _sp(plan, "boolProp", "TestPlan.functional_mode", False)
    _sp(plan, "boolProp", "TestPlan.serialize_threadgroups", False)
    plan_tree = _add(top, plan)

    variables = {"protocol": info["protocol"], "host": info["host"], "port": info["port"]}
    if secured:
        variables["token"] = "CHANGE_ME"
    for ep in endpoints:
        for name, val in ep["path_params"].items():
            variables.setdefault(name, val)

    _add(plan_tree, _variables(variables))
    _add(plan_tree, _defaults(info))
    _add(plan_tree, _headers(secured))
    tg_tree = _add(plan_tree, _thread_group())
    _add(tg_tree, _timer())
    for ep in endpoints:
        s_tree = _add(tg_tree, _sampler(ep, info["base_path"]))
        _add(s_tree, _assertion(ep["expected_status"]))
    return ET.ElementTree(root)


def generate_jmx_file(spec_source: str, output_path: str) -> dict:
    """Pure function (testable without the LLM): spec in, .jmx file out."""
    try:
        spec = load_spec(spec_source)
        endpoints = extract_endpoints(spec)
    except Exception as e:
        return {"status": "error", "error_message": f"Could not read spec: {e}"}
    if not endpoints:
        return {"status": "error", "error_message": "The spec contains no endpoints."}

    tree = build_jmx(spec, endpoints, spec_source)
    ET.indent(tree, space="  ")
    try:
        out = safe_path(output_path)   # jtl_path in the JTL tool
    except PermissionError as e:
        return {"status": "error", "error_message": str(e)}
    out.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(out), encoding="utf-8", xml_declaration=True)
    return {
        "status": "success",
        "output_path": str(out),
        "samplers_created": len(endpoints),
        "endpoints": [f'{e["method"]} {e["path"]}' for e in endpoints],
        "secured_api_needs_token": has_security(spec),
        "run_command": f"jmeter -n -t {out} -l results.jtl -Jthreads=10 -Jrampup=30 -Jduration=120",
    }


def generate_jmx(spec_source: str, output_path: str, tool_context: ToolContext) -> dict:
    """Generates a ready-to-run JMeter .jmx test plan from an OpenAPI/Swagger spec.

    Args:
        spec_source: File path or http(s) URL of the OpenAPI/Swagger spec.
        output_path: Where to write the plan, for example output/users-api.jmx.

    Returns:
        A dict with status, the output path, the endpoints covered and a JMeter run command.
    """
    result = generate_jmx_file(spec_source, output_path)
    if result["status"] == "success":
        tool_context.state["generated_jmx_path"] = output_path
    return result


if __name__ == "__main__":
    print(json.dumps(generate_jmx_file(sys.argv[1], sys.argv[2]), indent=2))