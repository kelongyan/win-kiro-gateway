# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Win Kiro Gateway 是一个本地逆向代理，为 Kiro (Amazon Q Developer / AWS CodeWhisperer) 提供 OpenAI 和 Anthropic 兼容接口。主要运行环境为 Windows + PowerShell。

## Common Commands

### Testing
```powershell
# 运行所有测试
python -m pytest

# 运行单个测试文件
python -m pytest tests/unit/test_routes_openai.py

# 运行特定测试
python -m pytest tests/unit/test_routes_openai.py::TestVerifyApiKey::test_valid_bearer_token_returns_true

# 带详细输出
python -m pytest -v

# 显示 print 输出
python -m pytest -s
```

### Running the Server
```powershell
# 推荐：使用管理脚本（提供交互菜单、自动重载、健康检查）
.\run.ps1

# 直接运行（用于调试）
python main.py

# 指定端口
python main.py --port 9000

# 前台模式（实时日志）
python main.py  # 或在 run.ps1 菜单中选择 F
```

### Configuration
- 主配置文件：`.env`（参考 `.env.example`）
- 运行时文件：`.runtime/` 目录（pid、state、logs）
- 必须配置 `PROXY_API_KEY` 才能启动

## Architecture

### Request Flow (核心数据流)

```
Client Request
    ↓
routes_openai.py / routes_anthropic.py  ← 协议适配层（OpenAI/Anthropic 格式差异）
    ↓
converters_core.py                      ← 统一转换门面
    ├─ converters_messages.py           ← 消息规范化、角色修复、合并
    ├─ converters_content.py            ← 文本/图片/thinking 注入
    └─ converters_tools.py              ← tool schema 转换
    ↓
request_executor.py                     ← 共享请求执行链（重试、限流）
    ↓
Kiro API (AWS)
    ↓
streaming_core.py                       ← Kiro 事件流解析
    ↓
streaming_shared.py                     ← 共享后处理（truncation 检测）
    ↓
streaming_openai.py / streaming_anthropic.py  ← 协议格式化
    ↓
Client Response
```

### Key Design Patterns

1. **协议适配分层**：`routes_*.py` 只处理协议差异（请求格式、鉴权、响应格式），业务逻辑在 `request_executor.py` 统一处理

2. **Converter 职责分离**：
   - `converters_core.py`：门面，组装完整 payload
   - `converters_messages.py`：消息规范化（角色修复、合并、降级）
   - `converters_content.py`：内容处理（文本、图片、thinking/truncation prompt 注入）
   - `converters_tools.py`：tool 转换（schema、tool_use、tool_result、图片降级）

3. **认证三层结构**：
   - `auth.py`：认证入口与 token 生命周期调度
   - `auth_storage.py`：多源读取（JSON / SQLite / enterprise device registration）
   - `auth_refresh.py`：刷新逻辑（Kiro Desktop / AWS SSO OIDC）

4. **Streaming 分层**：
   - `streaming_core.py`：Kiro 原始事件流解析（与协议无关）
   - `streaming_shared.py`：共享后处理（truncation 检测、recovery state）
   - `streaming_openai.py` / `streaming_anthropic.py`：协议特定格式化

### Important Constraints

1. **HTTP 连接池配置**：当前完全禁用 keepalive（`HTTP_MAX_KEEPALIVE_CONNECTIONS=0`）以避免 AWS 连接复用导致的 SSL 错误。修改连接池配置时需谨慎测试。

2. **Tool Description 长度限制**：Kiro API 对 tool description 有长度限制（默认 10000 字符）。超长描述会被移到 system prompt 中，并在 tool schema 里留下引用。见 `TOOL_DESCRIPTION_MAX_LENGTH` 配置。

3. **Truncation Recovery**：当检测到 Kiro API 截断响应时，会自动注入合成消息（synthetic tool_result 或 user message）帮助模型理解截断。见 `TRUNCATION_RECOVERY` 配置。

4. **Fake Reasoning**：通过注入 `<thinking_mode>enabled</thinking_mode>` 标签实现"伪"扩展思考。这不是原生 API 支持，而是 prompt 注入 hack。见 `FAKE_REASONING_ENABLED` 配置。

5. **Windows 路径处理**：`.env` 中的 Windows 路径（如 `D:\Projects\file.json`）需要特殊处理以避免反斜杠被解释为转义序列。`config.py` 中的 `_get_raw_env_value()` 处理此问题。

### Testing Patterns

- 测试使用 `pytest` + `pytest-asyncio`
- Mock 对象使用 `unittest.mock.AsyncMock` 和 `Mock`
- FastAPI 测试使用 `TestClient`
- 测试文件命名：`test_*.py`
- 测试类命名：`Test*`
- 测试方法命名：`test_*`

### Configuration Priority

1. CLI 参数（`--host`, `--port`）
2. 环境变量（`SERVER_HOST`, `SERVER_PORT`）
3. `.env` 文件
4. 默认值

### Runtime Files

所有运行时文件统一放在 `.runtime/` 目录：
- `run.pid`：进程 PID
- `run.state`：账号状态和日志
- `run.out.log` / `run.err.log`：stdout/stderr 日志

不要将运行时文件放在仓库根目录。

## Development Notes

- 修改 `converters_*.py` 时注意保持职责分离，不要让单个模块承担过多转换逻辑
- 修改 streaming 相关代码时，确保 OpenAI 和 Anthropic 两个协议都能正常工作
- 添加新的环境变量时，在 `config.py` 中添加解析逻辑和文档注释
- 修改认证逻辑时，确保支持所有三种认证方式（JSON / SQLite / env vars）
