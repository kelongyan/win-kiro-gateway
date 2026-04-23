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
Shared upstream request execution helpers for API routes.

This module centralizes the common part of route execution:
- Choosing shared vs per-request HTTP client
- Sending request to Kiro API
- Reading and decoding error responses
- Ensuring clients are closed in the right ownership mode
"""

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx
from loguru import logger

from kiro.http_client import KiroHttpClient
from kiro.kiro_errors import enhance_kiro_error


@dataclass
class UpstreamRequestContext:
    """
    通用上游请求上下文。

    Attributes:
        http_client: 当前请求使用的 HTTP 客户端封装
        response: 上游响应对象；仅在成功发出请求后存在
        url: 实际请求的上游地址
    """

    http_client: KiroHttpClient
    response: httpx.Response
    url: str


@dataclass
class UpstreamErrorResult:
    """
    标准化的上游错误结果。

    Attributes:
        status_code: 上游返回的 HTTP 状态码
        error_text: 解码后的原始错误文本
        user_message: 面向客户端的用户友好错误信息
    """

    status_code: int
    error_text: str
    user_message: str


def build_route_http_client(auth_manager: Any, shared_client: Optional[httpx.AsyncClient], stream: bool) -> KiroHttpClient:
    """
    根据请求模式创建路由使用的 HTTP 客户端。

    Args:
        auth_manager: 认证管理器
        shared_client: 应用级共享 httpx.AsyncClient
        stream: 当前请求是否为流式

    Returns:
        已按既有策略选择 shared/per-request 模式的 KiroHttpClient
    """
    if stream:
        # 流式请求使用独立 client，避免网络切换时遗留孤儿连接。
        return KiroHttpClient(auth_manager, shared_client=None)

    # 非流式请求复用共享 client，维持连接池收益。
    return KiroHttpClient(auth_manager, shared_client=shared_client)


async def execute_kiro_request(
    http_client: KiroHttpClient,
    url: str,
    payload: Dict[str, Any]
) -> httpx.Response:
    """
    执行一次到 Kiro API 的请求。

    Args:
        http_client: 已初始化的 HTTP 客户端封装
        url: 上游请求地址
        payload: Kiro payload

    Returns:
        上游响应对象
    """
    logger.debug(f"Kiro API URL: {url}")
    return await http_client.request_with_retry(
        "POST",
        url,
        payload,
        stream=True
    )


async def parse_upstream_error(response: httpx.Response) -> UpstreamErrorResult:
    """
    解析非 200 上游响应，转换为统一错误结构。

    Args:
        response: 上游响应对象

    Returns:
        标准化错误结果
    """
    try:
        error_content = await response.aread()
    except Exception:
        error_content = b"Unknown error"

    error_text = error_content.decode("utf-8", errors="replace")
    error_message = error_text

    try:
        error_json = json.loads(error_text)
        error_info = enhance_kiro_error(error_json)
        error_message = error_info.user_message
        logger.debug(f"Original Kiro error: {error_info.original_message} (reason: {error_info.reason})")
    except (json.JSONDecodeError, KeyError):
        pass

    return UpstreamErrorResult(
        status_code=response.status_code,
        error_text=error_text,
        user_message=error_message
    )


async def close_route_http_client(http_client: KiroHttpClient) -> None:
    """
    关闭当前路由持有的 HTTP 客户端。

    Args:
        http_client: 路由使用的 HTTP 客户端封装
    """
    await http_client.close()
