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
Storage helpers for KiroAuthManager.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Sequence

from loguru import logger

from kiro.config import (
    get_kiro_refresh_url,
    get_kiro_api_host,
    get_kiro_q_host,
)


def load_credentials_from_sqlite(manager, db_path: str, token_keys: Sequence[str], registration_keys: Sequence[str]) -> None:
    """
    从 kiro-cli SQLite 数据库加载凭证。
    """
    try:
        path = Path(db_path).expanduser()
        if not path.exists():
            logger.warning(f"SQLite database not found: {db_path}")
            return

        conn = sqlite3.connect(str(path))
        cursor = conn.cursor()

        token_row = None
        for key in token_keys:
            cursor.execute("SELECT value FROM auth_kv WHERE key = ?", (key,))
            token_row = cursor.fetchone()
            if token_row:
                manager._sqlite_token_key = key
                logger.debug(f"Loaded credentials from SQLite key: {key}")
                break

        if token_row:
            token_data = json.loads(token_row[0])
            if token_data:
                if "access_token" in token_data:
                    manager._access_token = token_data["access_token"]
                if "refresh_token" in token_data:
                    manager._refresh_token = token_data["refresh_token"]
                if "profile_arn" in token_data:
                    manager._profile_arn = token_data["profile_arn"]
                if "region" in token_data:
                    # 这里只记录 SSO 区域，真正 API 仍使用初始化时的区域。
                    manager._sso_region = token_data["region"]
                    logger.debug(f"SSO region from SQLite: {manager._sso_region} (API stays at {manager._region})")
                if "scopes" in token_data:
                    manager._scopes = token_data["scopes"]
                if "expires_at" in token_data:
                    try:
                        expires_str = token_data["expires_at"]
                        if expires_str.endswith("Z"):
                            manager._expires_at = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
                        else:
                            manager._expires_at = datetime.fromisoformat(expires_str)
                        if manager._access_token and manager._expires_at and manager._expires_at > datetime.now(manager._expires_at.tzinfo):
                            manager._last_refresh_at = datetime.now(manager._expires_at.tzinfo)
                    except Exception as e:
                        logger.warning(f"Failed to parse expires_at from SQLite: {e}")

        registration_row = None
        for key in registration_keys:
            cursor.execute("SELECT value FROM auth_kv WHERE key = ?", (key,))
            registration_row = cursor.fetchone()
            if registration_row:
                logger.debug(f"Loaded device registration from SQLite key: {key}")
                break

        if registration_row:
            registration_data = json.loads(registration_row[0])
            if registration_data:
                if "client_id" in registration_data:
                    manager._client_id = registration_data["client_id"]
                if "client_secret" in registration_data:
                    manager._client_secret = registration_data["client_secret"]
                if "region" in registration_data and not manager._sso_region:
                    manager._sso_region = registration_data["region"]
                    logger.debug(f"SSO region from device-registration: {manager._sso_region}")

        conn.close()
        logger.info(f"Credentials loaded from SQLite database: {db_path}")

    except sqlite3.Error as e:
        logger.error(f"SQLite error loading credentials: {e}")
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in SQLite data: {e}")
    except Exception as e:
        logger.error(f"Error loading credentials from SQLite: {e}")


def load_credentials_from_file(manager, file_path: str) -> None:
    """
    从 JSON 凭证文件加载凭证。
    """
    try:
        path = Path(file_path).expanduser()
        if not path.exists():
            logger.warning(f"Credentials file not found: {file_path}")
            return

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "refreshToken" in data:
            manager._refresh_token = data["refreshToken"]
        if "accessToken" in data:
            manager._access_token = data["accessToken"]
        if "profileArn" in data:
            manager._profile_arn = data["profileArn"]
        if "region" in data:
            manager._region = data["region"]
            manager._refresh_url = get_kiro_refresh_url(manager._region)
            manager._api_host = get_kiro_api_host(manager._region)
            manager._q_host = get_kiro_q_host(manager._region)
            logger.info(
                f"Region updated from credentials file: region={manager._region}, "
                f"api_host={manager._api_host}, q_host={manager._q_host}"
            )

        if "clientIdHash" in data:
            manager._client_id_hash = data["clientIdHash"]
            manager._load_enterprise_device_registration(manager._client_id_hash)

        if "clientId" in data:
            manager._client_id = data["clientId"]
        if "clientSecret" in data:
            manager._client_secret = data["clientSecret"]

        if "expiresAt" in data:
            try:
                expires_str = data["expiresAt"]
                if expires_str.endswith("Z"):
                    manager._expires_at = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
                else:
                    manager._expires_at = datetime.fromisoformat(expires_str)
                if manager._access_token and manager._expires_at and manager._expires_at > datetime.now(manager._expires_at.tzinfo):
                    manager._last_refresh_at = datetime.now(manager._expires_at.tzinfo)
            except Exception as e:
                logger.warning(f"Failed to parse expiresAt: {e}")

        logger.info(f"Credentials loaded from {file_path}")

    except Exception as e:
        logger.error(f"Error loading credentials from file: {e}")


