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
Core converters for transforming API formats to Kiro format.

This module contains shared logic used by both OpenAI and Anthropic converters:
- Text content extraction from various formats
- Message merging and processing
- Kiro payload building
- Tool processing and sanitization

The core layer provides a unified interface that API-specific adapters use
to convert their formats to Kiro API format.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from kiro.config import (
    TOOL_DESCRIPTION_MAX_LENGTH,
    FAKE_REASONING_ENABLED,
    FAKE_REASONING_MAX_TOKENS,
)
from kiro.converters_content import (
    extract_text_content,
    extract_images_from_content,
    get_thinking_system_prompt_addition,
    get_truncation_recovery_system_addition,
    inject_thinking_tags,
)
from kiro.converters_tools import (
    sanitize_json_schema,
    process_tools_with_long_descriptions,
    validate_tool_names,
    convert_tools_to_kiro_format,
    convert_images_to_kiro_format,
    convert_tool_results_to_kiro_format,
    extract_tool_results_from_content,
    extract_tool_uses_from_message,
    tool_calls_to_text,
    tool_results_to_text,
)
from kiro.converters_messages import (
    strip_all_tool_content,
    ensure_assistant_before_tool_results,
    merge_adjacent_messages,
    ensure_first_message_is_user,
    normalize_message_roles,
    ensure_alternating_roles,
)


# ==================================================================================================
# Data Classes for Unified Message Format
# ==================================================================================================

@dataclass
class UnifiedMessage:
    """
    Unified message format used internally by converters.
    
    This format is API-agnostic and can be created from both OpenAI and Anthropic formats.
    Serves as the canonical representation for all message data before conversion to Kiro API.
    
    Attributes:
        role: Message role (user, assistant, system)
        content: Text content or list of content blocks
        tool_calls: List of tool calls (for assistant messages)
        tool_results: List of tool results (for user messages with tool responses)
        images: List of images in unified format (for multimodal user messages)
                Format: [{"media_type": "image/jpeg", "data": "base64..."}]
    """
    role: str
    content: Any = ""
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_results: Optional[List[Dict[str, Any]]] = None
    images: Optional[List[Dict[str, Any]]] = None


@dataclass
class UnifiedTool:
    """
    Unified tool format used internally by converters.
    
    Attributes:
        name: Tool name
        description: Tool description
        input_schema: JSON Schema for tool parameters
    """
    name: str
    description: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None


@dataclass
class KiroPayloadResult:
    """
    Result of building Kiro payload.
    
    Attributes:
        payload: The complete Kiro API payload
        tool_documentation: Documentation for tools with long descriptions (to add to system prompt)
    """
    payload: Dict[str, Any]
    tool_documentation: str = ""


# ==================================================================================================
# Kiro History Building
# ==================================================================================================

