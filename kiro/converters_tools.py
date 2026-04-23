# -*- coding: utf-8 -*-

# Kiro Gateway
# https://github.com/jwadow/kiro-gateway
# Copyright (C) 2025 Jwadow
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""
Shared tool, image, and tool-result conversion helpers for converters.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from kiro.config import TOOL_DESCRIPTION_MAX_LENGTH


def _get_tool_description_max_length() -> int:
    """
    获取当前工具描述长度限制，兼容测试对 converters_core 的 patch。
    """
    try:
        import kiro.converters_core as core_module
        return getattr(core_module, "TOOL_DESCRIPTION_MAX_LENGTH", TOOL_DESCRIPTION_MAX_LENGTH)
    except Exception:
        return TOOL_DESCRIPTION_MAX_LENGTH


def sanitize_json_schema(schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Sanitizes JSON Schema from fields that Kiro API doesn't accept.

    Args:
        schema: JSON Schema to sanitize

    Returns:
        Sanitized copy of schema
    """
    if not schema:
        return {}

    result = {}

    for key, value in schema.items():
        if key == "required" and isinstance(value, list) and len(value) == 0:
            continue

        if key == "additionalProperties":
            continue

        if key == "properties" and isinstance(value, dict):
            result[key] = {
                prop_name: sanitize_json_schema(prop_value) if isinstance(prop_value, dict) else prop_value
                for prop_name, prop_value in value.items()
            }
        elif isinstance(value, dict):
            result[key] = sanitize_json_schema(value)
        elif isinstance(value, list):
            result[key] = [
                sanitize_json_schema(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value

    return result


def process_tools_with_long_descriptions(
    tools: Optional[List[Any]]
) -> Tuple[Optional[List[Any]], str]:
    """
    Processes tools with long descriptions.

    Args:
        tools: List of tools in unified format

    Returns:
        Processed tools and tool documentation for system prompt
    """
    if not tools:
        return None, ""

    limit = _get_tool_description_max_length()

    if limit <= 0:
        return tools, ""

    # Delayed import to avoid circular dependency with converters_core dataclasses.
    from kiro.converters_core import UnifiedTool

    tool_documentation_parts = []
    processed_tools = []

    for tool in tools:
        description = tool.description or ""

        if len(description) <= limit:
            processed_tools.append(tool)
        else:
            logger.debug(
                f"Tool '{tool.name}' has long description ({len(description)} chars > {limit}), "
                f"moving to system prompt"
            )

            tool_documentation_parts.append(f"## Tool: {tool.name}\n\n{description}")

            reference_description = f"[Full documentation in system prompt under '## Tool: {tool.name}']"

            processed_tool = UnifiedTool(
                name=tool.name,
                description=reference_description,
                input_schema=tool.input_schema
            )
            processed_tools.append(processed_tool)

    tool_documentation = ""
    if tool_documentation_parts:
        tool_documentation = (
            "\n\n---\n"
            "# Tool Documentation\n"
            "The following tools have detailed documentation that couldn't fit in the tool definition.\n\n"
            + "\n\n---\n\n".join(tool_documentation_parts)
        )

    return processed_tools if processed_tools else None, tool_documentation


def validate_tool_names(tools: Optional[List[Any]]) -> None:
    """
    Validates tool names against Kiro API 64-character limit.
    """
    if not tools:
        return

    problematic_tools = []
    for tool in tools:
        if len(tool.name) > 64:
            problematic_tools.append((tool.name, len(tool.name)))

    if problematic_tools:
        tool_list = "\n".join([
            f"  - '{name}' ({length} characters)"
            for name, length in problematic_tools
        ])

        raise ValueError(
            f"Tool name(s) exceed Kiro API limit of 64 characters:\n"
            f"{tool_list}\n\n"
            f"Solution: Use shorter tool names (max 64 characters).\n"
            f"Example: 'get_user_data' instead of 'get_authenticated_user_profile_data_with_extended_information_about_it'"
        )


def convert_tools_to_kiro_format(tools: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """
    Converts unified tools to Kiro API format.
    """
    if not tools:
        return []

    kiro_tools = []
    for tool in tools:
        sanitized_params = sanitize_json_schema(tool.input_schema)

        description = tool.description
        if not description or not description.strip():
            description = f"Tool: {tool.name}"
            logger.debug(f"Tool '{tool.name}' has empty description, using placeholder")

        kiro_tools.append({
            "toolSpecification": {
                "name": tool.name,
                "description": description,
                "inputSchema": {"json": sanitized_params}
            }
        })

    return kiro_tools


def convert_images_to_kiro_format(images: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """
    Converts unified images to Kiro API format.
    """
    if not images:
        return []

    kiro_images = []
    for img in images:
        media_type = img.get("media_type", "image/jpeg")
        data = img.get("data", "")

        if not data:
            logger.warning("Skipping image with empty data")
            continue

        if data.startswith("data:"):
            try:
                header, actual_data = data.split(",", 1)
                media_part = header.split(";")[0]
                extracted_media_type = media_part.replace("data:", "")
                if extracted_media_type:
                    media_type = extracted_media_type
                data = actual_data
                logger.debug(f"Stripped data URL prefix, extracted media_type: {media_type}")
            except (ValueError, IndexError) as e:
                logger.warning(f"Failed to parse data URL prefix: {e}")

        format_str = media_type.split("/")[-1] if "/" in media_type else media_type

        kiro_images.append({
            "format": format_str,
            "source": {
                "bytes": data
            }
        })

    if kiro_images:
        logger.debug(f"Converted {len(kiro_images)} image(s) to Kiro format")

    return kiro_images


def convert_tool_results_to_kiro_format(tool_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Converts unified tool results to Kiro API format.
    """
    from kiro.converters_content import extract_text_content

    kiro_results = []
    for tr in tool_results:
        content = tr.get("content", "")
        if isinstance(content, str):
            content_text = content
        else:
            content_text = extract_text_content(content)

        if not content_text:
            content_text = "(empty result)"

        kiro_results.append({
            "content": [{"text": content_text}],
            "status": "success",
            "toolUseId": tr.get("tool_use_id", "")
        })

    return kiro_results


def extract_tool_results_from_content(content: Any) -> List[Dict[str, Any]]:
    """
    Extracts tool results from message content.
    """
    from kiro.converters_content import extract_text_content

    tool_results = []

    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                tool_results.append({
                    "content": [{"text": extract_text_content(item.get("content", "")) or "(empty result)"}],
                    "status": "success",
                    "toolUseId": item.get("tool_use_id", "")
                })

    return tool_results


def extract_tool_uses_from_message(
    content: Any,
    tool_calls: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """
    Extracts tool uses from assistant message.
    """
    tool_uses = []

    if tool_calls:
        for tc in tool_calls:
            if isinstance(tc, dict):
                func = tc.get("function", {})
                arguments = func.get("arguments", "{}")
                if isinstance(arguments, str):
                    input_data = json.loads(arguments) if arguments else {}
                else:
                    input_data = arguments if arguments else {}
                tool_uses.append({
                    "name": func.get("name", ""),
                    "input": input_data,
                    "toolUseId": tc.get("id", "")
                })

    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                tool_uses.append({
                    "name": item.get("name", ""),
                    "input": item.get("input", {}),
                    "toolUseId": item.get("id", "")
                })

    return tool_uses


def tool_calls_to_text(tool_calls: List[Dict[str, Any]]) -> str:
    """
    Converts tool_calls to human-readable text representation.
    """
    if not tool_calls:
        return ""

    parts = []
    for tc in tool_calls:
        func = tc.get("function", {})
        name = func.get("name", "unknown")
        arguments = func.get("arguments", "{}")
        tool_id = tc.get("id", "")

        if tool_id:
            parts.append(f"[Tool: {name} ({tool_id})]\n{arguments}")
        else:
            parts.append(f"[Tool: {name}]\n{arguments}")

    return "\n\n".join(parts)


def tool_results_to_text(tool_results: List[Dict[str, Any]]) -> str:
    """
    Converts tool_results to human-readable text representation.
    """
    from kiro.converters_content import extract_text_content

    if not tool_results:
        return ""

    parts = []
    for tr in tool_results:
        content = tr.get("content", "")
        tool_use_id = tr.get("tool_use_id", "")

        if isinstance(content, str):
            content_text = content
        else:
            content_text = extract_text_content(content)

        if not content_text:
            content_text = "(empty result)"

        if tool_use_id:
            parts.append(f"[Tool Result ({tool_use_id})]\n{content_text}")
        else:
            parts.append(f"[Tool Result]\n{content_text}")

    return "\n\n".join(parts)
