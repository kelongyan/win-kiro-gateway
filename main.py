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
Kiro Gateway - OpenAI-compatible interface for Kiro API.

Application entry point. Creates FastAPI app and connects routes.

Usage:
    # Using default settings (host: 0.0.0.0, port: 8000)
    python main.py
    
    # With CLI arguments (highest priority)
    python main.py --port 9000
    python main.py --host 127.0.0.1 --port 9000
    
    # With environment variables (medium priority)
    SERVER_PORT=9000 python main.py
    
    # Using uvicorn directly (uvicorn handles its own CLI args)
    uvicorn main:app --host 0.0.0.0 --port 8000

Priority: CLI args > Environment variables > Default values
"""

import argparse
import asyncio
import importlib
import logging
import sys
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

# 先显式加载运行环境，再导入其他项目模块，避免 `kiro.config`
# 在导入阶段被本地 `.env` 污染。
import kiro.config as config_module

config_module.load_runtime_env()
config_module = importlib.reload(config_module)

APP_TITLE = config_module.APP_TITLE
APP_DESCRIPTION = config_module.APP_DESCRIPTION
APP_VERSION = config_module.APP_VERSION
REFRESH_TOKEN = config_module.REFRESH_TOKEN
PROFILE_ARN = config_module.PROFILE_ARN
REGION = config_module.REGION
KIRO_CREDS_FILE = config_module.KIRO_CREDS_FILE
KIRO_CLI_DB_FILE = config_module.KIRO_CLI_DB_FILE
PROXY_API_KEY = config_module.PROXY_API_KEY
LOG_LEVEL = config_module.LOG_LEVEL
SERVER_HOST = config_module.SERVER_HOST
SERVER_PORT = config_module.SERVER_PORT
DEFAULT_SERVER_HOST = config_module.DEFAULT_SERVER_HOST
DEFAULT_SERVER_PORT = config_module.DEFAULT_SERVER_PORT
STREAMING_READ_TIMEOUT = config_module.STREAMING_READ_TIMEOUT
HTTP_MAX_CONNECTIONS = config_module.HTTP_MAX_CONNECTIONS
HTTP_MAX_KEEPALIVE_CONNECTIONS = config_module.HTTP_MAX_KEEPALIVE_CONNECTIONS
HTTP_KEEPALIVE_EXPIRY = config_module.HTTP_KEEPALIVE_EXPIRY
HTTP_CONNECT_TIMEOUT = config_module.HTTP_CONNECT_TIMEOUT
HTTP_WRITE_TIMEOUT = config_module.HTTP_WRITE_TIMEOUT
HTTP_POOL_TIMEOUT = config_module.HTTP_POOL_TIMEOUT
MAX_RETRIES = config_module.MAX_RETRIES
BASE_RETRY_DELAY = config_module.BASE_RETRY_DELAY
MAX_CONCURRENT_REQUESTS = config_module.MAX_CONCURRENT_REQUESTS
REQUEST_QUEUE_TIMEOUT = config_module.REQUEST_QUEUE_TIMEOUT
TOKEN_AUTO_REFRESH_ENABLED = config_module.TOKEN_AUTO_REFRESH_ENABLED
TOKEN_AUTO_REFRESH_CHECK_INTERVAL = config_module.TOKEN_AUTO_REFRESH_CHECK_INTERVAL
TOKEN_AUTO_REFRESH_WINDOW = config_module.TOKEN_AUTO_REFRESH_WINDOW
HIDDEN_MODELS = config_module.HIDDEN_MODELS
MODEL_ALIASES = config_module.MODEL_ALIASES
HIDDEN_FROM_LIST = config_module.HIDDEN_FROM_LIST
FALLBACK_MODELS = config_module.FALLBACK_MODELS
VPN_PROXY_URL = config_module.VPN_PROXY_URL
CORS_ALLOWED_ORIGINS = config_module.CORS_ALLOWED_ORIGINS
_warn_timeout_configuration = config_module._warn_timeout_configuration

from kiro.auth import KiroAuthManager
from kiro.cache import ModelInfoCache
from kiro.model_resolver import ModelResolver
from kiro.routes_openai import router as openai_router
from kiro.routes_anthropic import router as anthropic_router
from kiro.exceptions import validation_exception_handler
from kiro.debug_middleware import DebugLoggerMiddleware


def _create_health_counters() -> dict:
    return {
        "requests_total": 0,
        "errors_total": 0,
        "cached_responses": 0,
        "errors_by_type": {
            "auth": 0,
            "rate_limit": 0,
            "timeout": 0,
            "upstream": 0,
            "validation": 0,
            "internal": 0,
        },
    }


async def token_refresh_loop(app: FastAPI) -> None:
    while True:
        try:
            await asyncio.sleep(TOKEN_AUTO_REFRESH_CHECK_INTERVAL)
            auth_manager = getattr(app.state, "auth_manager", None)
            if auth_manager is None or not getattr(auth_manager, "_access_token", None):
                continue

            expires_at = getattr(auth_manager, "_expires_at", None)
            if expires_at is None:
                continue

            expires_in_seconds = (expires_at - datetime.now(timezone.utc)).total_seconds()
            if expires_in_seconds <= TOKEN_AUTO_REFRESH_WINDOW:
                logger.info(
                    f"Token expires in {int(expires_in_seconds)}s; triggering proactive refresh"
                )
                await auth_manager.force_refresh()
        except asyncio.CancelledError:
            logger.debug("Background token refresh loop cancelled")
            raise
        except Exception as e:
            logger.warning(f"Background token refresh loop failed: {e}")


# --- Loguru Configuration ---
logger.remove()
logger.add(
    sys.stderr,
    level=LOG_LEVEL,
    colorize=True,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
)


class InterceptHandler(logging.Handler):
    """
    Intercepts logs from standard logging and redirects them to loguru.
    
    This allows capturing logs from uvicorn, FastAPI and other libraries
    that use standard logging instead of loguru.
    
    Also filters out noisy shutdown-related exceptions (CancelledError, KeyboardInterrupt)
    that are normal during Ctrl+C but uvicorn logs as ERROR.
    """
    
    # Exceptions that are normal during shutdown and should not be logged as errors
    SHUTDOWN_EXCEPTIONS = (
        "CancelledError",
        "KeyboardInterrupt",
        "asyncio.exceptions.CancelledError",
    )
    
    def emit(self, record: logging.LogRecord) -> None:
        # Filter out shutdown-related exceptions that uvicorn logs as ERROR
        # These are normal during Ctrl+C and don't need to spam the console
        if record.exc_info:
            exc_type = record.exc_info[0]
            if exc_type is not None:
                exc_name = exc_type.__name__
                if exc_name in self.SHUTDOWN_EXCEPTIONS:
                    # Suppress the full traceback, just log a simple message
                    logger.info("Server shutdown in progress...")
                    return
        
        # Also filter by message content for cases where exc_info is not set
        msg = record.getMessage()
        if any(exc in msg for exc in self.SHUTDOWN_EXCEPTIONS):
            return
        
        # Get the corresponding loguru level
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        
        # Find the caller frame for correct source display
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging_intercept():
    """
    Configures log interception from standard logging to loguru.
    
    Intercepts logs from:
    - uvicorn (access logs, error logs)
    - uvicorn.error
    - uvicorn.access
    - fastapi
    """
    # List of loggers to intercept
    loggers_to_intercept = [
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "fastapi",
    ]
    
    for logger_name in loggers_to_intercept:
        logging_logger = logging.getLogger(logger_name)
        logging_logger.handlers = [InterceptHandler()]
        logging_logger.propagate = False


# Configure uvicorn/fastapi log interception
setup_logging_intercept()


def configure_proxy_environment(vpn_proxy_url: str) -> None:
    """
    根据配置显式注入代理环境变量。

    Args:
        vpn_proxy_url: 代理地址；为空时不做任何处理
    """
    if not vpn_proxy_url:
        return

    # 没有协议头时统一补成 http://，保持与现有行为兼容。
    proxy_url_with_scheme = vpn_proxy_url if "://" in vpn_proxy_url else f"http://{vpn_proxy_url}"

    # httpx 会自动读取这些环境变量，因此只需要在应用启动前写入一次。
    os.environ["HTTP_PROXY"] = proxy_url_with_scheme
    os.environ["HTTPS_PROXY"] = proxy_url_with_scheme
    os.environ["ALL_PROXY"] = proxy_url_with_scheme

    # 本地回环地址不能走代理，否则会把代理自己的请求再次转发出去。
    no_proxy_hosts = os.environ.get("NO_PROXY", "")
    local_hosts = "127.0.0.1,localhost"
    if no_proxy_hosts:
        os.environ["NO_PROXY"] = f"{no_proxy_hosts},{local_hosts}"
    else:
        os.environ["NO_PROXY"] = local_hosts

    logger.info(f"Proxy configured: {proxy_url_with_scheme}")
    logger.debug(f"NO_PROXY: {os.environ['NO_PROXY']}")


# --- Configuration Validation ---
def validate_configuration() -> None:
    """
    Validates that required configuration is present.
    
    Checks:
    - Either REFRESH_TOKEN, KIRO_CREDS_FILE, or KIRO_CLI_DB_FILE is configured
    - Supports both .env file (local) and environment variables (Docker)
    
    Raises:
        SystemExit: If critical configuration is missing
    """
    errors = []
    
    # Check if .env file exists (optional - can use environment variables)
    env_file = Path(".env")
    
    # Check for credentials (from .env or environment variables)
    has_refresh_token = bool(REFRESH_TOKEN)
    has_creds_file = bool(KIRO_CREDS_FILE)
    has_cli_db = bool(KIRO_CLI_DB_FILE)
    has_proxy_api_key = bool(PROXY_API_KEY and PROXY_API_KEY.strip())

    # 本地代理必须显式设置访问密钥，避免开发时不小心暴露成无保护服务。
    if not has_proxy_api_key:
        errors.append(
            "PROXY_API_KEY is not configured!\n"
            "\n"
            "   Set PROXY_API_KEY in your .env file or process environment.\n"
            "   Example:\n"
            "      PROXY_API_KEY=\"replace-with-your-own-secret\""
        )
    
    # Check if creds file actually exists
    if KIRO_CREDS_FILE:
        creds_path = Path(KIRO_CREDS_FILE).expanduser()
        if not creds_path.exists():
            has_creds_file = False
            logger.warning(f"KIRO_CREDS_FILE not found: {KIRO_CREDS_FILE}")
    
    # Check if CLI database file actually exists
    if KIRO_CLI_DB_FILE:
        cli_db_path = Path(KIRO_CLI_DB_FILE).expanduser()
        if not cli_db_path.exists():
            has_cli_db = False
            logger.warning(f"KIRO_CLI_DB_FILE not found: {KIRO_CLI_DB_FILE}")
    
    # If no credentials found, show helpful error
    if not has_refresh_token and not has_creds_file and not has_cli_db:
        if not env_file.exists():
            # No .env file and no environment variables
            errors.append(
                "No Kiro credentials configured!\n"
                "\n"
                "To get started:\n"
                "1. Create .env file:\n"
                "   cp .env.example .env\n"
                "\n"
                "2. Edit .env and configure your credentials:\n"
                "   2.1. Set you super-secret password as PROXY_API_KEY\n"
                "   2.2. Set your Kiro credentials:\n"
                "      - Option 1: KIRO_CREDS_FILE to your Kiro credentials JSON file\n"
                "      - Option 2: REFRESH_TOKEN from Kiro IDE traffic\n"
                "      - Option 3: KIRO_CLI_DB_FILE to kiro-cli SQLite database\n"
                "\n"
                "Or use environment variables (for Docker):\n"
                "   docker run -e PROXY_API_KEY=\"...\" -e REFRESH_TOKEN=\"...\" ...\n"
                "\n"
                "See README.md for detailed instructions."
            )
        else:
            # .env exists but no credentials configured
            errors.append(
                "No Kiro credentials configured!\n"
                "\n"
                "   Configure one of the following in your .env file:\n"
                "\n"
                "Set you super-secret password as PROXY_API_KEY\n"
                "   PROXY_API_KEY=\"my-super-secret-password-123\"\n"
                "\n"
                "   Option 1 (Recommended): JSON credentials file\n"
                "      KIRO_CREDS_FILE=\"path/to/your/kiro-credentials.json\"\n"
                "\n"
                "   Option 2: Refresh token\n"
                "      REFRESH_TOKEN=\"your_refresh_token_here\"\n"
                "\n"
                "   Option 3: kiro-cli SQLite database (AWS SSO)\n"
                "      KIRO_CLI_DB_FILE=\"~/.local/share/kiro-cli/data.sqlite3\"\n"
                "\n"
                "   See README.md for how to obtain credentials."
            )
    
    # Print errors and exit if any
    if errors:
        logger.error("")
        logger.error("=" * 60)
        logger.error("  CONFIGURATION ERROR")
        logger.error("=" * 60)
        for error in errors:
            for line in error.split('\n'):
                logger.error(f"  {line}")
        logger.error("=" * 60)
        logger.error("")
        sys.exit(1)
    
    # Note: Credential loading details are logged by KiroAuthManager


# --- Lifespan Manager ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the application lifecycle.
    
    Creates and initializes:
    - Shared HTTP client with connection pooling
    - KiroAuthManager for token management
    - ModelInfoCache for model caching
    
    The shared HTTP client is used by all requests to reduce memory usage
    and enable connection reuse. This is especially important for handling
    concurrent requests efficiently (fixes issue #24).
    """
    logger.info("Starting application... Creating state managers.")
    
    # Create shared HTTP client with connection pooling
    # This reduces memory usage and enables connection reuse across requests
    # Limits: max 100 total connections, max 20 keep-alive connections
    limits = httpx.Limits(
        max_connections=HTTP_MAX_CONNECTIONS,
        max_keepalive_connections=HTTP_MAX_KEEPALIVE_CONNECTIONS,
        keepalive_expiry=HTTP_KEEPALIVE_EXPIRY
    )
    # Timeout configuration for streaming (long read timeout for model "thinking")
    timeout = httpx.Timeout(
        connect=HTTP_CONNECT_TIMEOUT,
        read=STREAMING_READ_TIMEOUT,  # 300 seconds for streaming
        write=HTTP_WRITE_TIMEOUT,
        pool=HTTP_POOL_TIMEOUT
    )
    app.state.http_client = httpx.AsyncClient(
        limits=limits,
        timeout=timeout,
        follow_redirects=True
    )
    app.state.started_at = time.time()
    app.state.health_counters = _create_health_counters()
    app.state.http_client_config = {
        "max_connections": HTTP_MAX_CONNECTIONS,
        "max_keepalive_connections": HTTP_MAX_KEEPALIVE_CONNECTIONS,
        "keepalive_expiry_seconds": HTTP_KEEPALIVE_EXPIRY,
        "timeouts": {
            "connect": HTTP_CONNECT_TIMEOUT,
            "read": STREAMING_READ_TIMEOUT,
            "write": HTTP_WRITE_TIMEOUT,
            "pool": HTTP_POOL_TIMEOUT,
        },
        "retry": {
            "max_retries": MAX_RETRIES,
            "base_retry_delay": BASE_RETRY_DELAY,
        },
    }
    logger.info("Shared HTTP client created with connection pooling")

    app.state.request_limiter = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    app.state.request_limiter_limit = MAX_CONCURRENT_REQUESTS
    app.state.request_queue_timeout = REQUEST_QUEUE_TIMEOUT
    logger.info(
        f"Request limiter initialized: max_concurrent={MAX_CONCURRENT_REQUESTS}, "
        f"queue_timeout={REQUEST_QUEUE_TIMEOUT}s"
    )

    # Create AuthManager
    # Priority: SQLite DB > JSON file > environment variables
    app.state.auth_manager = KiroAuthManager(
        refresh_token=REFRESH_TOKEN,
        profile_arn=PROFILE_ARN,
        region=REGION,
        creds_file=KIRO_CREDS_FILE if KIRO_CREDS_FILE else None,
        sqlite_db=KIRO_CLI_DB_FILE if KIRO_CLI_DB_FILE else None,
    )
    app.state.token_refresh_task = None
    
    # Create model cache
    app.state.model_cache = ModelInfoCache()
    
    # BLOCKING: Load models from Kiro API at startup
    # This ensures the cache is populated BEFORE accepting any requests.
    # No race conditions - requests only start after yield.
    logger.info("Loading models from Kiro API...")
    try:
        token = await app.state.auth_manager.get_access_token()
        from kiro.utils import get_kiro_headers
        from kiro.auth import AuthType
        headers = get_kiro_headers(app.state.auth_manager, token)
        
        # Build params - profileArn is only needed for Kiro Desktop auth
        params = {"origin": "AI_EDITOR"}
        if app.state.auth_manager.auth_type == AuthType.KIRO_DESKTOP and app.state.auth_manager.profile_arn:
            params["profileArn"] = app.state.auth_manager.profile_arn
        
        list_models_url = f"{app.state.auth_manager.q_host}/ListAvailableModels"
        logger.debug(f"Fetching models from: {list_models_url}")
        
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                list_models_url,
                headers=headers,
                params=params
            )
            
            if response.status_code == 200:
                response_data = response.json()
                models_list = response_data.get("models", [])
                await app.state.model_cache.update(models_list)
                logger.debug(f"Successfully loaded {len(models_list)} models from Kiro API")
            else:
                raise Exception(f"HTTP {response.status_code}")
    except Exception as e:
        # FALLBACK: Use built-in model list
        logger.error(f"Failed to fetch models from Kiro API: {e}")
        logger.error("Using pre-configured fallback models. Not all models may be available on your plan, or the list may be outdated.")
        
        # Populate cache with fallback models
        await app.state.model_cache.update(FALLBACK_MODELS)
        logger.debug(f"Loaded {len(FALLBACK_MODELS)} fallback models")
    
    # Add hidden models to cache (they appear in /v1/models but not in Kiro API)
    # Hidden models are added ALWAYS, regardless of API success/failure
    for display_name, internal_id in HIDDEN_MODELS.items():
        app.state.model_cache.add_hidden_model(display_name, internal_id)
    
    if HIDDEN_MODELS:
        logger.debug(f"Added {len(HIDDEN_MODELS)} hidden models to cache")
    
    # Log final cache state
    all_models = app.state.model_cache.get_all_model_ids()
    logger.info(f"Model cache ready: {len(all_models)} models total")
    
    # Create model resolver (uses cache + hidden models + aliases for resolution)
    app.state.model_resolver = ModelResolver(
        cache=app.state.model_cache,
        hidden_models=HIDDEN_MODELS,
        aliases=MODEL_ALIASES,
        hidden_from_list=HIDDEN_FROM_LIST
    )
    logger.info("Model resolver initialized")
    
    # Log alias configuration if any
    if MODEL_ALIASES:
        logger.debug(f"Model aliases configured: {list(MODEL_ALIASES.keys())}")
    if HIDDEN_FROM_LIST:
        logger.debug(f"Models hidden from list: {HIDDEN_FROM_LIST}")

    if TOKEN_AUTO_REFRESH_ENABLED:
        app.state.token_refresh_task = asyncio.create_task(token_refresh_loop(app))
        logger.info(
            f"Background token refresh enabled: interval={TOKEN_AUTO_REFRESH_CHECK_INTERVAL}s, "
            f"window={TOKEN_AUTO_REFRESH_WINDOW}s"
        )

    yield
    
    # Graceful shutdown
    logger.info("Shutting down application...")
    token_refresh_task = getattr(app.state, "token_refresh_task", None)
    if token_refresh_task is not None:
        token_refresh_task.cancel()
        try:
            await token_refresh_task
        except asyncio.CancelledError:
            pass
    try:
        await app.state.http_client.aclose()
        logger.info("Shared HTTP client closed")
    except Exception as e:
        logger.warning(f"Error closing shared HTTP client: {e}")


