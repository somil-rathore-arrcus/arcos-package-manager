#!/usr/bin/env python3
"""Generate frontend/src/types/api.ts from the API's own OpenAPI schema.

Hand-written types drift from the API the moment a field is added. Generating
them from the schema the backend actually serves means a mismatch becomes a
TypeScript error rather than an undefined at runtime.

    ./.venv/bin/python scripts/generate_frontend_types.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TARGET = ROOT / "frontend" / "src" / "types" / "api.ts"

HEADER = """// GENERATED from the FastAPI OpenAPI schema - do not edit by hand.
// Regenerate: ./.venv/bin/python scripts/generate_frontend_types.py

"""


def ts_type(schema: dict, schemas: dict) -> str:
    if not schema:
        return "unknown"

    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]

    for key in ("anyOf", "oneOf"):
        if key in schema:
            parts = [ts_type(s, schemas) for s in schema[key]]
            # Pydantic renders Optional[T] as anyOf[T, null].
            parts = [p for p in dict.fromkeys(parts)]
            return " | ".join(parts)

    if "allOf" in schema:
        return ts_type(schema["allOf"][0], schemas)

    if "enum" in schema:
        return " | ".join(f'"{v}"' for v in schema["enum"])

    kind = schema.get("type")
    if kind == "array":
        return f"{ts_type(schema.get('items', {}), schemas)}[]"
    if kind == "object":
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            return f"Record<string, {ts_type(extra, schemas)}>"
        return "Record<string, unknown>"
    return {
        "string": "string", "integer": "number", "number": "number",
        "boolean": "boolean", "null": "null",
    }.get(kind, "unknown")


def render(name: str, schema: dict, schemas: dict) -> str:
    if "enum" in schema and schema.get("type") == "string":
        values = " | ".join(f'"{v}"' for v in schema["enum"])
        return f"export type {name} = {values}\n"

    required = set(schema.get("required", []))
    # FastAPI serialises every field of a response model, so a field with a
    # default is always present in the payload even though the schema marks it
    # optional. Typing it optional would force a null check on every consumer
    # for something that cannot be absent - and `default_factory` fields do not
    # even carry a `default` in the schema, so presence cannot be read off that.
    # Request bodies are the opposite case: a default is precisely what the
    # client may omit.
    is_request = name.endswith("Request")
    lines = [f"export interface {name} {{"]
    for field, spec in (schema.get("properties") or {}).items():
        rendered = ts_type(spec, schemas)
        always_present = field in required or not is_request
        optional = "" if always_present else "?"
        description = (spec.get("description") or "").replace("\n", " ").strip()
        if description:
            lines.append(f"  /** {description} */")
        lines.append(f"  {field}{optional}: {rendered}")
    lines.append("}\n")
    return "\n".join(lines)


def main() -> int:
    from apm.api.main import create_app

    schema = create_app().openapi()
    schemas = schema.get("components", {}).get("schemas", {})

    blocks = [
        render(name, spec, schemas)
        for name, spec in sorted(schemas.items())
        if not name.startswith("ValidationError")
        and name != "HTTPValidationError"
    ]

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(HEADER + "\n".join(blocks), encoding="utf-8")
    print(f"wrote {TARGET} ({len(blocks)} types)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
