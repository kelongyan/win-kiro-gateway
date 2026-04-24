# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

Win Kiro Gateway 是一个本地 FastAPI 代理，将客户端的 OpenAI Chat Completions API 和 Anthropic Messages API 请求转换为 Kiro / Amazon Q Developer 上游请求，并把 Kiro 的 AWS event stream 响应再转换回对应协议格式。

当前仓库主要面向 Windows + PowerShell 本地使用场景，推荐通过 `.env` 配置凭证和代理密钥，通过 `run.ps1` 启动本地服务。

## 常用命令

### 安装依赖

```powershell
python -m pip install -r requirements.txt
```

测试依赖已包含在 `requirements.txt` 中：`pytest`、`pytest-asyncio`、`hypothesis`。

### 运行服务

```powershell
# 推荐的 Windows PowerShell 启动方式（自动管理进程、日志和状态）
.\run.ps1

# 直接运行应用
python main.py

# 指定 host / port（优先级：CLI 参数 > 环境变量 > 默认值）
python main.py --host 127.0.0.1 --port 9000
python main.py -H 0.0.0.0 -p 8080

# 使用 uvicorn
uvicorn main:app --host 127.0.0.1 --port 8000
```

服务默认监听 `127.0.0.1:8000`。运行前 `.env` 至少需要配置 `PROXY_API_KEY`，并配置一种 Kiro 凭证来源：`KIRO_CREDS_FILE`、`REFRESH_TOKEN` 或 `KIRO_CLI_DB_FILE`。

### 运行测试

```powershell
# 全量测试
pytest

# 详细输出
pytest -v

# 只跑单元测试
pytest tests/unit/ -v

# 只跑集成测试
pytest tests/integration/ -v

# 跑单个测试文件
pytest tests/unit/test_auth_manager.py -v

# 跑单个测试用例
pytest tests/unit/test_auth_manager.py::TestKiroAuthManagerInitialization::test_initialization_stores_credentials -v

# 遇到首个失败即停止
pytest -x

# 显示失败时局部变量
pytest -l
```

### 覆盖率

```powershell
python -m pip install pytest-cov
pytest --cov=kiro --cov-report=html
start htmlcov/index.html
```

### Docker

```powershell
# Compose 启动
Docker Compose up -d

# 查看日志
docker-compose logs -f

# 构建镜像
docker build -t win-kiro-gateway .
```

`docker-compose.yml` 通过 `env_file: .env` 读取配置，并默认映射 `8000:8000`。

### Lint / typecheck

仓库当前没有 `pyproject.toml`、`setup.cfg` 或其它 lint/typecheck 配置文件，也没有声明 mypy/ruff/black 等命令。修改后优先运行相关 pytest。

## 高层架构

### 应用入口与生命周期

- `main.py` 负责显式加载 `.env`、校验配置、注入代理环境变量、创建 FastAPI app、注册中间件和路由。
- `create_app()` 注册 CORS、`DebugLoggerMiddleware`、Pydantic 校验异常处理器，以及 OpenAI / Anthropic 两套路由。
- `lifespan()` 创建应用级共享 `httpx.AsyncClient`、`KiroAuthManager`、`ModelInfoCache`、`ModelResolver` 和请求并发限制器（`asyncio.Semaphore`）。启动时会请求 Kiro `/ListAvailableModels` 填充模型缓存，失败时使用 `FALLBACK_MODELS`。
- 并发限制通过 `MAX_CONCURRENT_REQUESTS` 和 `REQUEST_QUEUE_TIMEOUT` 配置，避免同时发起过多上游请求。

### 配置

- `kiro/config.py` 集中读取环境变量、默认值、模型别名、隐藏模型、fallback 模型、超时、debug、fake reasoning、truncation recovery 等配置。
- `load_runtime_env()` 只在应用启动阶段显式调用，避免普通模块导入和测试被本地 `.env` 污染。
- Windows 路径相关配置（如 `KIRO_CREDS_FILE`、`KIRO_CLI_DB_FILE`）通过原始 `.env` 读取逻辑处理，避免反斜杠转义问题。
- 支持四种凭证来源：`KIRO_CREDS_FILE`（JSON 凭证文件）、`REFRESH_TOKEN`（直接配置 token）、`KIRO_CLI_DB_FILE`（kiro-cli SQLite 数据库）、或 AWS SSO 凭证。
- 稳定性相关参数可通过环境变量覆盖，包括主动 token 刷新（`TOKEN_AUTO_REFRESH_*`）、共享连接池（`HTTP_*`）以及重试参数（`MAX_RETRIES`、`BASE_RETRY_DELAY`）。

### 路由层

