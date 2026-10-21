"""适配 Cade 的结构化结果校验，保留 Mini 的文本工具契约。"""

from __future__ import annotations

import json
from typing import Any

import jsonschema
from jsonschema.validators import validator_for
from referencing import Registry
from referencing.exceptions import Unresolvable

from cade.harness.tools import ToolResult


def convert_mcp_tool_result(
    response: dict[str, Any], output_schema: dict[str, Any] | None
) -> ToolResult:
    """保留非文本内容，并把协议校验失败作为工具错误返回。"""
    parts: list[str] = []
    errors: list[str] = []
    content = response.get("content", [])
    if not isinstance(content, list):
        errors.append("MCP content must be a list")
        content = [content]
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
                continue
            errors.append("MCP text content requires a string")
        # Mini 的提供方契约只接受文本，完整序列化其他内容而非丢弃。
        parts.append(json.dumps(block, ensure_ascii=False, sort_keys=True))
    structured = response.get("structuredContent")
    is_error = response.get("isError", False)
    if not isinstance(is_error, bool):
        errors.append("MCP isError must be a boolean")
        is_error = True
    status = "not_declared"
    if structured is not None:
        if not isinstance(structured, dict):
            errors.append("MCP structuredContent must be an object")
        parts.append(json.dumps(structured, ensure_ascii=False, sort_keys=True))
    if output_schema is not None and not is_error:
        status = "valid"
        if structured is None:
            errors.append("MCP outputSchema requires structuredContent")
        else:
            try:
                validator = validator_for(output_schema)
                validator.check_schema(output_schema)
                validator(output_schema, registry=Registry()).validate(structured)
            except (
                jsonschema.SchemaError,
                jsonschema.ValidationError,
                Unresolvable,
            ) as exc:
                errors.append(f"MCP output validation failed: {exc}")
        if errors:
            status = "invalid"
    parts.extend(errors)
    return ToolResult(
        content="\n".join(parts) or json.dumps(response, ensure_ascii=False),
        is_error=is_error or bool(errors),
        metadata={"mcp_result": response, "validation": status},
    )
