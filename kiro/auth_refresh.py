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
Refresh helpers for KiroAuthManager.
"""

from datetime import datetime, timezone, timedelta

import httpx
from loguru import logger

from kiro.config import get_aws_sso_oidc_url


async def refresh_token_kiro_desktop(manager) -> None:
    """
    刷新 Kiro Desktop token。
    """
    if not manager._refresh_token:
        raise ValueError("Refresh token is not set")

    logger.info("Refreshing Kiro token via Kiro Desktop Auth...")

    payload = {"refreshToken": manager._refresh_token}
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"KiroIDE-0.7.45-{manager._fingerprint}",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(manager._refresh_url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    new_access_token = data.get("accessToken")
    new_refresh_token = data.get("refreshToken")
    expires_in = data.get("expiresIn", 3600)
    new_profile_arn = data.get("profileArn")

    if not new_access_token:
        raise ValueError(f"Response does not contain accessToken: {data}")

    manager._access_token = new_access_token
    if new_refresh_token:
        manager._refresh_token = new_refresh_token
    if new_profile_arn:
        manager._profile_arn = new_profile_arn

    # 提前 60 秒刷新，避免临界时刻撞到上游过期。
    manager._expires_at = datetime.now(timezone.utc).replace(microsecond=0)
    manager._expires_at = datetime.fromtimestamp(
        manager._expires_at.timestamp() + expires_in - 60,
        tz=timezone.utc
    )

    logger.info(f"Token refreshed via Kiro Desktop Auth, expires: {manager._expires_at.isoformat()}")

    if manager._sqlite_db:
        manager._save_credentials_to_sqlite()
    else:
        manager._save_credentials_to_file()


async def refresh_token_aws_sso_oidc(manager) -> None:
    """
    刷新 AWS SSO OIDC token，并在 400 + SQLite 模式下做一次重载重试。
    """
    try:
        await do_aws_sso_oidc_refresh(manager)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400 and manager._sqlite_db:
            logger.warning("Token refresh failed with 400, reloading credentials from SQLite and retrying...")
            manager._load_credentials_from_sqlite(manager._sqlite_db)
            await do_aws_sso_oidc_refresh(manager)
        else:
            raise


async def do_aws_sso_oidc_refresh(manager) -> None:
    """
    执行一次真正的 AWS SSO OIDC refresh 请求。
    """
    if not manager._refresh_token:
        raise ValueError("Refresh token is not set")
    if not manager._client_id:
        raise ValueError("Client ID is not set (required for AWS SSO OIDC)")
    if not manager._client_secret:
        raise ValueError("Client secret is not set (required for AWS SSO OIDC)")

    logger.info("Refreshing Kiro token via AWS SSO OIDC...")

    sso_region = manager._sso_region or manager._region
    url = get_aws_sso_oidc_url(sso_region)

    payload = {
        "grantType": "refresh_token",
        "clientId": manager._client_id,
        "clientSecret": manager._client_secret,
        "refreshToken": manager._refresh_token,
    }

    headers = {
        "Content-Type": "application/json",
    }

    logger.debug(
        f"AWS SSO OIDC refresh request: url={url}, sso_region={sso_region}, "
        f"api_region={manager._region}, client_id={manager._client_id[:8]}..."
    )

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, json=payload, headers=headers)

        if response.status_code != 200:
            error_body = response.text
            logger.error(
                f"AWS SSO OIDC refresh failed: status={response.status_code}, "
                f"body={error_body}"
            )
            try:
                error_json = response.json()
                error_code = error_json.get("error", "unknown")
                error_desc = error_json.get("error_description", "no description")
                logger.error(
                    f"AWS SSO OIDC error details: error={error_code}, "
                    f"description={error_desc}"
                )
            except Exception:
                pass
            response.raise_for_status()

        result = response.json()

    new_access_token = result.get("accessToken")
    new_refresh_token = result.get("refreshToken")
    expires_in = result.get("expiresIn", 3600)

    if not new_access_token:
        raise ValueError(f"AWS SSO OIDC response does not contain accessToken: {result}")

    manager._access_token = new_access_token
    if new_refresh_token:
        manager._refresh_token = new_refresh_token

    manager._expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60)

    logger.info(f"Token refreshed via AWS SSO OIDC, expires: {manager._expires_at.isoformat()}")

    if manager._sqlite_db:
        manager._save_credentials_to_sqlite()
    else:
        manager._save_credentials_to_file()
