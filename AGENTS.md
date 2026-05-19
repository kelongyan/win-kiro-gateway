# AGENTS.md

Win Kiro Gateway 的 OpenCode 代理指令。架构细节见 `CLAUDE.md`。

## 环境与工作流

**主要场景**：Windows + PowerShell  
**运行时文件**：`.runtime/` (pid, state, logs)  
**配置文件**：`.env` (必须显式配置 `PROXY_API_KEY`)

### 常用命令

```powershell
# 安装依赖
python -m pip install -r requirements.txt

# 启动（推荐）
.\run.ps1

# 直接启动
python main.py
python main.py --host 127.0.0.1 --port 9000

# 测试
python -m pytest
python -m pytest tests/unit -v
python -m pytest tests/integration -v
python -m pytest tests/unit/test_routes_openai.py::TestVerifyApiKey::test_valid_bearer_token_returns_true -v
python -m pytest -x  # 遇到第一个失败就停止
python -m pytest -s -v  # 显示 print 输出

# Docker
docker-compose up -d
docker-compose logs -f
docker-compose down
```

**配置优先级**：CLI 参数 > 环境变量 > `.env` > 默认值

## 配置要点

### 必需配置

- `PROXY_API_KEY`：本地代理访问密钥（必须显式设置，无默认值）
- 凭证来源（四选一）：
  - `KIRO_CREDS_FILE`：JSON 凭证文件路径
  - `REFRESH_TOKEN`：直接提供 refresh token
  - `KIRO_CLI_DB_FILE`：kiro-cli SQLite 数据库路径
  - AWS SSO cache 文件

### Windows 路径处理

Windows 路径配置（如 `KIRO_CREDS_FILE`、`KIRO_CLI_DB_FILE`）从 `.env` 读取时保持原样，避免反斜杠转义问题。

示例：
```env
KIRO_CREDS_FILE=C:\Users\Administrator\.aws\sso\cache\kiro-auth-token.json
```

## 测试规范

### 运行测试

- **从仓库根目录运行**：`pytest.ini` 已配置 `testpaths = tests` 和 `pythonpath = .`
- **网络隔离**：`tests/conftest.py` 全局阻止真实网络调用
- **测试栈**：`pytest` + `pytest-asyncio`

### 测试隔离

所有测试必须与外部服务隔离：
- `conftest.py` 的 `block_all_network_calls` fixture 全局拦截 `httpx.AsyncClient`
- 测试尝试真实网络请求会抛出 `RuntimeError`
- 修改 auth、config、truncation、streaming 时，运行相关单元测试 + 受影响的路由/流式测试

### 测试命令模式

```powershell
# 单个测试文件
python -m pytest tests/unit/test_routes_openai.py -v

# 单个测试类
python -m pytest tests/unit/test_routes_openai.py::TestVerifyApiKey -v

# 单个测试方法
python -m pytest tests/unit/test_routes_openai.py::TestVerifyApiKey::test_valid_bearer_token_returns_true -v

# 遇到第一个失败就停止
python -m pytest -x

# 显示 print 输出（调试用）
python -m pytest -s -v
```

## 架构速查

详细架构见 `CLAUDE.md`，这里只列关键点：

- **入口**：`main.py` 构建 FastAPI app，加载运行时环境，启动 Uvicorn
- **协议适配**：`routes_openai.py`、`routes_anthropic.py` 只处理协议差异
- **核心转换**：`converters_core.py` 是主门面，消息规范化在 `converters_messages.py`
- **上游执行**：`request_executor.py` 处理共享 HTTP 客户端、上游请求、错误规范化
- **流式处理**：`streaming_core.py` 解析 Kiro SSE，`streaming_openai.py`/`streaming_anthropic.py` 格式化输出
- **认证**：`auth.py`、`auth_storage.py`、`auth_refresh.py` 管理 token 生命周期
- **配置**：`config.py` 集中环境解析和运行时设置

## 配置与运行时

### 启动要求

- 启动前必须配置 `PROXY_API_KEY`
- 运行时文件位于 `.runtime/`：`run.pid`、`run.state`、`run.out.log`、`run.err.log`

### 健康检查

- `/health` 只报告本地状态，不探测上游服务
- 包含 token 到期状态、刷新失败次数、错误分类统计、HTTP 客户端配置

### HTTP 客户端设置

Keepalive 相关设置故意保守，因为上游 SSL 连接复用存在问题。

## 常见陷阱

1. **Windows 命令**：默认提供 PowerShell 命令，不要用 Bash 语法
2. **测试网络隔离**：所有测试必须 mock httpx 调用，否则会失败
3. **配置优先级**：CLI 参数 > 环境变量 > `.env` > 默认值
4. **路径配置**：Windows 路径在 `.env` 中保持原样，不需要转义反斜杠
5. **运行时文件**：不要手动创建 `.runtime/` 下的文件，由 `run.ps1` 管理
6. **PROXY_API_KEY**：必须显式配置，无默认值，避免误暴露无保护服务

## 修改指南

### 修改认证逻辑

1. 修改 `auth.py`、`auth_storage.py` 或 `auth_refresh.py`
2. 运行 `python -m pytest tests/unit/test_auth*.py -v`
3. 运行受影响的路由测试

### 修改配置

1. 修改 `config.py`
2. 运行 `python -m pytest tests/unit/test_config.py -v`
3. 检查依赖该配置的模块测试

### 修改流式处理

1. 修改 `streaming_*.py`
2. 运行 `python -m pytest tests/unit/test_streaming*.py -v`
3. 运行集成测试 `python -m pytest tests/integration -v`

### 修改转换逻辑

1. 修改 `converters_*.py`
2. 运行 `python -m pytest tests/unit/test_converters*.py -v`
3. 运行路由测试验证端到端行为

## 参考

- **架构与命令详情**：`CLAUDE.md`
- **配置示例**：`.env.example`
- **用户文档**：`README.md`
- **测试 fixtures**：`tests/conftest.py`
