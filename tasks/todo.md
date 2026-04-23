# Todo

## 当前任务：项目清理（第一步）

- [ ] 清理本地产物与无关文件夹：`__pycache__/`、`.pytest_cache/`、`pytest-cache-files-77ebf7m8/`、`.crush/`
- [ ] 清理运行期临时文件：`.kiro-gateway*.log`、`.kiro-gateway*.pid`、`.kiro-gateway*.state`
- [x] 清理无关文档，仅保留 `README.md`
- [x] 保留运行必需文件：`main.py`、`kiro/`、`tests/`、`requirements.txt`、`Dockerfile`、`docker-compose.yml`、`kiro-gateway.ps1`、`kiro-gateway-v2.ps1`
- [x] 保留环境与配置文件：`.env`、`.env.example`、`.gitignore`
- [x] 清理完成后验证关键入口仍存在，且仓库结构符合预期
- [x] 调整 `README.md`，改为匹配当前 Windows + PowerShell + 本地代理使用方式

## 当前任务：残留处理与入口整理（第二步）

- [ ] 处理 `pytest-cache-files-77ebf7m8/` 权限阻塞，删除无法访问的测试缓存目录
- [ ] 处理运行中占用的 `.kiro-gateway-v2.err.log` 与 `.kiro-gateway-v2.out.log`
- [ ] 梳理 `kiro-gateway.ps1` 与 `kiro-gateway-v2.ps1` 的职责边界，确定保留入口与兼容策略
- [ ] 整理根目录结构，减少重复入口与无关暴露文件
- [ ] 验证保留脚本仍可启动、停止并完成健康检查

## 当前任务：P0 稳定性改造

- [x] 将配置加载改为显式启动流程，消除 `kiro.config` 的 `.env` 导入副作用
- [x] 在 `main.py` 中引入 `create_app()`，将代理环境配置与应用初始化显式化
- [x] 统一路由层与 converter 的模型解析链路，实际请求复用 `ModelResolver`
- [x] 收紧本地代理默认安全边界：默认本机监听、`PROXY_API_KEY` 必填、CORS 默认不再全开放
- [x] 同步更新 `kiro-gateway-v2.ps1`，在缺少 `PROXY_API_KEY` 时拒绝启动
- [x] 更新相关单元测试并运行全量验证

## 当前任务：P2 稳定性拆分（首轮）

- [x] 拆分 `kiro/converters_core.py`，优先拆出低风险的内容提取、工具转换、消息规范化子模块
- [x] 保持 `build_kiro_payload()` 与现有外部调用接口不变，仅迁移内部实现
- [x] 不在首轮拆分中修改 `auth.py`、`streaming_*`、`parsers.py` 的核心逻辑
- [x] 复用现有 converters 相关测试，验证拆分后行为完全一致

## 当前任务：P2 稳定性拆分（第二轮）

- [x] 拆分 `kiro/auth.py`，优先拆出凭证存储与 refresh helper，保留 `KiroAuthManager` 外部接口不变
- [x] 拆分 `streaming_openai.py` / `streaming_anthropic.py` 的重复结构，优先抽共享状态与后处理 helper
- [x] 不改 `streaming_core.py` 的核心事件解析契约
- [x] 复用 auth / streaming 相关测试，验证拆分后行为完全一致

## 当前任务：README 与目录收口

- [x] 清理根目录无关缓存、临时日志和临时脚本
- [x] 清理根目录旧运行时日志，统一只保留 `.runtime/`
- [x] 更新 `README.md`，只保留 `kiro-gateway-v2.ps1` 作为正式脚本入口
- [x] 在 `README.md` 中补充当前代码分层结构说明
- [x] 更新 `.gitignore`，忽略 `.runtime/` 与本次调试临时文件模式

## 当前任务：P0 稳定性改造

- [ ] 将 `kiro.config` 改为显式环境加载，避免 import-time 读取 `.env`
- [ ] 在 `main.py` 中引入 `create_app()` 与显式代理环境配置，收口启动副作用
- [ ] 统一 `OpenAI/Anthropic` 路由的模型解析链路，路由层先走 `ModelResolver.resolve()`
- [ ] 调整 converter 签名，显式接收已解析后的上游 `model_id`
- [ ] 收紧默认安全边界：默认 host 改为 `127.0.0.1`，`PROXY_API_KEY` 改为必填，默认 CORS 不再放开 `*`
- [ ] 同步更新 `kiro-gateway-v2.ps1`，未配置 `PROXY_API_KEY` 时拒绝启动
- [ ] 运行 `python -m pytest -q`
- [ ] 验证 `/health`、`/v1/models`、`claude-sonnet-4.5`、`glm-5` 调用正常

- [x] 修复 `kiro-gateway-v2.ps1` 后台启动成功却误报失败的问题
- [x] 验证后台启动状态判断改为稳定的就绪检测
- [x] 定位并修复上游 `429` 重试耗尽后被错误映射为 `504 Unknown error` 的问题
- [x] 补充 `KiroHttpClient` 相关单元测试，覆盖限流与服务端错误重试耗尽场景
- [x] 运行目标测试，验证返回码与错误信息符合预期

