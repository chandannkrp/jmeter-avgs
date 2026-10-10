import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from .safety import MAX_SPEC_BYTES, check_url, opener, safe_path

import yaml

HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def load_spec(source: str) -> dict:
    """Load an OpenAPI 3 / Swagger 2 spec from a file path or http(s) URL (JSON or YAML)."""
    if source.startswith(("http://", "https://")):
        check_url(source)
        with opener.open(source, timeout=15) as resp:
            text = resp.read(MAX_SPEC_BYTES).decode("utf-8")
    else:
        text = safe_path(source).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return yaml.safe_load(text)


def server_info(spec: dict, source: str = "") -> dict:
    """Protocol/host/port/base_path for OpenAPI 3 (servers) and Swagger 2 (host, basePath)."""
    src = urlparse(source) if source.startswith("http") else None
    if "openapi" in spec:
        p = urlparse((spec.get("servers") or [{"url": "/"}])[0]["url"])
        relative = p.hostname is None  # e.g. "/api/v3": borrow host from the spec URL
        host = p.hostname or (src.hostname if src else "localhost")
        scheme = p.scheme or (src.scheme if src else "http")
        port = p.port or (src.port if src and relative else None)
        return {"protocol": scheme, "host": host, "port": str(port or ""),
                "base_path": p.path.rstrip("/")}
    host, _, port = spec.get("host", "localhost").partition(":")
    return {"protocol": (spec.get("schemes") or ["http"])[0], "host": host, "port": port,
            "base_path": spec.get("basePath", "").rstrip("/")}


def has_security(spec: dict) -> bool:
    return bool(spec.get("components", {}).get("securitySchemes")
                or spec.get("securityDefinitions"))


def _resolve(spec: dict, ref: str):
    node = spec
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    return node


def _deref(spec: dict, node):
    return _resolve(spec, node["$ref"]) if isinstance(node, dict) and "$ref" in node else node


def sample_value(spec: dict, schema, depth: int = 0):
    """Build a realistic sample value from a JSON schema (resolves $ref, depth-limited)."""
    if not isinstance(schema, dict) or depth > 5:
        return None
    if "$ref" in schema:
        try:
            return sample_value(spec, _resolve(spec, schema["$ref"]), depth + 1)
        except KeyError:
            return None
    for key in ("example", "default"):
        if key in schema:
            return schema[key]
    if schema.get("enum"):
        return schema["enum"][0]
    for key in ("allOf", "oneOf", "anyOf"):
        if key in schema:
            parts = [sample_value(spec, s, depth + 1) for s in schema[key]]
            if key == "allOf":
                merged = {}
                for part in parts:
                    if isinstance(part, dict):
                        merged.update(part)
                return merged
            return parts[0] if parts else None
    t = schema.get("type") or ("object" if "properties" in schema else None)
    if t == "object":
        return {k: sample_value(spec, v, depth + 1)
                for k, v in schema.get("properties", {}).items()}
    if t == "array":
        return [sample_value(spec, schema.get("items", {}), depth + 1)]
    if t == "integer":
        return 1
    if t == "number":
        return 1.5
    if t == "boolean":
        return True
    if t == "string":
        formats = {"date-time": "2025-01-01T00:00:00Z", "date": "2025-01-01",
                   "uuid": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "email": "test@example.com"}
        return formats.get(schema.get("format"), "string")
    return None


def extract_endpoints(spec: dict) -> list:
    """Flatten the spec into a list of endpoints with sample params and bodies."""
    def value(spec, p):
        v = sample_value(spec, p.get("schema", p))
        return 1 if v is None else v

    endpoints = []
    for path, item in spec.get("paths", {}).items():
        for method in HTTP_METHODS:
            op = item.get(method)
            if not op:
                continue
            params = [_deref(spec, p) for p in item.get("parameters", []) + op.get("parameters", [])]

            body = None
            if op.get("requestBody"):
                content = _deref(spec, op["requestBody"]).get("content", {})
                schema = content.get("application/json", {}).get("schema")
                body = sample_value(spec, schema) if schema else None
            else:  # Swagger 2 style body parameter
                for p in params:
                    if p.get("in") == "body":
                        body = sample_value(spec, p.get("schema", {}))

            ok = sorted(str(c) for c in op.get("responses", {}) if str(c).startswith("2"))
            endpoints.append({
                "method": method.upper(),
                "path": path,
                "summary": op.get("summary") or op.get("operationId") or "",
                "path_params": {p["name"]: value(spec, p) for p in params if p.get("in") == "path"},
                "query_params": {
                    p["name"]: value(spec, p) for p in params
                    if p.get("in") == "query" and (p.get("required") or "default" in p.get("schema", p))
                },
                "body": body,
                "expected_status": ok[0] if ok else "200",
            })
    return endpoints


def parse_openapi(spec_source: str) -> dict:
    """Reads an OpenAPI/Swagger spec and lists its API endpoints.

    Args:
        spec_source: File path or http(s) URL of the OpenAPI/Swagger JSON or YAML spec.

    Returns:
        A dict with status, API title, server details and a compact endpoint list.
    """
    try:
        spec = load_spec(spec_source)
        endpoints = extract_endpoints(spec)
    except Exception as e:
        return {"status": "error", "error_message": f"Could not read spec: {e}"}
    return {
        "status": "success",
        "title": spec.get("info", {}).get("title", ""),
        "server": server_info(spec, spec_source),
        "secured": has_security(spec),
        "endpoint_count": len(endpoints),
        "endpoints": [{"method": e["method"], "path": e["path"], "summary": e["summary"],
                       "has_body": e["body"] is not None} for e in endpoints],
    }


if __name__ == "__main__":
    print(json.dumps(extract_endpoints(load_spec(sys.argv[1])), indent=2))