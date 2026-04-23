<div align="center">

# 👻 Kiro Gateway

**本地 Kiro 逆向代理，提供 OpenAI / Anthropic 兼容接口**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com/)

*当前仓库以 Windows + PowerShell 场景为主，使用 `.env` 和 `kiro-gateway-v2.ps1` 启动本地代理，再将本地 URL 和 key 配置给 Cherry Studio、Cursor、Cline、OpenAI SDK 等兼容工具。*

[Models](#-available-models) • [Features](#-features) • [Quick Start](#-quick-start) • [Configuration](#%EF%B8%8F-configuration)

</div>

---

## 🤖 Available Models

模型以你当前 Kiro 账号和上游实际返回为准，最可靠的查看方式是请求本地代理的 `/v1/models`。

当前仓库实测可见过的模型包括：

- `claude-sonnet-4.5`
- `claude-haiku-4.5`
- `claude-sonnet-4`
- `claude-3.7-sonnet`
- `glm-5`
- `deepseek-3.2`
- `minimax-m2.1`
- `minimax-m2.5`
- `qwen3-coder-next`
- `auto-kiro`

> 💡 **模型名透传与规范化：** 常见 Claude 名称格式会自动规范化；如果上游接受某个模型 ID，也可以直接在客户端里手填模型名进行测试。

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🔌 **OpenAI-compatible API** | Works with any OpenAI-compatible tool |
| 🔌 **Anthropic-compatible API** | Native `/v1/messages` endpoint |
| 🌐 **VPN/Proxy Support** | HTTP/SOCKS5 proxy for restricted networks |
| 🧠 **Extended Thinking** | Reasoning is exclusive to our project |
| 👁️ **Vision Support** | Send images to model |
| 🛠️ **Tool Calling** | Supports function calling |
| 💬 **Full message history** | Passes complete conversation context |
| 📡 **Streaming** | Full SSE streaming support |
| 🔄 **Retry Logic** | Automatic retries on errors (403, 429, 5xx) |
| 📋 **Extended model list** | Including versioned models |
| 🔐 **Smart token management** | Automatic refresh before expiration |

---

## 🚀 Quick Start

推荐直接使用当前仓库里的 PowerShell 管理脚本启动本地代理。

### Prerequisites

- Windows + PowerShell
- Python 3.10+
- 已可用的 Kiro 凭证
- 已配置好的 `.env`

### Installation

```powershell
# 安装依赖
python -m pip install -r requirements.txt

# 如需初始化配置，可参考 .env.example 创建 .env
# 然后在 .env 中至少配置：
# - PROXY_API_KEY
# - SERVER_PORT
# - KIRO_CREDS_FILE 或 REFRESH_TOKEN 或 KIRO_CLI_DB_FILE

# 推荐：使用增强版脚本启动
.\kiro-gateway-v2.ps1

# 如需直接运行应用
python main.py
```

代理启动后，默认地址为 `http://localhost:8000`，并默认只监听本机地址。

脚本运行时文件统一放在仓库根目录下的 `.runtime/`：

- `kiro-gateway-v2.pid`
- `kiro-gateway-v2.state`
- `kiro-gateway-v2.out.log`
- `kiro-gateway-v2.err.log`

### Connect External Tools

外部工具通常按下面方式接入：

- Base URL: `http://localhost:8000/v1`
- API Key: `.env` 中的 `PROXY_API_KEY`
- Model: 例如 `claude-sonnet-4.5`、`glm-5`

Cherry Studio 一类工具如果走 OpenAI 兼容模式，直接填上面三项即可。

---

## 🧱 Current Structure

当前仓库已经整理成下面这几层，后续维护时优先按这个分层理解：

- `main.py`
  应用入口、`create_app()`、生命周期初始化、CLI 启动逻辑。
- `kiro/request_executor.py`
  OpenAI / Anthropic 路由共享的上游请求执行链。
- `kiro/routes_openai.py` / `kiro/routes_anthropic.py`
  协议适配层，只保留各自请求格式、响应格式和鉴权差异。
- `kiro/converters_core.py`
  converter 门面层，保留统一数据结构和主 payload 组装流程。
- `kiro/converters_content.py`
  文本、图片、thinking / truncation prompt 注入辅助。
- `kiro/converters_tools.py`
  tool schema、tool/result/image 转换辅助。
- `kiro/converters_messages.py`
  消息规范化、角色修复、相邻消息合并、tool 内容降级为文本。
- `kiro/auth.py`
  认证入口与 token 生命周期调度。
- `kiro/auth_storage.py`
  JSON / SQLite / enterprise device registration 的读取与保存。
- `kiro/auth_refresh.py`
  Kiro Desktop 和 AWS SSO OIDC 的 refresh 请求逻辑。
- `kiro/streaming_core.py`
  Kiro 事件流解析核心。
- `kiro/streaming_shared.py`
  OpenAI / Anthropic 共享的 streaming 后处理，如 truncation 检测与 recovery state 保存。
- `kiro/streaming_openai.py` / `kiro/streaming_anthropic.py`
  各自协议的流式输出格式化与非流式聚合。
- `.runtime/`
  运行时生成的 pid / state / log 目录，不属于源码。

---

## ⚙️ Configuration

### Option 1: JSON Credentials File (Kiro IDE / Enterprise)

Specify the path to the credentials file:

Works with:
- **Kiro IDE** (standard) - for personal accounts
- **Enterprise** - for corporate accounts with SSO

```env
KIRO_CREDS_FILE="~/.aws/sso/cache/kiro-auth-token.json"

# Password to protect YOUR proxy server (make up any secure string)
# You'll use this as api_key when connecting to your gateway
PROXY_API_KEY="my-super-secret-password-123"
```

<details>
<summary>📄 JSON file format</summary>

```json
{
  "accessToken": "eyJ...",
  "refreshToken": "eyJ...",
  "expiresAt": "2025-01-12T23:00:00.000Z",
  "profileArn": "arn:aws:codewhisperer:us-east-1:...",
  "region": "us-east-1",
  "clientIdHash": "abc123..."  // Optional: for corporate SSO setups
}
```

> **Note:** If you have two JSON files in `~/.aws/sso/cache/` (e.g., `kiro-auth-token.json` and a file with a hash name), use `kiro-auth-token.json` in `KIRO_CREDS_FILE`. The gateway will automatically load the other file.

</details>

### Option 2: Environment Variables (.env file)

Create a `.env` file in the project root:

```env
# Required
REFRESH_TOKEN="your_kiro_refresh_token"

# Password to protect YOUR proxy server (make up any secure string)
PROXY_API_KEY="my-super-secret-password-123"

# Optional
PROFILE_ARN="arn:aws:codewhisperer:us-east-1:..."
KIRO_REGION="us-east-1"
```

### Option 3: AWS SSO Credentials (kiro-cli / Enterprise)

If you use `kiro-cli` or Kiro IDE with AWS SSO (AWS IAM Identity Center), the gateway will automatically detect and use the appropriate authentication.

Works with both free Builder ID accounts and corporate accounts.

```env
KIRO_CREDS_FILE="~/.aws/sso/cache/your-sso-cache-file.json"

# Password to protect YOUR proxy server
PROXY_API_KEY="my-super-secret-password-123"

# Note: PROFILE_ARN is NOT needed for AWS SSO (Builder ID and corporate accounts)
# The gateway will work without it
```

<details>
<summary>📄 AWS SSO JSON file format</summary>

AWS SSO credentials files (from `~/.aws/sso/cache/`) contain:

```json
{
  "accessToken": "eyJ...",
  "refreshToken": "eyJ...",
  "expiresAt": "2025-01-12T23:00:00.000Z",
  "region": "us-east-1",
  "clientId": "...",
  "clientSecret": "..."
}
```

**Note:** AWS SSO (Builder ID and corporate accounts) users do NOT need `profileArn`. The gateway will work without it (if specified, it will be ignored).

</details>

<details>
<summary>🔍 How it works</summary>

The gateway automatically detects the authentication type based on the credentials file:

- **Kiro Desktop Auth** (default): Used when `clientId` and `clientSecret` are NOT present
  - Endpoint: `https://prod.{region}.auth.desktop.kiro.dev/refreshToken`
  
- **AWS SSO (OIDC)**: Used when `clientId` and `clientSecret` ARE present
  - Endpoint: `https://oidc.{region}.amazonaws.com/token`

No additional configuration is needed — just point to your credentials file!

</details>

### Option 4: kiro-cli SQLite Database

If you use `kiro-cli` and prefer to use its SQLite database directly:

```env
KIRO_CLI_DB_FILE="~/.local/share/kiro-cli/data.sqlite3"

# Password to protect YOUR proxy server
PROXY_API_KEY="my-super-secret-password-123"

# Note: PROFILE_ARN is NOT needed for AWS SSO (Builder ID and corporate accounts)
# The gateway will work without it
```

<details>
<summary>📄 Database locations</summary>

| CLI Tool | Database Path |
|----------|---------------|
| kiro-cli | `~/.local/share/kiro-cli/data.sqlite3` |
| amazon-q-developer-cli | `~/.local/share/amazon-q/data.sqlite3` |

The gateway reads credentials from the `auth_kv` table which stores:
- `kirocli:odic:token` or `codewhisperer:odic:token` — access token, refresh token, expiration
- `kirocli:odic:device-registration` or `codewhisperer:odic:device-registration` — client ID and secret

Both key formats are supported for compatibility with different kiro-cli versions.

</details>

### Getting Credentials

**For Kiro IDE users:**
- Log in to Kiro IDE and use Option 1 above (JSON credentials file)
- The credentials file is created automatically after login

**For Kiro CLI users:**
- Log in with `kiro-cli login` and use Option 3 or Option 4 above
- No manual token extraction needed!

<details>
<summary>🔧 Advanced: Manual token extraction</summary>

If you need to manually extract the refresh token (e.g., for debugging), you can intercept Kiro IDE traffic:
- Look for requests to: `prod.us-east-1.auth.desktop.kiro.dev/refreshToken`

</details>

---

## 🐳 Docker Deployment

> 如果你当前主要在 Windows 本机上通过 PowerShell 使用代理，优先使用上面的 Quick Start。Docker 部署仅在你需要隔离运行环境时再使用。

### Quick Start

```bash
# 1. 在当前仓库目录中准备 .env
# 参考 .env.example 补齐配置

# 2. Run with docker-compose
docker-compose up -d

# 3. Check status
docker-compose logs -f
curl http://localhost:8000/health
```

### Docker Run (Without Compose)

<details>
<summary>🔹 Using Environment Variables</summary>

```bash
docker run -d \
  -p 8000:8000 \
  -e PROXY_API_KEY="my-super-secret-password-123" \
  -e REFRESH_TOKEN="your_refresh_token" \
  --name kiro-gateway \
  ghcr.io/jwadow/kiro-gateway:latest
```

</details>

<details>
<summary>🔹 Using Credentials File</summary>

**Linux/macOS:**
```bash
docker run -d \
  -p 8000:8000 \
  -v ~/.aws/sso/cache:/home/kiro/.aws/sso/cache:ro \
  -e KIRO_CREDS_FILE=/home/kiro/.aws/sso/cache/kiro-auth-token.json \
  -e PROXY_API_KEY="my-super-secret-password-123" \
  --name kiro-gateway \
  ghcr.io/jwadow/kiro-gateway:latest
```

**Windows (PowerShell):**
```powershell
docker run -d `
  -p 8000:8000 `
  -v ${HOME}/.aws/sso/cache:/home/kiro/.aws/sso/cache:ro `
  -e KIRO_CREDS_FILE=/home/kiro/.aws/sso/cache/kiro-auth-token.json `
  -e PROXY_API_KEY="my-super-secret-password-123" `
  --name kiro-gateway `
  ghcr.io/jwadow/kiro-gateway:latest
```

</details>

<details>
<summary>🔹 Using .env File</summary>

```bash
docker run -d -p 8000:8000 --env-file .env --name kiro-gateway ghcr.io/jwadow/kiro-gateway:latest
```

</details>

### Docker Compose Configuration

Edit `docker-compose.yml` and uncomment volume mounts for your OS:

```yaml
volumes:
  # Kiro IDE credentials (choose your OS)
  - ~/.aws/sso/cache:/home/kiro/.aws/sso/cache:ro              # Linux/macOS
  # - ${USERPROFILE}/.aws/sso/cache:/home/kiro/.aws/sso/cache:ro  # Windows
  
  # kiro-cli database (choose your OS)
  - ~/.local/share/kiro-cli:/home/kiro/.local/share/kiro-cli:ro  # Linux/macOS
  # - ${USERPROFILE}/.local/share/kiro-cli:/home/kiro/.local/share/kiro-cli:ro  # Windows
  
  # Debug logs (optional)
  - ./debug_logs:/app/debug_logs
```

### Management Commands

```bash
docker-compose logs -f      # View logs
docker-compose restart      # Restart
docker-compose down         # Stop
docker-compose pull && docker-compose up -d  # Update
```

<details>
<summary>🔧 Building from Source</summary>

```bash
docker build -t kiro-gateway .
docker run -d -p 8000:8000 --env-file .env kiro-gateway
```

</details>

---

## 🌐 VPN/Proxy Support

**For users in China, corporate networks, or regions with connectivity issues to AWS services.**

The gateway supports routing all Kiro API requests through a VPN or proxy server. This is essential if you experience connection problems to AWS endpoints or need to use a corporate proxy.

### Configuration

Add to your `.env` file:

```env
# HTTP proxy
VPN_PROXY_URL=http://127.0.0.1:7890

# SOCKS5 proxy
VPN_PROXY_URL=socks5://127.0.0.1:1080

# With authentication (corporate proxies)
VPN_PROXY_URL=http://username:password@proxy.company.com:8080

# Without protocol (defaults to http://)
VPN_PROXY_URL=192.168.1.100:8080
```

### Supported Protocols

- ✅ **HTTP** — Standard proxy protocol
- ✅ **HTTPS** — Secure proxy connections
- ✅ **SOCKS5** — Advanced proxy protocol (common in VPN software)
- ✅ **Authentication** — Username/password embedded in URL

### When You Need This

| Situation | Solution |
|-----------|----------|
| Connection timeouts to AWS | Use VPN/proxy to route traffic |
| Corporate network restrictions | Configure your company's proxy |
| Regional connectivity issues | Use a VPN service with proxy support |
| Privacy requirements | Route through your own proxy server |

### Popular VPN Software with Proxy Support

Most VPN clients provide a local proxy server you can use:
- **Sing-box** — Modern VPN client with HTTP/SOCKS5 proxy
- **Clash** — Usually runs on `http://127.0.0.1:7890`
- **V2Ray** — Configurable SOCKS5/HTTP proxy
- **Shadowsocks** — SOCKS5 proxy support
- **Corporate VPN** — Check your IT department for proxy settings

Leave `VPN_PROXY_URL` empty (default) if you don't need proxy support.

---

## 📡 API Reference

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check |
| `/health` | GET | Detailed health check |
| `/v1/models` | GET | List available models |
| `/v1/chat/completions` | POST | OpenAI Chat Completions API |
| `/v1/messages` | POST | Anthropic Messages API |

---

## 💡 Usage Examples

### OpenAI API

<details>
<summary>🔹 Simple cURL Request</summary>

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer my-super-secret-password-123" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'
```

> **Note:** Replace `my-super-secret-password-123` with the `PROXY_API_KEY` you set in your `.env` file.

</details>

<details>
<summary>🔹 Streaming Request</summary>

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer my-super-secret-password-123" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "What is 2+2?"}
    ],
    "stream": true
  }'
```

</details>

<details>
<summary>🛠️ With Tool Calling</summary>

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer my-super-secret-password-123" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "messages": [{"role": "user", "content": "What is the weather in London?"}],
    "tools": [{
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get weather for a location",
        "parameters": {
          "type": "object",
          "properties": {
            "location": {"type": "string", "description": "City name"}
          },
          "required": ["location"]
        }
      }
    }]
  }'