## Review

- [x] 已删除无关文档与目录：`docs/`、`CLAUDE.md`、`CLA.md`、`CONTRIBUTING.md`、`CONTRIBUTORS.md`
- [x] 已删除辅助与本地目录：`manual_api_test.py`、`.claude/`、`.crush/`、`__pycache__/`、`.pytest_cache/`
- [x] 已删除部分运行期文件：`.kiro-gateway*.pid`、`.kiro-gateway*.state`、旧版 `.kiro-gateway*.log`
- [x] 已更新 `README.md`：移除失效链接，补充 PowerShell 启动方式、本地代理接入方式与当前实测模型说明
- [x] 已更新 `.gitignore`：忽略 `.claude/`、`.crush/`、`.kiro-gateway*.log`、`.kiro-gateway*.pid`、`.kiro-gateway*.state`、`pytest-cache-files-*`
- [ ] 剩余阻塞：`pytest-cache-files-77ebf7m8/` 目录存在权限拒绝，暂未删除
- [ ] 剩余阻塞：运行中的 `.kiro-gateway-v2.err.log` 与 `.kiro-gateway-v2.out.log` 被当前进程占用，暂未删除
- [x] 已完成 P0：`kiro/config.py` 移除导入时 `.env` 副作用，新增显式 `load_runtime_env()`
- [x] 已完成 P0：`main.py` 引入 `create_app()` 与 `configure_proxy_environment()`，并将 CORS 改为显式允许列表
- [x] 已完成 P0：默认监听地址收紧为 `127.0.0.1`，`PROXY_API_KEY` 改为必填配置
- [x] 已完成 P0：OpenAI / Anthropic 路由在执行链路中统一调用 `ModelResolver.resolve()`，并把解析结果传给 converter
- [x] 已完成 P0：`kiro/__init__.py` 去副作用化，避免导入包时提前加载路由和错误配置
- [x] 已完成 P0：`kiro-gateway-v2.ps1` 不再使用内置默认 key，缺少 `PROXY_API_KEY` 时拒绝启动或测试
- [x] 已完成 P0：修复 Windows 默认 `gbk` 控制台下启动横幅的 `UnicodeEncodeError`
- [x] 已验证：`python -m pytest -q` 结果为 `1424 passed, 3 warnings`
- [x] 已验证：临时实例 `http://127.0.0.1:18080` 健康检查通过，`/v1/models`、`claude-sonnet-4.5`、`glm-5` 实际调用成功
- [x] 已完成 P1：新增 `kiro/request_executor.py`，OpenAI / Anthropic 路由共享上游请求执行链
- [x] 已完成 P1：`kiro-gateway-v2.ps1` 运行时文件收口到 `.runtime/`
- [x] 已验证 P1：真实调用 `Start-Gateway` 后 `.runtime/kiro-gateway-v2.pid/.state/.out.log/.err.log` 全部生成，`/health` 返回 `healthy`
- [x] 已完成 P2 首轮：将 `converters_core.py` 中的低风险函数拆分到 `converters_content.py`、`converters_tools.py`、`converters_messages.py`
- [x] 已验证 P2 首轮：`tests/unit/test_converters_core.py`、`test_converters_openai.py`、`test_converters_anthropic.py` 全部通过
- [x] 已验证 P2 首轮：`python -m pytest -q` 结果仍为 `1424 passed, 3 warnings`
- [x] 已完成 P2 第二轮：新增 `kiro/auth_storage.py`、`kiro/auth_refresh.py`，`auth.py` 改为原方法委托
- [x] 已完成 P2 第二轮：新增 `kiro/streaming_shared.py`，抽取 streaming 共享后处理
- [x] 已验证 P2 第二轮：`tests/unit/test_auth_manager.py`、`test_streaming_openai.py`、`test_streaming_anthropic.py`、`test_streaming_core.py` 全部通过
- [x] 已验证 P2 第二轮：`python -m pytest -q` 结果仍为 `1424 passed, 3 warnings`
- [x] 已修复 `kiro-gateway-v2.ps1`：后台启动改为轮询等待 `/health` 就绪，不再固定等待 2 秒后单次判定
- [x] 已修复 PID 文件读取：读取 `.kiro-gateway-v2.pid` 时增加 `Trim()`，避免换行导致进程查询失败
- [x] 已验证：PowerShell 语法解析 `PARSE_OK`；`Test-GatewayHealth` 返回 `true`；`Wait-GatewayReady` 对当前 8000 端口实例返回 `{\"Ready\":true,\"Reason\":\"health_check_passed\",\"ResultPid\":14968}`
- [x] 已修复 `kiro/http_client.py`：`403/429/5xx` 在重试耗尽后返回最后一次真实上游响应，不再伪装成 `504 Unknown error`
- [x] 已补充 `tests/unit/test_http_client.py`：覆盖非流式 `429`、非流式 `503`、流式 `429` 的重试耗尽场景，并校验中间失败响应会被关闭
- [x] 已验证：使用 `D:\Python314\python.exe -m pytest tests\unit\test_http_client.py -q`，结果 `42 passed, 1 warning`