def load_enterprise_device_registration(manager, client_id_hash: str) -> None:
    """
    从 Enterprise Kiro IDE 的 device registration 文件加载 clientId / clientSecret。
    """
    try:
        device_reg_path = Path.home() / ".aws" / "sso" / "cache" / f"{client_id_hash}.json"

        if not device_reg_path.exists():
            logger.warning(f"Enterprise device registration file not found: {device_reg_path}")
            return

        with open(device_reg_path, "r", encoding="utf-8") as f:
            device_data = json.load(f)

        if "clientId" in device_data:
            manager._client_id = device_data["clientId"]

        if "clientSecret" in device_data:
            manager._client_secret = device_data["clientSecret"]

        logger.info(f"Enterprise device registration loaded from {device_reg_path}")

    except Exception as e:
        logger.error(f"Error loading enterprise device registration: {e}")


def save_credentials_to_file(manager) -> None:
    """
    将刷新后的凭证回写到 JSON 文件。
    """
    if not manager._creds_file:
        return

    try:
        path = Path(manager._creds_file).expanduser()

        existing_data = {}
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)

        existing_data["accessToken"] = manager._access_token
        existing_data["refreshToken"] = manager._refresh_token
        if manager._expires_at:
            existing_data["expiresAt"] = manager._expires_at.isoformat()
        if manager._profile_arn:
            existing_data["profileArn"] = manager._profile_arn

        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing_data, f, indent=2, ensure_ascii=False)

        logger.debug(f"Credentials saved to {manager._creds_file}")

    except Exception as e:
        logger.error(f"Error saving credentials: {e}")


def save_credentials_to_sqlite(manager, token_keys: Sequence[str]) -> None:
    """
    将刷新后的凭证回写到 SQLite。
    """
    if not manager._sqlite_db:
        return

    try:
        path = Path(manager._sqlite_db).expanduser()
        if not path.exists():
            logger.warning(f"SQLite database not found for writing: {manager._sqlite_db}")
            return

        conn = sqlite3.connect(str(path), timeout=5.0)
        cursor = conn.cursor()

        token_data = {
            "access_token": manager._access_token,
            "refresh_token": manager._refresh_token,
            "expires_at": manager._expires_at.isoformat() if manager._expires_at else None,
            "region": manager._sso_region or manager._region,
        }
        if manager._scopes:
            token_data["scopes"] = manager._scopes

        token_json = json.dumps(token_data)

        if manager._sqlite_token_key:
            cursor.execute(
                "UPDATE auth_kv SET value = ? WHERE key = ?",
                (token_json, manager._sqlite_token_key)
            )
            if cursor.rowcount > 0:
                conn.commit()
                conn.close()
                logger.debug(f"Credentials saved to SQLite key: {manager._sqlite_token_key}")
                return
            logger.warning(f"Failed to update SQLite key: {manager._sqlite_token_key}, trying fallback")

        for key in token_keys:
            cursor.execute(
                "UPDATE auth_kv SET value = ? WHERE key = ?",
                (token_json, key)
            )
            if cursor.rowcount > 0:
                conn.commit()
                conn.close()
                logger.debug(f"Credentials saved to SQLite key: {key} (fallback)")
                return

        conn.close()
        logger.warning("Failed to save credentials to SQLite: no matching keys found")

    except sqlite3.Error as e:
        logger.error(f"SQLite error saving credentials: {e}")
    except Exception as e:
        logger.error(f"Error saving credentials to SQLite: {e}")