- `kiro/routes_openai.py` 提供 `/`、`/health`、`/v1/models`、`/v1/chat/completions`，使用 `Authorization: Bearer <PROXY_API_KEY>` 鉴权。
- `kiro/routes_anthropic.py` 提供 `/v1/messages`，支持 `x-api-key` 和 `Authorization: Bearer` 鉴权。
- 两套路由的主流程相同：鉴权 → truncation recovery 消息修正 → 生成 conversation ID → `ModelResolver.resolve()` → converter 构造 Kiro payload → `request_executor.execute_kiro_request()` → streaming/non-streaming 响应转换。
- `/health` 只返回本地运行快照，不主动请求上游；其中包含 auth 生命周期状态、错误分类计数和共享 HTTP client 配置。

### 认证与上游请求

- `kiro/auth.py` 的 `KiroAuthManager` 管理 token 生命周期，支持 Kiro Desktop 凭证、JSON 凭证文件和 kiro-cli / Amazon Q SQLite 数据库。
- `kiro/auth_storage.py` 负责 JSON / SQLite / enterprise device registration 的读写。
- `kiro/auth_refresh.py` 负责 Kiro Desktop refresh endpoint 和 AWS SSO OIDC token refresh。
- `kiro/http_client.py` 封装 Kiro 上游请求，处理 403 token refresh、429 / 5xx 指数退避、网络错误分类和流式超时配置。
- `kiro/request_executor.py` 抽出路由共享的上游执行逻辑：流式请求使用独立 client，非流式请求复用应用级共享 client；非 200 响应通过 `kiro_errors.enhance_kiro_error()` 转成用户友好错误。

### 模型解析

- `kiro/model_resolver.py` 是模型解析入口，按 alias → 名称规范化 → 动态缓存 → hidden models → passthrough 顺序解析。
- 设计原则是 gateway 而不是 gatekeeper：未知模型不会本地拒绝，而是规范化后透传给 Kiro，让上游决定是否可用。
- `/v1/models` 使用 `ModelResolver.get_available_models()` 汇总动态缓存、hidden models 和 alias，并过滤 `HIDDEN_FROM_LIST`。

### 协议转换

- `kiro/converters_openai.py` 和 `kiro/converters_anthropic.py` 是协议适配层，分别把 OpenAI / Anthropic 请求转换成统一内部格式。
- `kiro/converters_core.py` 是核心 payload 构造层，使用 `UnifiedMessage` / `UnifiedTool` 组装 Kiro `conversationState`，并处理系统提示、工具描述过长、消息角色规范化、相邻消息合并、图片、tool results、fake reasoning 和 truncation recovery 注入。
- `kiro/converters_content.py`、`kiro/converters_tools.py`、`kiro/converters_messages.py` 分别拆分内容、工具和消息规范化辅助逻辑。

### Streaming 与非流式响应

- Kiro 上游始终以流式方式请求；非流式 API 响应是本地收集完整 stream 后聚合生成。
- `kiro/streaming_core.py` 解析 Kiro AWS event stream，输出协议无关的 `KiroEvent`，并集中处理 first-token timeout、thinking parser、usage/context usage。
- `kiro/streaming_openai.py` 将 `KiroEvent` 转成 OpenAI SSE chunk 或最终 Chat Completions 响应。
- `kiro/streaming_anthropic.py` 将 `KiroEvent` 转成 Anthropic Messages SSE 事件或最终 Messages 响应。
- `kiro/streaming_shared.py` 存放 OpenAI / Anthropic 共享的截断检测和 truncation state 保存逻辑。

### 测试约定

- `pytest.ini` 设置 `testpaths = tests` 和 `pythonpath = .`。
- `tests/conftest.py` 中的全局 fixture 会阻断真实网络请求，测试应保持 100% 网络隔离。
- 测试目录分为 `tests/unit/` 和 `tests/integration/`；新增测试应复用现有 pytest 风格，并优先 mock 外部 API。

## 维护注意事项

- 不要在模块导入阶段自动读取 `.env`；运行时环境加载应保持在 `main.py` 启动路径中。
- 路由层负责统一模型解析，converter 仅在未传入已解析模型 ID 时保留兼容性 fallback，避免展示层和执行层模型解析分叉。
- 流式请求的连接释放很重要：上游非 200 响应、重试响应和正常/异常结束的 stream 都要正确关闭。
- 修改 OpenAI 和 Anthropic 协议行为时，通常需要同步检查 converter、route、streaming 和对应测试，避免两套协议能力不一致。
- debug 日志默认关闭；排查请求转换或上游错误时可通过 `.env` 设置 `DEBUG_MODE=errors` 或 `DEBUG_MODE=all`。