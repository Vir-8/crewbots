"""TypeScript types for the app, written straight from the API's OpenAPI schema, so the two can't drift.

It handles the JSON Schema that Pydantic emits for this API's models (objects, enums, literals, arrays, maps,
nullable and tagged unions) and fails loudly on anything else rather than guessing.
"""

from typing import Any

HEADER = "// Generated from the crewbots API schema by `crewbots openapi --ts`. Do not edit; regenerate.\n"


def from_openapi(schema: dict[str, Any]) -> str:
    components = schema["components"]["schemas"]
    out = [HEADER]
    for name in sorted(components):
        out.append(_declaration(name, components[name]))
    return "\n".join(out)


def _declaration(name: str, spec: dict[str, Any]) -> str:
    doc = f"/** {spec['description'].strip()} */\n" if spec.get("description") else ""
    if spec.get("type") == "object" and "properties" in spec:
        required = set(spec.get("required", []))
        fields = []
        for field, field_spec in spec["properties"].items():
            optional = "" if field in required else "?"
            fields.append(f"  {field}{optional}: {_type(field_spec)};")
        return f"{doc}export interface {name} {{\n" + "\n".join(fields) + "\n}\n"
    return f"{doc}export type {name} = {_type(spec)};\n"


def _type(spec: dict[str, Any]) -> str:
    if "$ref" in spec:
        return spec["$ref"].rsplit("/", 1)[-1]
    if "const" in spec:
        return _literal(spec["const"])
    if "enum" in spec:
        return " | ".join(_literal(v) for v in spec["enum"])
    for key in ("anyOf", "oneOf"):
        if key in spec:
            return " | ".join(dict.fromkeys(_type(s) for s in spec[key]))
    kind = spec.get("type")
    if kind == "array":
        item = _type(spec.get("items", {}))
        return f"{item}[]" if item.isidentifier() else f"({item})[]"
    if kind == "object":
        extra = spec.get("additionalProperties")
        value = _type(extra) if isinstance(extra, dict) and extra else "unknown"
        return f"Record<string, {value}>"
    simple = {"string": "string", "integer": "number", "number": "number", "boolean": "boolean", "null": "null"}
    if kind in simple:
        return simple[kind]
    if not set(spec) - {"title", "description", "default", "examples"}:
        return "unknown"  # a schema that says nothing about the type: any value
    raise ValueError(f"no TypeScript for schema {spec}")


def _literal(value: Any) -> str:
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if value is None:
        return "null"
    return str(value).lower() if isinstance(value, bool) else str(value)
