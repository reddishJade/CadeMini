"""Responses 请求编码与工具参数解码。

包含 JSON Schema strict 模式转换与工具参数解析。
"""

from __future__ import annotations

import copy
from typing import Any

import orjson

from cade.ai.types import ToolArguments

# ── JSON Schema strict 模式 ──

_UNSUPPORTED_STRICT_SCHEMA_KEYS = frozenset(
    {
        "default",
        "format",
        "maximum",
        "maxItems",
        "maxLength",
        "maxProperties",
        "minimum",
        "minItems",
        "minLength",
        "minProperties",
        "multipleOf",
        "pattern",
        "patternProperties",
        "propertyNames",
        "uniqueItems",
    }
)


def make_schema_strict(schema: dict[str, Any]) -> dict[str, Any]:
    """将 JSON Schema 转换为 OpenAI strict mode 兼容格式。"""
    s = copy.deepcopy(schema)

    def process(node: Any) -> Any:
        if not isinstance(node, dict):
            return node

        t = node.get("type")
        if t == "object" or "properties" in node:
            properties = node.get("properties", {})
            existing_required = node.get("required", [])
            required = (
                set(existing_required) if isinstance(existing_required, list) else set()
            )
            if isinstance(properties, dict) and properties:
                node["properties"] = {
                    key: _nullable_schema(value) if key not in required else value
                    for key, value in properties.items()
                }
                node["required"] = list(properties.keys())
                node["additionalProperties"] = False

        for key in _UNSUPPORTED_STRICT_SCHEMA_KEYS:
            node.pop(key, None)

        if "properties" in node:
            node["properties"] = {k: process(v) for k, v in node["properties"].items()}
        if "items" in node:
            node["items"] = process(node["items"])
        if "anyOf" in node:
            node["anyOf"] = [process(item) for item in node["anyOf"]]
        if "allOf" in node:
            node["allOf"] = [process(item) for item in node["allOf"]]
        if "oneOf" in node:
            node["oneOf"] = [process(item) for item in node["oneOf"]]

        return node

    return process(s)


def _nullable_schema(schema: Any) -> Any:
    """把原可选字段编码为 strict schema 可接受的 null union。"""
    if not isinstance(schema, dict):
        return schema
    result = copy.deepcopy(schema)
    schema_type = result.get("type")
    if isinstance(schema_type, list):
        if "null" not in schema_type:
            result["type"] = [*schema_type, "null"]
        return result
    if isinstance(schema_type, str):
        if schema_type != "null":
            result["type"] = [schema_type, "null"]
        return result
    if "anyOf" in result:
        any_of = result.get("anyOf")
        if isinstance(any_of, list):
            has_null = any(
                isinstance(item, dict) and item.get("type") == "null" for item in any_of
            )
            if not has_null:
                result["anyOf"] = [*any_of, {"type": "null"}]
    return result


# ── 工具参数解析 ──


def parse_tool_arguments(raw_arguments: str) -> ToolArguments:
    """解析工具调用参数；非法 JSON 转为可识别的错误占位。"""
    try:
        result = orjson.loads((raw_arguments or "{}").encode())
        parsed = _tool_arguments(result)
        if parsed is not None:
            return parsed
        return {"__invalid_tool_arguments__": str(result)}
    except orjson.JSONDecodeError:
        return {"__invalid_tool_arguments__": raw_arguments}


def _tool_arguments(value: object) -> ToolArguments | None:
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in value.items()}