```

</details>

<details>
<summary>🐍 Python OpenAI SDK</summary>

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="my-super-secret-password-123"  # Your PROXY_API_KEY from .env
)

response = client.chat.completions.create(
    model="claude-sonnet-4-5",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"}
    ],
    stream=True
)

for chunk in response:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

</details>

<details>
<summary>🦜 LangChain</summary>

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://localhost:8000/v1",
    api_key="my-super-secret-password-123",  # Your PROXY_API_KEY from .env
    model="claude-sonnet-4-5"
)

response = llm.invoke("Hello, how are you?")
print(response.content)
```

</details>

### Anthropic API

<details>
<summary>🔹 Simple cURL Request</summary>

```bash
curl http://localhost:8000/v1/messages \
  -H "x-api-key: my-super-secret-password-123" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

> **Note:** Anthropic API uses `x-api-key` header instead of `Authorization: Bearer`. Both are supported.

</details>

<details>
<summary>🔹 With System Prompt</summary>

```bash
curl http://localhost:8000/v1/messages \
  -H "x-api-key: my-super-secret-password-123" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "max_tokens": 1024,
    "system": "You are a helpful assistant.",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

> **Note:** In Anthropic API, `system` is a separate field, not a message.

</details>

<details>
<summary>📡 Streaming</summary>

