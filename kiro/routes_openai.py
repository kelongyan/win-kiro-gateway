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
FastAPI routes for Kiro Gateway.

Contains all API endpoints:
- / and /health: Health check
- /v1/models: Models list
- /v1/chat/completions: Chat completions
"""

import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Security
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from loguru import logger

from kiro.config import (
    PROXY_API_KEY,
    APP_VERSION,
    UPSTREAM_PROVIDER,
    DEBUG_MODE,
)
from kiro.models_openai import (
    OpenAIModel,
    ModelList,
    ChatCompletionRequest,
)
from kiro.auth import KiroAuthManager, AuthType
from kiro.cache import ModelInfoCache
from kiro.model_resolver import ModelResolver
from kiro.converters_openai import build_kiro_payload
from kiro.streaming_openai import stream_kiro_to_openai, collect_stream_response, stream_with_first_token_retry
from kiro.http_client import KiroHttpClient
from kiro.request_executor import (
    build_route_http_client,
    close_route_http_client,
    execute_kiro_request,
    parse_upstream_error,
)
from kiro.request_limiter import RequestLimiterBusy, acquire_request_slot, release_request_slot
from kiro.utils import generate_conversation_id

# Import debug_logger
try:
    from kiro.debug_logger import debug_logger
except ImportError:
    debug_logger = None


# --- Security scheme ---
api_key_header = APIKeyHeader(name="Authorization", auto_error=False)


async def verify_api_key(auth_header: str = Security(api_key_header)) -> bool:
    """
    Verify API key in Authorization header.
    
    Expects format: "Bearer {PROXY_API_KEY}"
    
    Args:
        auth_header: Authorization header value
    
    Returns:
        True if key is valid
    
    Raises:
        HTTPException: 401 if key is invalid or missing
    """
    if not auth_header or auth_header != f"Bearer {PROXY_API_KEY}":
        logger.warning("Access attempt with invalid API key.")
        raise HTTPException(status_code=401, detail="Invalid or missing API Key")
    return True


# --- Router ---
router = APIRouter()


@router.get("/")
async def root():
    """
    Health check endpoint.
    
    Returns:
        Status and application version
    """
    return {
        "status": "ok",
        "message": "Kiro Gateway is running",
        "version": APP_VERSION
    }


@router.get("/health")
async def health(request: Request):
    """
    Detailed health check.

    Returns:
        Status, timestamp and version
    """
    # 汇总本地运行态信息，避免健康检查主动访问上游导致额外压力。
    auth_manager = getattr(request.app.state, "auth_manager", None)
    model_cache = getattr(request.app.state, "model_cache", None)
    health_counters = getattr(request.app.state, "health_counters", {})
    started_at = getattr(request.app.state, "started_at", None)
    limiter = getattr(request.app.state, "request_limiter", None)
    limiter_limit = getattr(request.app.state, "request_limiter_limit", 0)
    available_slots = None
    if limiter is not None and hasattr(limiter, "_value"):
        available_slots = limiter._value

    auth_type = None
    api_host = None
    region = None
    if auth_manager is not None:
        auth_type_value = getattr(auth_manager, "auth_type", None)
        auth_type = auth_type_value.value if auth_type_value is not None else None
        api_host = getattr(auth_manager, "api_host", None)
        region = getattr(auth_manager, "region", None)

    model_count = None
    cache_stale = None
    if model_cache is not None:
        model_count = model_cache.size
        cache_stale = model_cache.is_stale()

    uptime_seconds = 0
    if started_at is not None:
        uptime_seconds = max(0, int(time.time() - started_at))

    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": APP_VERSION,
        "uptime_seconds": uptime_seconds,
        "requests_total": health_counters.get("requests_total", 0),
        "errors_total": health_counters.get("errors_total", 0),
        "cached_responses": health_counters.get("cached_responses", 0),
        "debug_mode": DEBUG_MODE,
        "auth": {
            "initialized": auth_manager is not None,
            "type": auth_type,
            "region": region,
            "api_host": api_host,
        },
        "models": {
            "initialized": model_cache is not None,
            "count": model_count,
            "cache_stale": cache_stale,
        },
        "request_limiter": {
            "enabled": limiter is not None,
            "limit": limiter_limit,
            "available_slots": available_slots,
            "queue_timeout_seconds": getattr(request.app.state, "request_queue_timeout", None),
        },
    }

@router.get("/v1/models", response_model=ModelList, dependencies=[Depends(verify_api_key)])
async def get_models(request: Request):
    """
    Return list of available models.
    
    Models are loaded at startup (blocking) and cached.
    This endpoint returns the cached list.
    
    Args:
        request: FastAPI Request for accessing app.state
    
    Returns:
        ModelList with available models in consistent format (with dots)
    """
    logger.info("Request to /v1/models")
    
    model_resolver: ModelResolver = request.app.state.model_resolver
    
    # Get all available models from resolver (cache + hidden models)
    available_model_ids = model_resolver.get_available_models()
    
    # Build OpenAI-compatible model list
    owner = "zhipu" if UPSTREAM_PROVIDER == "glm" else "anthropic"
    description = "GLM-5 model via gateway" if UPSTREAM_PROVIDER == "glm" else "Claude model via Kiro API"

    openai_models = [
        OpenAIModel(
            id=model_id,
            owned_by=owner,
            description=description
        )
        for model_id in available_model_ids
    ]
    
    return ModelList(data=openai_models)


@router.post("/v1/chat/completions", dependencies=[Depends(verify_api_key)])
async def chat_completions(request: Request, request_data: ChatCompletionRequest):
    """
    Chat completions endpoint - compatible with OpenAI API.
    
    Accepts requests in OpenAI format and translates them to Kiro API.
    Supports streaming and non-streaming modes.
    
    Args:
        request: FastAPI Request for accessing app.state
        request_data: Request in OpenAI ChatCompletionRequest format
    
    Returns:
        StreamingResponse for streaming mode
        JSONResponse for non-streaming mode
    
    Raises:
        HTTPException: On validation or API errors
    """
    logger.info(f"Request to /v1/chat/completions (model={request_data.model}, stream={request_data.stream})")
    # 仅记录本地计数，帮助 /health 快速判断服务是否持续接流量。
    health_counters = getattr(request.app.state, "health_counters", None)
    if isinstance(health_counters, dict):
        health_counters["requests_total"] = health_counters.get("requests_total", 0) + 1
    auth_manager: KiroAuthManager = request.app.state.auth_manager
    model_cache: ModelInfoCache = request.app.state.model_cache
    model_resolver: ModelResolver = request.app.state.model_resolver
    
    # Note: prepare_new_request() and log_request_body() are now called by DebugLoggerMiddleware
    # This ensures debug logging works even for requests that fail Pydantic validation (422 errors)
    
    # Check for truncation recovery opportunities
    from kiro.truncation_state import get_tool_truncation, get_content_truncation
    from kiro.truncation_recovery import generate_truncation_tool_result, generate_truncation_user_message
    from kiro.models_openai import ChatMessage
    
    modified_messages = []
    tool_results_modified = 0
    content_notices_added = 0
    
    for msg in request_data.messages:
        # Check if this is a tool_result for a truncated tool call
        if msg.role == "tool" and msg.tool_call_id:
            truncation_info = get_tool_truncation(msg.tool_call_id)
            if truncation_info:
                # Modify tool_result content to include truncation notice
                synthetic = generate_truncation_tool_result(
                    tool_name=truncation_info.tool_name,
                    tool_use_id=msg.tool_call_id,
                    truncation_info=truncation_info.truncation_info
                )
                # Prepend truncation notice to original content
                modified_content = f"{synthetic['content']}\n\n---\n\nOriginal tool result:\n{msg.content}"
                
                # Create NEW ChatMessage object (Pydantic immutability)
                modified_msg = msg.model_copy(update={"content": modified_content})
                modified_messages.append(modified_msg)
                tool_results_modified += 1
                logger.debug(f"Modified tool_result for {msg.tool_call_id} to include truncation notice")
                continue  # Skip normal append since we already added modified version
        
        # Check if this is an assistant message with truncated content
        if msg.role == "assistant" and msg.content and isinstance(msg.content, str):
            truncation_info = get_content_truncation(msg.content)
            if truncation_info:
                # Add this message first
                modified_messages.append(msg)
                # Then add synthetic user message about truncation
                synthetic_user_msg = ChatMessage(
                    role="user",
                    content=generate_truncation_user_message()
                )
                modified_messages.append(synthetic_user_msg)
                content_notices_added += 1
                logger.debug(f"Added truncation notice after assistant message (hash: {truncation_info.message_hash})")
                continue  # Skip normal append since we already added it
        
        modified_messages.append(msg)
    
    if tool_results_modified > 0 or content_notices_added > 0:
        request_data.messages = modified_messages
        logger.info(f"Truncation recovery: modified {tool_results_modified} tool_result(s), added {content_notices_added} content notice(s)")
    
    # Generate conversation ID for Kiro API (random UUID, not used for tracking)
    conversation_id = generate_conversation_id()

    # 统一在路由层完成模型解析，确保展示层与实际执行链路一致。
    model_resolution = model_resolver.resolve(request_data.model)
    logger.debug(
        f"Resolved OpenAI model '{request_data.model}' -> '{model_resolution.internal_id}' "
        f"(source={model_resolution.source})"
    )
    
    # Build payload for Kiro
    # profileArn is only needed for Kiro Desktop auth
    # AWS SSO OIDC (Builder ID) users don't need profileArn and it causes 403 if sent
    profile_arn_for_payload = ""
    if auth_manager.auth_type == AuthType.KIRO_DESKTOP and auth_manager.profile_arn:
        profile_arn_for_payload = auth_manager.profile_arn
    
    try:
        kiro_payload = build_kiro_payload(
            request_data,
            conversation_id,
            profile_arn_for_payload,
            resolved_model_id=model_resolution.internal_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Log Kiro payload
    try:
        kiro_request_body = json.dumps(kiro_payload, ensure_ascii=False, indent=2).encode('utf-8')
        if debug_logger:
            debug_logger.log_kiro_request_body(kiro_request_body)
    except Exception as e:
        logger.warning(f"Failed to log Kiro request: {e}")
    
    url = f"{auth_manager.api_host}/generateAssistantResponse"
    shared_client = request.app.state.http_client
    http_client = build_route_http_client(
        auth_manager=auth_manager,
        shared_client=shared_client,
        stream=request_data.stream
    )
    limiter_acquired = False
    try:
        limiter_acquired = await acquire_request_slot(request.app.state)
        # Make request to Kiro API (for both streaming and non-streaming modes)
        # Important: we wait for Kiro response BEFORE returning StreamingResponse,
        # so that 200 OK means Kiro accepted the request and started responding
        response = await execute_kiro_request(http_client, url, kiro_payload)
        
        if response.status_code != 200:
            error_result = await parse_upstream_error(response)
            await close_route_http_client(http_client)
            release_request_slot(request.app.state, limiter_acquired)
            limiter_acquired = False

            # Log access log for error (before flush, so it gets into app_logs)
            logger.warning(
                f"HTTP {error_result.status_code} - POST /v1/chat/completions - {error_result.user_message[:100]}"
            )
            
            # Flush debug logs on error ("errors" mode)
            if debug_logger:
                debug_logger.flush_on_error(error_result.status_code, error_result.user_message)
            
            # Return error in OpenAI API format
            return JSONResponse(
                status_code=error_result.status_code,
                content={
                    "error": {
                        "message": error_result.user_message,
                        "type": "kiro_api_error",
                        "code": error_result.status_code
                    }
                }
            )
        
        # Prepare data for fallback token counting
        # Convert Pydantic models to dicts for tokenizer
        messages_for_tokenizer = [msg.model_dump() for msg in request_data.messages]
        tools_for_tokenizer = [tool.model_dump() for tool in request_data.tools] if request_data.tools else None
        
        if request_data.stream:
            # Streaming mode
            async def stream_wrapper():
                nonlocal limiter_acquired
                streaming_error = None
                client_disconnected = False
                try:
                    async for chunk in stream_kiro_to_openai(
                        http_client.client,
                        response,
                        request_data.model,
                        model_cache,
                        auth_manager,
                        request_messages=messages_for_tokenizer,
                        request_tools=tools_for_tokenizer
                    ):
                        yield chunk
                except GeneratorExit:
                    # Client disconnected - this is normal
                    client_disconnected = True
                    logger.debug("Client disconnected during streaming (GeneratorExit in routes)")
                except Exception as e:
                    streaming_error = e
                    # Try to send [DONE] to client before finishing
                    # so client doesn't "hang" waiting for data
                    try:
                        yield "data: [DONE]\n\n"
                    except Exception:
                        pass  # Client already disconnected
                    raise
                finally:
                    await close_route_http_client(http_client)
                    # Log access log for streaming (success or error)
                    if streaming_error:
                        error_type = type(streaming_error).__name__
                        error_msg = str(streaming_error) if str(streaming_error) else "(empty message)"
                        logger.error(f"HTTP 500 - POST /v1/chat/completions (streaming) - [{error_type}] {error_msg[:100]}")
                    elif client_disconnected:
                        logger.info(f"HTTP 200 - POST /v1/chat/completions (streaming) - client disconnected")
                    else:
                        logger.info(f"HTTP 200 - POST /v1/chat/completions (streaming) - completed")
                    release_request_slot(request.app.state, limiter_acquired)
                    limiter_acquired = False
                    # Write debug logs AFTER streaming completes
                    if debug_logger:
                        if streaming_error:
                            debug_logger.flush_on_error(500, str(streaming_error))
                        else:
                            debug_logger.discard_buffers()
            
            return StreamingResponse(stream_wrapper(), media_type="text/event-stream")
        
        else:
            
            # Non-streaming mode - collect entire response
            openai_response = await collect_stream_response(
                http_client.client,
                response,
                request_data.model,
                model_cache,
                auth_manager,
                request_messages=messages_for_tokenizer,
                request_tools=tools_for_tokenizer
            )
            
            await close_route_http_client(http_client)
            release_request_slot(request.app.state, limiter_acquired)
            limiter_acquired = False

            # Log access log for non-streaming success
            logger.info(f"HTTP 200 - POST /v1/chat/completions (non-streaming) - completed")
            
            # Write debug logs after non-streaming request completes
            if debug_logger:
                debug_logger.discard_buffers()
            
            return JSONResponse(content=openai_response)
    
    except RequestLimiterBusy as e:
        await close_route_http_client(http_client)
        logger.warning(f"HTTP 429 - POST /v1/chat/completions - {e}")
        if debug_logger:
            debug_logger.flush_on_error(429, str(e))
        if isinstance(health_counters, dict):
            health_counters["errors_total"] = health_counters.get("errors_total", 0) + 1
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "message": str(e),
                    "type": "rate_limit_error",
                    "code": 429
                }
            }
        )
    except HTTPException as e:
        await close_route_http_client(http_client)
        release_request_slot(request.app.state, limiter_acquired)
        # Log access log for HTTP error
        logger.error(f"HTTP {e.status_code} - POST /v1/chat/completions - {e.detail}")
        if isinstance(health_counters, dict):
            health_counters["errors_total"] = health_counters.get("errors_total", 0) + 1
        # Flush debug logs on HTTP error ("errors" mode)
        if debug_logger:
            debug_logger.flush_on_error(e.status_code, str(e.detail))
        raise
    except Exception as e:
        await close_route_http_client(http_client)
        release_request_slot(request.app.state, limiter_acquired)
        logger.error(f"Internal error: {e}", exc_info=True)
        if isinstance(health_counters, dict):
            health_counters["errors_total"] = health_counters.get("errors_total", 0) + 1
        # Log access log for internal error
        logger.error(f"HTTP 500 - POST /v1/chat/completions - {str(e)[:100]}")
        # Flush debug logs on internal error ("errors" mode)
        if debug_logger:
            debug_logger.flush_on_error(500, str(e))
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")
    finally:
        if debug_logger:
            debug_logger.clear_request(request)
