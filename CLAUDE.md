# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Win Kiro Gateway 是一个本地逆向代理，为 Kiro（Amazon Q Developer / AWS CodeWhisperer）提供 OpenAI 和 Anthropic 兼容接口。主要使用场景是 Windows + PowerShell，本地通过 `.env` 配置凭证和网关参数，再用 `run.ps1` 或 `python main.py` 启动代理。

## Common Commands

### Setup
```powershell
python -m pip install -r requirements.txt
```

### Testing
```powershell
# 运行全部测试
python -m pytest

# 仅运行单元测试 / 集成测试
python -m pytest tests/unit -v
python -m pytest tests/integration -v

# 运行单个测试文件
python -m pytest tests/unit/test_routes_openai.py -v

# 运行单个测试
python -m pytest tests/unit/test_routes_openai.py::TestVerifyApiKey::test_valid_bearer_token_returns_true -v

# 首次失败即停止 / 显示 print 输出
python -m pytest -x
python -m pytest -s -v
```

### Running the Gateway
```powershell
# 推荐：使用管理脚本（自动重载、健康检查、运行态文件管理）
.\run.ps1

# 直接运行应用
python main.py

# 使用 CLI 参数覆盖 .env
python main.py --host 127.0.0.1 --port 9000
```

### Docker
```powershell
# 后台启动
Docker Compose up -d

# 查看日志
Docker Compose logs -f

# 停止容器
Docker Compose down
```

## Configuration Notes

- 主配置入口在 `kiro/config.py`，运行时由 `main.py` 先调用 `load_runtime_env()`，避免模块导入阶段被本地 `.env` 污染。
- 配置优先级：CLI 参数 > 环境变量 > `.env` > 默认值。
- 启动前至少要有 `PROXY_API_KEY`，认证信息可来自 `.env`、Kiro JSON 凭证文件或 `kiro-cli` SQLite。
- 所有运行时文件统一放在 `.runtime/`：`run.pid`、`run.state`、`run.out.log`、`run.err.log`。
- Windows 路径类配置（如 `KIRO_CREDS_FILE`、`KIRO_CLI_DB_FILE`）依赖 `config.py:_get_raw_env_value()` 直接读取 `.env` 原文，避免反斜杠转义。

## High-Level Architecture

### Application lifecycle

- `main.py` 创建 FastAPI 应用，初始化 `auth_manager`、共享 `httpx.AsyncClient`、模型缓存、请求限流器和 `/health` 用的运行态计数器。
- 启动阶段会显式配置代理环境变量、预热认证状态和模型列表，并按配置启动后台 token 自动刷新任务。
- `run.ps1` 是 Windows 主工作流入口：负责 `.env` 读取、`.runtime/` 管理、日志轮转、凭证切换检测、健康检查和自动重启。

### Request pipeline

```text
Client Request
  -> routes_openai.py / routes_anthropic.py
  -> converters_core.py + converters_messages.py / converters_content.py / converters_tools.py
  -> request_executor.py
  -> http_client.py
  -> Kiro / GLM upstream
  -> streaming_core.py
  -> streaming_shared.py
  -> streaming_openai.py / streaming_anthropic.py
  -> Client Response
```

- `routes_openai.py` 和 `routes_anthropic.py` 只处理协议差异：鉴权、请求/响应 schema、SSE 输出格式、协议兼容字段。
- 两条路由都复用 `request_executor.py` 中的共享执行链：选择共享或独占 HTTP client、发送上游请求、解析错误、关闭资源。
- 非流式请求复用应用级 `httpx.AsyncClient`；流式请求使用独立 client，避免长连接和断流重试相互污染。

### Conversion layer

- `converters_core.py` 是统一门面，负责把 OpenAI / Anthropic 请求拼成 Kiro payload。
- `converters_messages.py` 处理消息规范化：角色修复、相邻消息合并、必要的降级。
- `converters_content.py` 处理文本、图片、thinking 注入和截断恢复相关提示。
- `converters_tools.py` 处理 tool schema、tool_use / tool_result 转换，以及图片型工具内容的降级。

修改 converters 时保持职责边界，不要把协议层逻辑塞回转换层。

### Authentication and upstream selection

- `auth.py` 的 `KiroAuthManager` 管理 token 生命周期、刷新锁、区域相关 URL 和 `/health` 认证快照。
- `auth_storage.py` 负责从 JSON 凭证、enterprise device registration、`kiro-cli` SQLite 读取和回写认证信息。
- `auth_refresh.py` 分别处理 Kiro Desktop refresh 与 AWS SSO OIDC refresh。
- `UPSTREAM_PROVIDER` 可切换上游；路由和模型列表会根据 provider 选择不同 owner / 描述，但主路径默认还是 Kiro。

### Streaming design

- `streaming_core.py` 把 Kiro 原始事件流解析成统一 `KiroEvent`，并处理首 token 超时、thinking 解析、上游中断分类。
- `streaming_shared.py` 负责 OpenAI / Anthropic 共用的流后处理：tool call 去重、正文截断检测、截断状态保存。
- `streaming_openai.py` 和 `streaming_anthropic.py` 只负责把统一事件重新格式化成各自协议的流式或聚合响应。

如果改 streaming，必须同时检查两个协议分支，而不是只修其中一个 formatter。

### Runtime constraints that matter

- 当前为规避 AWS 连接复用导致的 SSL 问题，keepalive 配置应谨慎修改；相关参数在 `kiro/config.py` 和应用启动的共享 HTTP client 配置里。
- 首 token 超时和整段流中断是两类不同故障：前者允许更激进重试，后者更偏向返回友好错误，避免重复执行完整请求。
- 截断恢复依赖 `truncation_state` 缓存前一次响应中的截断信息，并在下一次请求里注入 synthetic tool_result 或 user message。
- fake reasoning 不是上游原生能力，而是通过 thinking 标签和 `ThinkingParser` 做的兼容层。
- `/health` 只汇总本地状态，不主动探测上游；排查时优先看其中的 auth snapshot、request limiter 和 http client 配置。

## Testing Notes

- 测试框架是 `pytest` + `pytest-asyncio`。
- `tests/conftest.py` 默认阻断真实网络，并预置最小环境变量，避免测试依赖本机 `.env`。
- 许多测试会依赖模块级缓存和配置复位；改动 truncation、streaming、config、auth 时要特别留意测试隔离。
- `pytest.ini` 已固定 `testpaths = tests` 和 `pythonpath = .`，通常直接在仓库根目录运行 pytest 即可。

