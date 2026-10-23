"""Generate frontend wire types from the reviewed OpenAPI schemas (requires PyYAML)."""

import argparse
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def ts_type(schema):
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[1]
    if "const" in schema:
        return json.dumps(schema["const"], ensure_ascii=False)
    if "enum" in schema:
        return " | ".join(json.dumps(value, ensure_ascii=False) for value in schema["enum"])
    for keyword, separator in (("oneOf", " | "), ("anyOf", " | "), ("allOf", " & ")):
        if keyword in schema and schema.get("type") != "object":
            return separator.join(f"({ts_type(part)})" for part in schema[keyword])
    kind = schema.get("type")
    if isinstance(kind, list):
        return " | ".join(ts_type({**schema, "type": part}) for part in kind)
    if kind == "array":
        return f"Array<{ts_type(schema['items'])}>"
    if kind == "object":
        fields = []
        required = schema.get("required", [])
        for name, prop in schema.get("properties", {}).items():
            optional = "" if name in required else "?"
            fields.append(f"  {name}{optional}: {ts_type(prop)};")
        extra = schema.get("additionalProperties", False)
        if extra:
            fields.append(f"  [key: string]: {ts_type(extra) if isinstance(extra, dict) else 'unknown'};")
        return "{\n" + "\n".join(fields) + "\n}"
    return {"string": "string", "integer": "number", "number": "number", "boolean": "boolean", "null": "null"}.get(kind, "unknown")


def render_types(contract):
    header = "// Generated from contracts/openapi.yaml by contracts/generate_types.py; do not edit manually.\n"
    header += "// Wire types only: semantic validation and HTTP calls remain to be implemented.\n\n"
    return header + "\n\n".join(
        f"export type {name} = {ts_type(schema)};"
        for name, schema in contract["components"]["schemas"].items()
    ) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if frontend types have drifted")
    args = parser.parse_args()
    contract = yaml.safe_load((ROOT / "contracts/openapi.yaml").read_text(encoding="utf-8"))
    expected = render_types(contract)
    target = ROOT / "apps/web/src/api/types.ts"
    if args.check:
        if target.read_text(encoding="utf-8") != expected:
            raise SystemExit("Frontend types differ: run python contracts/generate_types.py from huice/.")
        print("Frontend types match OpenAPI.")
    else:
        target.write_text(expected, encoding="utf-8")
        print("Generated apps/web/src/api/types.ts.")


if __name__ == "__main__":
    main()
