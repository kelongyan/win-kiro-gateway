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
Shared streaming post-processing helpers.
"""

from typing import Any, Dict, List

from loguru import logger

from kiro.config import TRUNCATION_RECOVERY
from kiro.parsers import parse_bracket_tool_calls, deduplicate_tool_calls
from kiro.truncation_recovery import should_inject_recovery
from kiro.truncation_state import save_tool_truncation, save_content_truncation


def build_deduplicated_tool_calls(
    full_content: str,
    tool_calls_from_stream: List[Dict[str, Any]],
    parse_tool_calls=parse_bracket_tool_calls,
    dedupe_tool_calls=deduplicate_tool_calls,
) -> List[Dict[str, Any]]:
    """
    从流式 tool call 和 bracket-style tool call 中构建去重后的统一列表。
    """
    bracket_tool_calls = parse_tool_calls(full_content)
    all_tool_calls = tool_calls_from_stream + bracket_tool_calls
    return dedupe_tool_calls(all_tool_calls)


def detect_content_truncation(stream_completed_normally: bool, full_content: str, has_tool_output: bool) -> bool:
    """
    判断纯文本内容是否发生截断。

    只有在没有 tool 输出的情况下，才把“缺少完成信号”视为正文截断，
    否则容易和 tool call 流结束混淆。
    """
    return (
        not stream_completed_normally and
        len(full_content) > 0 and
        not has_tool_output
    )


def log_content_truncation(content_was_truncated: bool, full_content: str) -> None:
    """
    统一记录正文截断日志。
    """
    if not content_was_truncated:
        return

    logger.error(
        f"Content truncated by Kiro API: stream ended without completion signals, "
        f"length={len(full_content)} chars. "
        f"{'Model will be notified automatically about truncation.' if TRUNCATION_RECOVERY else 'Set TRUNCATION_RECOVERY=true in .env to auto-notify model about truncation.'}"
    )


def save_openai_truncation_state(all_tool_calls: List[Dict[str, Any]], content_was_truncated: bool, full_content: str) -> None:
    """
    保存 OpenAI 流程中的截断信息。
    """
    if not should_inject_recovery():
        return

    truncated_count = 0
    for tc in all_tool_calls:
        if tc.get("_truncation_detected"):
            save_tool_truncation(
                tool_call_id=tc["id"],
                tool_name=tc["function"]["name"],
                truncation_info=tc["_truncation_info"]
            )
            truncated_count += 1

    if content_was_truncated:
        save_content_truncation(full_content)

    if truncated_count > 0 or content_was_truncated:
        logger.info(
            f"Truncation detected: {truncated_count} tool(s), "
            f"content={content_was_truncated}. Will be handled when client sends next request."
        )


def save_anthropic_truncation_state(truncated_tools: List[Dict[str, Any]], content_was_truncated: bool, full_content: str) -> None:
    """
    保存 Anthropic 流程中的截断信息。
    """
    if not should_inject_recovery():
        return

    if truncated_tools:
        for truncated_tool in truncated_tools:
            save_tool_truncation(
                tool_call_id=truncated_tool["id"],
                tool_name=truncated_tool["name"],
                truncation_info=truncated_tool["truncation_info"]
            )

    if content_was_truncated:
        save_content_truncation(full_content)

    if truncated_tools or content_was_truncated:
        logger.info(
            f"Truncation detected: {len(truncated_tools)} tool(s), "
            f"content={content_was_truncated}. Will be handled when client sends next request."
        )