def build_kiro_history(messages: List[UnifiedMessage], model_id: str) -> List[Dict[str, Any]]:
    """
    Builds history array for Kiro API from unified messages.
    
    Kiro API expects alternating userInputMessage and assistantResponseMessage.
    This function converts unified format to Kiro format.
    
    All messages should have 'user' or 'assistant' roles at this point,
    as unknown roles are normalized earlier in the pipeline by normalize_message_roles().
    
    Args:
        messages: List of messages in unified format (with normalized roles)
        model_id: Internal Kiro model ID
    
    Returns:
        List of dictionaries for history field in Kiro API
    """
    history = []
    
    for msg in messages:
        if msg.role == "user":
            content = extract_text_content(msg.content)
            
            # Fallback for empty content - Kiro API requires non-empty content
            if not content:
                content = "(empty)"
            
            user_input = {
                "content": content,
                "modelId": model_id,
                "origin": "AI_EDITOR",
            }
            
            # Process images - extract from message or content
            # IMPORTANT: images go directly into userInputMessage, NOT into userInputMessageContext
            # This matches the native Kiro IDE format
            images = msg.images or extract_images_from_content(msg.content)
            if images:
                kiro_images = convert_images_to_kiro_format(images)
                if kiro_images:
                    user_input["images"] = kiro_images
            
            # Build userInputMessageContext for tools and toolResults only
            user_input_context: Dict[str, Any] = {}
            
            # Process tool_results - convert to Kiro format if present
            if msg.tool_results:
                kiro_tool_results = convert_tool_results_to_kiro_format(msg.tool_results)
                if kiro_tool_results:
                    user_input_context["toolResults"] = kiro_tool_results
            else:
                # Try to extract from content (already in Kiro format)
                tool_results = extract_tool_results_from_content(msg.content)
                if tool_results:
                    user_input_context["toolResults"] = tool_results
            
            # Add context if not empty (contains toolResults only, not images)
            if user_input_context:
                user_input["userInputMessageContext"] = user_input_context
            
            history.append({"userInputMessage": user_input})
            
        elif msg.role == "assistant":
            content = extract_text_content(msg.content)
            
            # Fallback for empty content - Kiro API requires non-empty content
            if not content:
                content = "(empty)"
            
            assistant_response = {"content": content}
            
            # Process tool_calls
            tool_uses = extract_tool_uses_from_message(msg.content, msg.tool_calls)
            if tool_uses:
                assistant_response["toolUses"] = tool_uses
            
            history.append({"assistantResponseMessage": assistant_response})
    
    return history


# ==================================================================================================
# Main Payload Building
# ==================================================================================================