```bash
curl http://localhost:8000/v1/messages \
  -H "x-api-key: my-super-secret-password-123" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "max_tokens": 1024,
    "stream": true,
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

</details>

<details>
<summary>🐍 Python Anthropic SDK</summary>

```python
import anthropic

client = anthropic.Anthropic(
    api_key="my-super-secret-password-123",  # Your PROXY_API_KEY from .env
    base_url="http://localhost:8000"
)

# Non-streaming
response = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.content[0].text)

# Streaming
with client.messages.stream(
    model="claude-sonnet-4-5",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}]
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

</details>

---

## 🔧 Debugging

Debug logging is **disabled by default**. To enable, add to your `.env`:

```env
# Debug logging mode:
# - off: disabled (default)
# - errors: save logs only for failed requests (4xx, 5xx) - recommended for troubleshooting
# - all: save logs for every request (overwrites on each request)
DEBUG_MODE=errors
```

### Debug Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `off` | Disabled (default) | Production |
| `errors` | Save logs only for failed requests (4xx, 5xx) | **Recommended for troubleshooting** |
| `all` | Save logs for every request | Development/debugging |

### Debug Files

When enabled, requests are logged to the `debug_logs/` folder:

| File | Description |
|------|-------------|
| `request_body.json` | Incoming request from client (OpenAI format) |
| `kiro_request_body.json` | Request sent to Kiro API |
| `response_stream_raw.txt` | Raw stream from Kiro |
| `response_stream_modified.txt` | Transformed stream (OpenAI format) |
| `app_logs.txt` | Application logs for the request |
| `error_info.json` | Error details (only on errors) |

---

## 📜 License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

This means:
- ✅ You can use, modify, and distribute this software
- ✅ You can use it for commercial purposes
- ⚠️ **You must disclose source code** when you distribute the software
- ⚠️ **Network use is distribution** — if you run a modified version on a server and let others interact with it, you must make the source code available to them
- ⚠️ Modifications must be released under the same license

See the [LICENSE](LICENSE) file for the full license text.

### Why AGPL-3.0?

AGPL-3.0 ensures that improvements to this software benefit the entire community. If you modify this gateway and deploy it as a service, you must share your improvements with your users.

---

## ⚠️ Disclaimer

This repository is an unofficial local proxy project for personal deployment and integration testing. It is not affiliated with, endorsed by, or backed by Amazon Web Services (AWS), Anthropic, Kiro IDE, or any other model provider. Use it at your own risk and ensure your usage complies with the relevant upstream terms.

---

<div align="center">

**[⬆ Back to Top](#-kiro-gateway)**

</div>
# win-kiro-gateway
