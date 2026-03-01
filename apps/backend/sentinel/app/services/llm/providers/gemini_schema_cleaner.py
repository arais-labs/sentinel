"""Clean JSON Schema for Gemini API compatibility.

Gemini rejects many standard JSON Schema keywords.  This module recursively
transforms tool parameter schemas to remove unsupported keywords, resolve
$ref pointers, flatten anyOf/oneOf unions, and convert const to enum.

Ported from openclaw/src/src/agents/schema/clean-for-gemini.ts.
"""

from __future__ import annotations

from typing import Any

_UNSUPPORTED_KEYWORDS: set[str] = {
    "patternProperties",
    "additionalProperties",
    "$schema",
    "$id",
    "$ref",
    "$defs",
    "definitions",
    "examples",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "multipleOf",
    "pattern",
    "format",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
    # Extra keywords from plan's extended list
    "exclusiveMinimum",
    "exclusiveMaximum",
    "contentMediaType",
    "contentEncoding",
    "if",
    "then",
    "else",
    "not",
    "prefixItems",
    "unevaluatedProperties",
    "unevaluatedItems",
    "dependentSchemas",
    "dependentRequired",
    "$comment",
    "default",
    "readOnly",
    "writeOnly",
    "deprecated",
    "$anchor",
    "$dynamicRef",
    "$dynamicAnchor",
    "$vocabulary",
}

_SCHEMA_META_KEYS = ("description", "title", "default")


def clean_schema_for_gemini(schema: Any) -> Any:
    """Public entry point — clean a JSON Schema for Gemini compatibility."""
    if not isinstance(schema, dict):
        return schema
    defs = _extend_defs(None, schema)
    return _clean(schema, defs, None)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

Defs = dict[str, Any] | None


def _copy_meta(src: dict, dst: dict) -> None:
    for key in _SCHEMA_META_KEYS:
        if key in src and src[key] is not None:
            dst[key] = src[key]


def _extend_defs(defs: Defs, schema: dict) -> Defs:
    raw_defs = schema.get("$defs") if isinstance(schema.get("$defs"), dict) else None
    legacy = schema.get("definitions") if isinstance(schema.get("definitions"), dict) else None
    if not raw_defs and not legacy:
        return defs
    merged = dict(defs) if defs else {}
    if raw_defs:
        merged.update(raw_defs)
    if legacy:
        merged.update(legacy)
    return merged


def _decode_pointer_segment(segment: str) -> str:
    return segment.replace("~1", "/").replace("~0", "~")


def _try_resolve_ref(ref: str, defs: Defs) -> Any | None:
    if not defs:
        return None
    import re
    m = re.match(r"^#/(?:\$defs|definitions)/(.+)$", ref)
    if not m:
        return None
    name = _decode_pointer_segment(m.group(1))
    return defs.get(name)


def _is_null_schema(variant: Any) -> bool:
    if not isinstance(variant, dict):
        return False
    if "const" in variant and variant["const"] is None:
        return True
    enum_val = variant.get("enum")
    if isinstance(enum_val, list) and len(enum_val) == 1 and enum_val[0] is None:
        return True
    t = variant.get("type")
    if t == "null":
        return True
    if isinstance(t, list) and len(t) == 1 and t[0] == "null":
        return True
    return False


def _strip_null_variants(variants: list) -> tuple[list, bool]:
    non_null = [v for v in variants if not _is_null_schema(v)]
    return non_null, len(non_null) != len(variants)


def _try_flatten_literal_anyof(variants: list) -> dict | None:
    if not variants:
        return None
    all_values: list[Any] = []
    common_type: str | None = None
    for v in variants:
        if not isinstance(v, dict):
            return None
        if "const" in v:
            literal_value = v["const"]
        elif isinstance(v.get("enum"), list) and len(v["enum"]) == 1:
            literal_value = v["enum"][0]
        else:
            return None
        vtype = v.get("type")
        if not isinstance(vtype, str):
            return None
        if common_type is None:
            common_type = vtype
        elif common_type != vtype:
            return None
        all_values.append(literal_value)
    if common_type and all_values:
        return {"type": common_type, "enum": all_values}
    return None


def _simplify_union(obj: dict, variants: list) -> tuple[list, Any | None]:
    """Try to simplify a union (anyOf/oneOf). Returns (variants, simplified_or_None)."""
    non_null, stripped = _strip_null_variants(variants)
    flattened = _try_flatten_literal_anyof(non_null)
    if flattened:
        result: dict[str, Any] = {"type": flattened["type"], "enum": flattened["enum"]}
        _copy_meta(obj, result)
        return non_null, result
    if stripped and len(non_null) == 1:
        lone = non_null[0]
        if isinstance(lone, dict):
            result = dict(lone)
            _copy_meta(obj, result)
            return non_null, result
        return non_null, lone
    return (non_null if stripped else variants), None


