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
Debug logging module for requests.

Supports three modes (DEBUG_MODE):
- off: logging disabled
- errors: logs are saved only on errors (4xx, 5xx)
- all: logs are overwritten on each request

In "errors" mode, data is buffered in memory and flushed to files
only when flush_on_error() is called.

Also captures application logs (loguru) for each request and saves
them to app_logs.txt file for debugging convenience.
"""

import contextvars
import io
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from loguru import logger

from kiro.config import DEBUG_MODE, DEBUG_DIR


@dataclass
class DebugLogSession:
    """保存单个请求的调试日志上下文，避免并发请求共享缓冲区。"""
    request_body_buffer: Optional[bytes] = None
    kiro_request_body_buffer: Optional[bytes] = None
    raw_chunks_buffer: bytearray = field(default_factory=bytearray)
    modified_chunks_buffer: bytearray = field(default_factory=bytearray)
    app_logs_buffer: io.StringIO = field(default_factory=io.StringIO)
    loguru_sink_id: Optional[int] = None


class DebugLogger:
    """
    Singleton for managing debug request logs.

    Operating modes:
    - off: does nothing
    - errors: buffers data, flushes to files only on errors
    - all: writes data immediately to files (as before)
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DebugLogger, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.debug_dir = Path(DEBUG_DIR)
        self._current_session: contextvars.ContextVar[Optional[DebugLogSession]] = contextvars.ContextVar(
            "debug_log_session",
            default=None
        )
        self._initialized = True

    @property
    def _request_body_buffer(self) -> Optional[bytes]:
        return self._get_session().request_body_buffer

    @_request_body_buffer.setter
    def _request_body_buffer(self, value: Optional[bytes]) -> None:
        self._get_session().request_body_buffer = value

    @property
    def _kiro_request_body_buffer(self) -> Optional[bytes]:
        return self._get_session().kiro_request_body_buffer

    @_kiro_request_body_buffer.setter
    def _kiro_request_body_buffer(self, value: Optional[bytes]) -> None:
        self._get_session().kiro_request_body_buffer = value

    @property
    def _raw_chunks_buffer(self) -> bytearray:
        return self._get_session().raw_chunks_buffer

    @property
    def _modified_chunks_buffer(self) -> bytearray:
        return self._get_session().modified_chunks_buffer

    @property
    def _app_logs_buffer(self) -> io.StringIO:
        return self._get_session().app_logs_buffer

    @_app_logs_buffer.setter
    def _app_logs_buffer(self, value: io.StringIO) -> None:
        self._get_session().app_logs_buffer = value

    @property
    def _loguru_sink_id(self) -> Optional[int]:
        return self._get_session().loguru_sink_id

    @_loguru_sink_id.setter
    def _loguru_sink_id(self, value: Optional[int]) -> None:
        self._get_session().loguru_sink_id = value

    def _get_session(self) -> DebugLogSession:
        """获取当前请求的调试会话；单元测试直接调用时按需创建默认会话。"""
        session = self._current_session.get()
        if session is None:
            session = DebugLogSession()
            self._current_session.set(session)
        return session

    def _set_session(self, session: Optional[DebugLogSession]) -> contextvars.Token:
        """设置当前异步上下文的调试会话，并返回可恢复的 token。"""
        return self._current_session.set(session)

    def reset_request(self, token: contextvars.Token) -> None:
        """恢复进入请求前的调试上下文。"""
        self._current_session.reset(token)

    def clear_request(self, request: Any) -> None:
        """请求处理结束后恢复之前的调试上下文。"""
        state = getattr(request, "state", None)
        token = getattr(state, "debug_logger_token", None)
        if token is None:
            return
        self.reset_request(token)
        try:
            delattr(state, "debug_logger_token")
        except AttributeError:
            pass

    def _is_enabled(self) -> bool:
        """Checks if logging is enabled."""
        return DEBUG_MODE in ("errors", "all")

    def _is_immediate_write(self) -> bool:
        """Checks if immediate file writing is needed (all mode)."""
        return DEBUG_MODE == "all"

    def _clear_buffers(self):
        """Clears all buffers."""
        session = self._get_session()
        session.request_body_buffer = None
        session.kiro_request_body_buffer = None
        session.raw_chunks_buffer.clear()
        session.modified_chunks_buffer.clear()
        self._clear_app_logs_buffer()

    def _clear_app_logs_buffer(self):
        """Clears the application logs buffer and removes sink."""
        session = self._get_session()
        if session.loguru_sink_id is not None:
            try:
                logger.remove(session.loguru_sink_id)
            except ValueError:
                pass
            session.loguru_sink_id = None
        session.app_logs_buffer = io.StringIO()

    def _setup_app_logs_capture(self):
        """
        Sets up application log capture to buffer.

        Adds a temporary sink to loguru that writes to StringIO buffer.
        Captures ALL logs without filtering, as sink is active only
        during processing of a specific request.
        """
        self._clear_app_logs_buffer()
        session = self._get_session()
        session.loguru_sink_id = logger.add(
            session.app_logs_buffer,
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
            level="DEBUG",
            colorize=False,
        )

    def prepare_new_request(self):
        """
        Prepares the logger for a new request.

        In "all" mode: clears the logs folder.
        In "errors" mode: clears buffers.
        In both modes: sets up application log capture.
        """
        if not self._is_enabled():
            return None

        token = self._set_session(DebugLogSession())
        self._setup_app_logs_capture()

        if self._is_immediate_write():
            try:
                if self.debug_dir.exists():
                    shutil.rmtree(self.debug_dir)
                self.debug_dir.mkdir(parents=True, exist_ok=True)
                logger.debug(f"[DebugLogger] Directory {self.debug_dir} cleared for new request.")
            except Exception as e:
                logger.error(f"[DebugLogger] Error preparing directory: {e}")
        return token

    def log_request_body(self, body: bytes):
        """
        Saves the request body (from client, OpenAI format).

        In "all" mode: writes immediately to file.
        In "errors" mode: buffers.
        """
        if not self._is_enabled():
            return

        if self._is_immediate_write():
            self._write_request_body_to_file(body)
        else:
            self._get_session().request_body_buffer = body

    def log_kiro_request_body(self, body: bytes):
        """
        Saves the modified request body (to Kiro API).

        In "all" mode: writes immediately to file.
        In "errors" mode: buffers.
        """
        if not self._is_enabled():
            return

        if self._is_immediate_write():
            self._write_kiro_request_body_to_file(body)
        else:
            self._get_session().kiro_request_body_buffer = body

    def log_raw_chunk(self, chunk: bytes):
        """
        Appends raw response chunk (from provider).

        In "all" mode: writes immediately to file.
        In "errors" mode: buffers.
        """
        if not self._is_enabled():
            return

        if self._is_immediate_write():
            self._append_raw_chunk_to_file(chunk)
        else:
            self._get_session().raw_chunks_buffer.extend(chunk)

    def log_modified_chunk(self, chunk: bytes):
        """
        Appends modified chunk (to client).

        In "all" mode: writes immediately to file.
        In "errors" mode: buffers.
        """
        if not self._is_enabled():
            return

        if self._is_immediate_write():
            self._append_modified_chunk_to_file(chunk)
        else:
            self._get_session().modified_chunks_buffer.extend(chunk)

    def log_error_info(self, status_code: int, error_message: str = ""):
        """
        Writes error information to file.

        Works in both modes (errors and all).
        In "all" mode writes immediately to file.
        In "errors" mode called from flush_on_error().

        Args:
            status_code: HTTP error status code
            error_message: Error message (optional)
        """
        if not self._is_enabled():
            return

        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)

            error_info = {
                "status_code": status_code,
                "error_message": error_message
            }
            error_file = self.debug_dir / "error_info.json"
            with open(error_file, "w", encoding="utf-8") as f:
                json.dump(error_info, f, indent=2, ensure_ascii=False)

            logger.debug(f"[DebugLogger] Error info saved (status={status_code})")
        except Exception as e:
            logger.error(f"[DebugLogger] Error writing error_info: {e}")

    def flush_on_error(self, status_code: int, error_message: str = ""):
        """
        Flushes buffers to files on error.

        In "errors" mode: flushes buffers and saves error_info.
        In "all" mode: only saves error_info (data already written).

        Args:
            status_code: HTTP error status code
            error_message: Error message (optional)
        """
        if not self._is_enabled():
            return

        if self._is_immediate_write():
            self.log_error_info(status_code, error_message)
            self._write_app_logs_to_file()
            self._clear_app_logs_buffer()
            return

        session = self._get_session()
        if not any([
            session.request_body_buffer,
            session.kiro_request_body_buffer,
            session.raw_chunks_buffer,
            session.modified_chunks_buffer,
        ]):
            return

        try:
            if self.debug_dir.exists():
                shutil.rmtree(self.debug_dir)
            self.debug_dir.mkdir(parents=True, exist_ok=True)

            if session.request_body_buffer:
                self._write_request_body_to_file(session.request_body_buffer)

            if session.kiro_request_body_buffer:
                self._write_kiro_request_body_to_file(session.kiro_request_body_buffer)

            if session.raw_chunks_buffer:
                file_path = self.debug_dir / "response_stream_raw.txt"
                with open(file_path, "wb") as f:
                    f.write(session.raw_chunks_buffer)

            if session.modified_chunks_buffer:
                file_path = self.debug_dir / "response_stream_modified.txt"
                with open(file_path, "wb") as f:
                    f.write(session.modified_chunks_buffer)

            self.log_error_info(status_code, error_message)
            self._write_app_logs_to_file()

            logger.info(f"[DebugLogger] Error logs flushed to {self.debug_dir} (status={status_code})")

        except Exception as e:
            logger.error(f"[DebugLogger] Error flushing buffers: {e}")
        finally:
            self._clear_buffers()

    def discard_buffers(self):
        """
        Clears buffers without writing to files.

        Called when request completed successfully in "errors" mode.
        Also called in "all" mode to save logs of successful request.
        """
        if DEBUG_MODE == "errors":
            self._clear_buffers()
        elif DEBUG_MODE == "all":
            self._write_app_logs_to_file()
            self._clear_app_logs_buffer()

    # ==================== Private file writing methods ====================

    def _write_request_body_to_file(self, body: bytes):
        """Writes request body to file."""
        try:
            file_path = self.debug_dir / "request_body.json"
            try:
                json_obj = json.loads(body)
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(json_obj, f, indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                with open(file_path, "wb") as f:
                    f.write(body)
        except Exception as e:
            logger.error(f"[DebugLogger] Error writing request_body: {e}")

    def _write_kiro_request_body_to_file(self, body: bytes):
        """Writes Kiro request body to file."""
        try:
            file_path = self.debug_dir / "kiro_request_body.json"
            try:
                json_obj = json.loads(body)
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(json_obj, f, indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                with open(file_path, "wb") as f:
                    f.write(body)
        except Exception as e:
            logger.error(f"[DebugLogger] Error writing kiro_request_body: {e}")

    def _append_raw_chunk_to_file(self, chunk: bytes):
        """Appends raw chunk to file."""
        try:
            file_path = self.debug_dir / "response_stream_raw.txt"
            with open(file_path, "ab") as f:
                f.write(chunk)
        except Exception:
            pass

    def _append_modified_chunk_to_file(self, chunk: bytes):
        """Appends modified chunk to file."""
        try:
            file_path = self.debug_dir / "response_stream_modified.txt"
            with open(file_path, "ab") as f:
                f.write(chunk)
        except Exception:
            pass

    def _write_app_logs_to_file(self):
        """Writes captured application logs to file."""
        try:
            logs_content = self._get_session().app_logs_buffer.getvalue()

            if not logs_content.strip():
                return

            self.debug_dir.mkdir(parents=True, exist_ok=True)

            file_path = self.debug_dir / "app_logs.txt"
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(logs_content)

            logger.debug(f"[DebugLogger] App logs saved to {file_path}")
        except Exception:
            pass


# Global instance
debug_logger = DebugLogger()