def create_app() -> FastAPI:
    """
    创建并配置 FastAPI 应用实例。

    Returns:
        已完成中间件、异常处理和路由注册的应用实例
    """
    # 无论通过 `python main.py` 还是 `uvicorn main:app` 启动，
    # 都要在创建应用前完成一次配置校验。
    validate_configuration()

    # 代理环境变量必须在任何 httpx client 创建之前完成注入。
    configure_proxy_environment(VPN_PROXY_URL)

    fastapi_app = FastAPI(
        title=APP_TITLE,
        description=APP_DESCRIPTION,
        version=APP_VERSION,
        lifespan=lifespan
    )

    # 仅允许显式配置的本地来源跨域访问，避免默认对所有来源开放。
    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 在 Pydantic 校验前初始化调试日志，便于保留失败请求上下文。
    fastapi_app.add_middleware(DebugLoggerMiddleware)

    fastapi_app.add_exception_handler(RequestValidationError, validation_exception_handler)

    fastapi_app.include_router(openai_router)
    fastapi_app.include_router(anthropic_router)

    return fastapi_app


# --- FastAPI Application ---
app = create_app()


# --- Uvicorn log config ---
# Minimal configuration for redirecting uvicorn logs to loguru.
# Uses InterceptHandler which intercepts logs and passes them to loguru.
UVICORN_LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "default": {
            "class": "main.InterceptHandler",
        },
    },
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.access": {"handlers": ["default"], "level": "INFO", "propagate": False},
    },
}