def _flatten_union_fallback(obj: dict, variants: list) -> dict | None:
    objects = [v for v in variants if isinstance(v, dict)]
    if not objects:
        return None
    if len(objects) == 1:
        merged: dict[str, Any] = dict(objects[0])
        _copy_meta(obj, merged)
        return merged
    types = {v.get("type") for v in objects if v.get("type")}
    if len(types) == 1:
        merged = {"type": next(iter(types))}
        _copy_meta(obj, merged)
        return merged
    first = objects[0]
    if first.get("type"):
        merged = {"type": first["type"]}
        _copy_meta(obj, merged)
        return merged
    merged = {}
    _copy_meta(obj, merged)
    return merged


def _clean(schema: Any, defs: Defs, ref_stack: set[str] | None) -> Any:
    if not isinstance(schema, dict):
        return schema
    if isinstance(schema, list):
        return [_clean(item, defs, ref_stack) for item in schema]

    obj: dict[str, Any] = schema
    next_defs = _extend_defs(defs, obj)

    # --- $ref resolution ---
    ref_value = obj.get("$ref")
    if isinstance(ref_value, str):
        if ref_stack and ref_value in ref_stack:
            return {}
        resolved = _try_resolve_ref(ref_value, next_defs)
        if resolved is not None:
            next_stack = set(ref_stack) if ref_stack else set()
            next_stack.add(ref_value)
            cleaned = _clean(resolved, next_defs, next_stack)
            if not isinstance(cleaned, dict):
                return cleaned
            result: dict[str, Any] = dict(cleaned)
            _copy_meta(obj, result)
            return result
        result = {}
        _copy_meta(obj, result)
        return result

    # --- anyOf / oneOf pre-processing ---
    has_any_of = "anyOf" in obj and isinstance(obj.get("anyOf"), list)
    has_one_of = "oneOf" in obj and isinstance(obj.get("oneOf"), list)

    cleaned_any_of: list | None = (
        [_clean(v, next_defs, ref_stack) for v in obj["anyOf"]] if has_any_of else None
    )
    cleaned_one_of: list | None = (
        [_clean(v, next_defs, ref_stack) for v in obj["oneOf"]] if has_one_of else None
    )

    if has_any_of and cleaned_any_of is not None:
        cleaned_any_of, simplified = _simplify_union(obj, cleaned_any_of)
        if simplified is not None:
            return simplified

    if has_one_of and cleaned_one_of is not None:
        cleaned_one_of, simplified = _simplify_union(obj, cleaned_one_of)
        if simplified is not None:
            return simplified

    # --- Main key iteration ---
    cleaned: dict[str, Any] = {}
    for key, value in obj.items():
        if key in _UNSUPPORTED_KEYWORDS:
            continue

        if key == "const":
            cleaned["enum"] = [value]
            continue

        # Skip type when a union is present
        if key == "type" and (has_any_of or has_one_of):
            continue

        # Normalize array-style type (e.g. ["string", "null"] → "string")
        if key == "type" and isinstance(value, list) and all(isinstance(v, str) for v in value):
            types = [v for v in value if v != "null"]
            cleaned["type"] = types[0] if len(types) == 1 else types
            continue

        if key == "properties" and isinstance(value, dict):
            cleaned[key] = {
                k: _clean(v, next_defs, ref_stack) for k, v in value.items()
            }
        elif key == "items" and value is not None:
            if isinstance(value, list):
                cleaned[key] = [_clean(entry, next_defs, ref_stack) for entry in value]
            elif isinstance(value, dict):
                cleaned[key] = _clean(value, next_defs, ref_stack)
            else:
                cleaned[key] = value
        elif key == "anyOf" and isinstance(value, list):
            cleaned[key] = cleaned_any_of if cleaned_any_of is not None else [
                _clean(v, next_defs, ref_stack) for v in value
            ]
        elif key == "oneOf" and isinstance(value, list):
            cleaned[key] = cleaned_one_of if cleaned_one_of is not None else [
                _clean(v, next_defs, ref_stack) for v in value
            ]
        elif key == "allOf" and isinstance(value, list):
            cleaned[key] = [_clean(v, next_defs, ref_stack) for v in value]
        else:
            cleaned[key] = value

    # Last-resort fallback: flatten remaining anyOf/oneOf
    if isinstance(cleaned.get("anyOf"), list):
        fallback = _flatten_union_fallback(cleaned, cleaned["anyOf"])
        if fallback is not None:
            return fallback
    if isinstance(cleaned.get("oneOf"), list):
        fallback = _flatten_union_fallback(cleaned, cleaned["oneOf"])
        if fallback is not None:
            return fallback

    return cleaned