def build_kiro_payload(
    messages: List[UnifiedMessage],
    system_prompt: str,
    model_id: str,
    tools: Optional[List[UnifiedTool]],
    conversation_id: str,
    profile_arn: str,
    inject_thinking: bool = True
) -> KiroPayloadResult:
    """
    Builds complete payload for Kiro API from unified data.
    
    This is the main function that assembles the Kiro API payload from
    API-agnostic unified message and tool formats.
    
    Args:
        messages: List of messages in unified format (without system messages)
        system_prompt: Already extracted system prompt
        model_id: Internal Kiro model ID
        tools: List of tools in unified format (or None)
        conversation_id: Unique conversation ID
        profile_arn: AWS CodeWhisperer profile ARN
        inject_thinking: Whether to inject thinking tags (default True)
    
    Returns:
        KiroPayloadResult with payload and tool documentation
    
    Raises:
        ValueError: If there are no messages to send
    """
    # Process tools with long descriptions
    processed_tools, tool_documentation = process_tools_with_long_descriptions(tools)
    
    # Validate tool names against Kiro API 64-character limit
    validate_tool_names(processed_tools)
    
    # Add tool documentation to system prompt if present
    full_system_prompt = system_prompt
    if tool_documentation:
        full_system_prompt = full_system_prompt + tool_documentation if full_system_prompt else tool_documentation.strip()
    
    # Add thinking mode legitimization to system prompt if enabled
    thinking_system_addition = get_thinking_system_prompt_addition()
    if thinking_system_addition:
        full_system_prompt = full_system_prompt + thinking_system_addition if full_system_prompt else thinking_system_addition.strip()
    
    # Add truncation recovery legitimization to system prompt if enabled
    truncation_system_addition = get_truncation_recovery_system_addition()
    if truncation_system_addition:
        full_system_prompt = full_system_prompt + truncation_system_addition if full_system_prompt else truncation_system_addition.strip()
    
    # If no tools are defined, strip ALL tool-related content from messages
    # Kiro API rejects requests with toolResults but no tools
    if not tools:
        messages_without_tools, had_tool_content = strip_all_tool_content(messages)
        messages_with_assistants = messages_without_tools
        converted_tool_results = had_tool_content
    else:
        # Ensure assistant messages exist before tool_results (Kiro API requirement)
        # Also returns flag if any tool_results were converted (to skip thinking tag injection)
        messages_with_assistants, converted_tool_results = ensure_assistant_before_tool_results(messages)
    
    # Merge adjacent messages with the same role
    merged_messages = merge_adjacent_messages(messages_with_assistants)
    
    # Ensure first message is from user (Kiro API requirement, fixes issue #60)
    merged_messages = ensure_first_message_is_user(merged_messages)
    
    # Normalize unknown roles to 'user' (fixes issue #64)
    # This must happen BEFORE ensure_alternating_roles() so that consecutive
    # messages with unknown roles (e.g., 'developer') are properly detected
    merged_messages = normalize_message_roles(merged_messages)
    
    # Ensure alternating user/assistant roles (fixes issue #64)
    # Insert synthetic assistant messages between consecutive user messages
    merged_messages = ensure_alternating_roles(merged_messages)
    
    if not merged_messages:
        raise ValueError("No messages to send")
    
    # Build history (all messages except the last one)
    history_messages = merged_messages[:-1] if len(merged_messages) > 1 else []
    
    # If there's a system prompt, add it to the first user message in history
    if full_system_prompt and history_messages:
        first_msg = history_messages[0]
        if first_msg.role == "user":
            original_content = extract_text_content(first_msg.content)
            first_msg.content = f"{full_system_prompt}\n\n{original_content}"
    
    history = build_kiro_history(history_messages, model_id)
    
    # Current message (the last one)
    current_message = merged_messages[-1]
    current_content = extract_text_content(current_message.content)
    
    # If system prompt exists but history is empty - add to current message
    if full_system_prompt and not history:
        current_content = f"{full_system_prompt}\n\n{current_content}"
    
    # If current message is assistant, need to add it to history
    # and create user message "Continue"
    if current_message.role == "assistant":
        history.append({
            "assistantResponseMessage": {
                "content": current_content
            }
        })
        current_content = "Continue"
    
    # If content is empty - use "Continue"
    if not current_content:
        current_content = "Continue"
    
    # Process images in current message - extract from message or content
    # IMPORTANT: images go directly into userInputMessage, NOT into userInputMessageContext
    # This matches the native Kiro IDE format
    images = current_message.images or extract_images_from_content(current_message.content)
    kiro_images = None
    if images:
        kiro_images = convert_images_to_kiro_format(images)
        if kiro_images:
            logger.debug(f"Added {len(kiro_images)} image(s) to current message")
    
    # Build user_input_context for tools and toolResults only (NOT images)
    user_input_context: Dict[str, Any] = {}
    
    # Add tools if present
    kiro_tools = convert_tools_to_kiro_format(processed_tools)
    if kiro_tools:
        user_input_context["tools"] = kiro_tools
    
    # Process tool_results in current message - convert to Kiro format if present
    if current_message.tool_results:
        # Convert unified format to Kiro format
        kiro_tool_results = convert_tool_results_to_kiro_format(current_message.tool_results)
        if kiro_tool_results:
            user_input_context["toolResults"] = kiro_tool_results
    else:
        # Try to extract from content (already in Kiro format)
        tool_results = extract_tool_results_from_content(current_message.content)
        if tool_results:
            user_input_context["toolResults"] = tool_results
    
    # Inject thinking tags if enabled (only for the current/last user message)
    if inject_thinking and current_message.role == "user":
        current_content = inject_thinking_tags(current_content)
    
    # Build userInputMessage
    user_input_message = {
        "content": current_content,
        "modelId": model_id,
        "origin": "AI_EDITOR",
    }
    
    # Add images directly to userInputMessage (NOT to userInputMessageContext)
    if kiro_images:
        user_input_message["images"] = kiro_images
    
    # Add user_input_context if present (contains tools and toolResults only)
    if user_input_context:
        user_input_message["userInputMessageContext"] = user_input_context
    
    # Assemble final payload
    payload = {
        "conversationState": {
            "chatTriggerType": "MANUAL",
            "conversationId": conversation_id,
            "currentMessage": {
                "userInputMessage": user_input_message
            }
        }
    }
    
    # Add history only if not empty
    if history:
        payload["conversationState"]["history"] = history
    
    # Add profileArn
    if profile_arn:
        payload["profileArn"] = profile_arn
    
    return KiroPayloadResult(payload=payload, tool_documentation=tool_documentation)