def parse_cli_args() -> argparse.Namespace:
    """
    Parse command-line arguments for server configuration.
    
    CLI arguments have the highest priority, overriding both
    environment variables and default values.
    
    Returns:
        Parsed arguments namespace with host and port values
    """
    parser = argparse.ArgumentParser(
        description=f"{APP_TITLE} - {APP_DESCRIPTION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Configuration Priority (highest to lowest):
  1. CLI arguments (--host, --port)
  2. Environment variables (SERVER_HOST, SERVER_PORT)
  3. Default values (127.0.0.1:8000)

Examples:
  python main.py                          # Use defaults or env vars
  python main.py --port 9000              # Override port only
  python main.py --host 127.0.0.1         # Local connections only
  python main.py -H 0.0.0.0 -p 8080       # Short form
  
  SERVER_PORT=9000 python main.py         # Via environment
  uvicorn main:app --port 9000            # Via uvicorn directly
        """
    )
    
    parser.add_argument(
        "-H", "--host",
        type=str,
        default=None,  # None means "use env or default"
        metavar="HOST",
        help=f"Server host address (default: {DEFAULT_SERVER_HOST}, env: SERVER_HOST)"
    )
    
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=None,  # None means "use env or default"
        metavar="PORT",
        help=f"Server port (default: {DEFAULT_SERVER_PORT}, env: SERVER_PORT)"
    )
    
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {APP_VERSION}"
    )
    
    return parser.parse_args()


def resolve_server_config(args: argparse.Namespace) -> tuple[str, int]:
    """
    Resolve final server configuration using priority hierarchy.
    
    Priority (highest to lowest):
    1. CLI arguments (--host, --port)
    2. Environment variables (SERVER_HOST, SERVER_PORT)
    3. Default values (127.0.0.1:8000)
    
    Args:
        args: Parsed CLI arguments
        
    Returns:
        Tuple of (host, port) with resolved values
    """
    # Host resolution: CLI > ENV > Default
    if args.host is not None:
        final_host = args.host
        host_source = "CLI argument"
    elif SERVER_HOST != DEFAULT_SERVER_HOST:
        final_host = SERVER_HOST
        host_source = "environment variable"
    else:
        final_host = DEFAULT_SERVER_HOST
        host_source = "default"
    
    # Port resolution: CLI > ENV > Default
    if args.port is not None:
        final_port = args.port
        port_source = "CLI argument"
    elif SERVER_PORT != DEFAULT_SERVER_PORT:
        final_port = SERVER_PORT
        port_source = "environment variable"
    else:
        final_port = DEFAULT_SERVER_PORT
        port_source = "default"
    
    # Log configuration sources for transparency
    logger.debug(f"Host: {final_host} (from {host_source})")
    logger.debug(f"Port: {final_port} (from {port_source})")
    
    return final_host, final_port


def print_startup_banner(host: str, port: int) -> None:
    """
    Print a startup banner with server information.
    
    Args:
        host: Server host address
        port: Server port
    """
    # ANSI color codes
    GREEN = "\033[92m"
    CYAN = "\033[96m"
    YELLOW = "\033[93m"
    WHITE = "\033[97m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"
    
    # 对本机监听地址统一显示 localhost，更符合用户直觉。
    display_host = "localhost" if host in ("0.0.0.0", "127.0.0.1") else host
    url = f"http://{display_host}:{port}"
    
    print()
    print(f"  {WHITE}{BOLD}[Kiro Gateway] {APP_TITLE} v{APP_VERSION}{RESET}")
    print()
    print(f"  {WHITE}Server running at:{RESET}")
    print(f"  {GREEN}{BOLD}->  {url}{RESET}")
    print()
    print(f"  {DIM}API Docs:      {url}/docs{RESET}")
    print(f"  {DIM}Health Check:  {url}/health{RESET}")
    print()
    print(f"  {DIM}{'-' * 48}{RESET}")
    print(f"  {WHITE}Found a bug? Need help? Have questions?{RESET}")
    print(f"  {YELLOW}->  https://github.com/jwadow/kiro-gateway/issues{RESET}")
    print(f"  {DIM}{'-' * 48}{RESET}")
    print()


# --- Entry Point ---
if __name__ == "__main__":
    import uvicorn
    
    # Warn about suboptimal timeout configuration
    _warn_timeout_configuration()
    
    # Parse CLI arguments
    args = parse_cli_args()
    
    # Resolve final configuration with priority hierarchy
    final_host, final_port = resolve_server_config(args)
    
    # Print startup banner
    print_startup_banner(final_host, final_port)
    
    logger.info(f"Starting Uvicorn server on {final_host}:{final_port}...")
    
    # Use string reference to avoid double module import
    uvicorn.run(
        "main:app",
        host=final_host,
        port=final_port,
        log_config=UVICORN_LOG_CONFIG,
    )
