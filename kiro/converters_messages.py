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
Shared message normalization and cleanup helpers for converters.
"""

from typing import List, Tuple

from loguru import logger

from kiro.converters_content import extract_text_content
from kiro.converters_tools import tool_calls_to_text, tool_results_to_text


def strip_all_tool_content(messages: List[object]) -> Tuple[List[object], bool]:
    """
    Strips all tool-related content from messages, converting it to text representation.
    """
    from kiro.converters_core import UnifiedMessage

    if not messages:
        return [], False

    result = []
    total_tool_calls_stripped = 0
    total_tool_results_stripped = 0

    for msg in messages:
        has_tool_calls = bool(msg.tool_calls)
        has_tool_results = bool(msg.tool_results)

        if has_tool_calls or has_tool_results:
            if has_tool_calls:
                total_tool_calls_stripped += len(msg.tool_calls)
            if has_tool_results:
                total_tool_results_stripped += len(msg.tool_results)

            existing_content = extract_text_content(msg.content)
            content_parts = []

            if existing_content:
                content_parts.append(existing_content)

            if has_tool_calls:
                tool_text = tool_calls_to_text(msg.tool_calls)
                if tool_text:
                    content_parts.append(tool_text)

            if has_tool_results:
                result_text = tool_results_to_text(msg.tool_results)
                if result_text:
                    content_parts.append(result_text)

            content = "\n\n".join(content_parts) if content_parts else "(empty)"

            cleaned_msg = UnifiedMessage(
                role=msg.role,
                content=content,
                tool_calls=None,
                tool_results=None,
                images=msg.images
            )
            result.append(cleaned_msg)
        else:
            result.append(msg)

    had_tool_content = total_tool_calls_stripped > 0 or total_tool_results_stripped > 0

    if had_tool_content:
        logger.debug(
            f"Converted tool content to text (no tools defined): "
            f"{total_tool_calls_stripped} tool_calls, {total_tool_results_stripped} tool_results"
        )

    return result, had_tool_content


def ensure_assistant_before_tool_results(messages: List[object]) -> Tuple[List[object], bool]:
    """
    Ensures that messages with tool_results have a preceding assistant message with tool_calls.
    """
    from kiro.converters_core import UnifiedMessage

    if not messages:
        return [], False

    result = []
    converted_any_tool_results = False

    for msg in messages:
        if msg.tool_results:
            has_preceding_assistant = (
                result and
                result[-1].role == "assistant" and
                result[-1].tool_calls
            )

            if not has_preceding_assistant:
                logger.debug(
                    f"Converting {len(msg.tool_results)} orphaned tool_results to text "
                    f"(no preceding assistant message with tool_calls). "
                    f"Tool IDs: {[tr.get('tool_use_id', 'unknown') for tr in msg.tool_results]}"
                )

                tool_results_text = tool_results_to_text(msg.tool_results)
                original_content = extract_text_content(msg.content) or ""
                if original_content and tool_results_text:
                    new_content = f"{original_content}\n\n{tool_results_text}"
                elif tool_results_text:
                    new_content = tool_results_text
                else:
                    new_content = original_content

                cleaned_msg = UnifiedMessage(
                    role=msg.role,
                    content=new_content,
                    tool_calls=msg.tool_calls,
                    tool_results=None,
                    images=msg.images
                )
                result.append(cleaned_msg)
                converted_any_tool_results = True
                continue

        result.append(msg)

    return result, converted_any_tool_results


def merge_adjacent_messages(messages: List[object]) -> List[object]:
    """
    Merges adjacent messages with the same role.
    """
    if not messages:
        return []

    merged = []
    merge_counts = {"user": 0, "assistant": 0}
    total_tool_calls_merged = 0
    total_tool_results_merged = 0

    for msg in messages:
        if not merged:
            merged.append(msg)
            continue

        last = merged[-1]
        if msg.role == last.role:
            if isinstance(last.content, list) and isinstance(msg.content, list):
                last.content = last.content + msg.content
            elif isinstance(last.content, list):
                last.content = last.content + [{"type": "text", "text": extract_text_content(msg.content)}]
            elif isinstance(msg.content, list):
                last.content = [{"type": "text", "text": extract_text_content(last.content)}] + msg.content
            else:
                last_text = extract_text_content(last.content)
                current_text = extract_text_content(msg.content)
                last.content = f"{last_text}\n{current_text}"

            if msg.role == "assistant" and msg.tool_calls:
                if last.tool_calls is None:
                    last.tool_calls = []
                last.tool_calls = list(last.tool_calls) + list(msg.tool_calls)
                total_tool_calls_merged += len(msg.tool_calls)

            if msg.role == "user" and msg.tool_results:
                if last.tool_results is None:
                    last.tool_results = []
                last.tool_results = list(last.tool_results) + list(msg.tool_results)
                total_tool_results_merged += len(msg.tool_results)

            if msg.role in merge_counts:
                merge_counts[msg.role] += 1
        else:
            merged.append(msg)

    total_merges = sum(merge_counts.values())
    if total_merges > 0:
        parts = []
        for role, count in merge_counts.items():
            if count > 0:
                parts.append(f"{count} {role}")
        merge_summary = ", ".join(parts)

        extras = []
        if total_tool_calls_merged > 0:
            extras.append(f"{total_tool_calls_merged} tool_calls")
        if total_tool_results_merged > 0:
            extras.append(f"{total_tool_results_merged} tool_results")

        if extras:
            logger.debug(f"Merged {total_merges} adjacent messages ({merge_summary}), including {', '.join(extras)}")
        else:
            logger.debug(f"Merged {total_merges} adjacent messages ({merge_summary})")

    return merged


def ensure_first_message_is_user(messages: List[object]) -> List[object]:
    """
    Ensures that the first message in the conversation is from user role.
    """
    from kiro.converters_core import UnifiedMessage

    if not messages:
        return messages

    if messages[0].role != "user":
        logger.debug(
            f"First message is '{messages[0].role}', prepending synthetic user message "
            f"(Kiro API requires conversations to start with user)"
        )

        synthetic_user = UnifiedMessage(
            role="user",
            content="(empty)"
        )

        return [synthetic_user] + messages

    return messages


def normalize_message_roles(messages: List[object]) -> List[object]:
    """
    Normalizes unknown message roles to 'user'.
    """
    from kiro.converters_core import UnifiedMessage

    if not messages:
        return messages

    normalized = []
    converted_count = 0

    for msg in messages:
        if msg.role not in ("user", "assistant"):
            logger.debug(f"Normalizing role '{msg.role}' to 'user'")
            normalized_msg = UnifiedMessage(
                role="user",
                content=msg.content,
                tool_calls=msg.tool_calls,
                tool_results=msg.tool_results,
                images=msg.images
            )
            normalized.append(normalized_msg)
            converted_count += 1
        else:
            normalized.append(msg)

    if converted_count > 0:
        logger.debug(f"Normalized {converted_count} message(s) with unknown roles to 'user'")

    return normalized


def ensure_alternating_roles(messages: List[object]) -> List[object]:
    """
    Ensures alternating user/assistant roles by inserting synthetic assistant messages.
    """
    from kiro.converters_core import UnifiedMessage

    if not messages or len(messages) < 2:
        return messages

    result = [messages[0]]
    synthetic_count = 0

    for msg in messages[1:]:
        prev_role = result[-1].role

        if msg.role == "user" and prev_role == "user":
            synthetic_assistant = UnifiedMessage(
                role="assistant",
                content="(empty)"
            )
            result.append(synthetic_assistant)
            synthetic_count += 1

        result.append(msg)

    if synthetic_count > 0:
        logger.debug(f"Inserted {synthetic_count} synthetic assistant message(s) to ensure alternation")

    return result
