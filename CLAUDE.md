# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Win Kiro Gateway is a local reverse proxy for Kiro (Amazon Q Developer / AWS CodeWhisperer) that exposes OpenAI-compatible and Anthropic-compatible APIs. The main workflow is Windows + PowerShell: configure `.env`, then start with `run.ps1` or `python main.py`.

## Common Commands

### Setup
```powershell
python -m pip install -r requirements.txt
```

### Run the gateway
```powershell
.\run.ps1
python main.py
python main.py --host 127.0.0.1 --port 9000
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Tests
```powershell
python -m pytest
python -m pytest tests/unit -v
python -m pytest tests/integration -v
python -m pytest tests/unit/test_routes_openai.py -v
python -m pytest tests/unit/test_routes_openai.py::TestVerifyApiKey::test_valid_bearer_token_returns_true -v
python -m pytest -x
python -m pytest -s -v
```

### Docker
```powershell
docker-compose up -d
docker-compose logs -f
docker-compose down
```

## High-Level Architecture

- `main.py` builds the FastAPI app, wires middleware and routers, loads runtime env before importing the rest of the app, and starts Uvicorn from the CLI entry point.
- `kiro/routes_openai.py` and `kiro/routes_anthropic.py` are protocol adapters only; they keep auth, request/response shapes, and streaming format differences out of the shared core.
- `kiro/converters_core.py` is the main facade for turning OpenAI/Anthropic requests into Kiro payloads. Message normalization lives in `kiro/converters_messages.py`, content handling in `kiro/converters_content.py`, and tool conversion in `kiro/converters_tools.py`.
- `kiro/request_executor.py` owns the shared upstream execution flow: choose shared vs per-request HTTP client, send the upstream request, and normalize upstream errors.
- `kiro/streaming_core.py` parses Kiro SSE into unified events. `kiro/streaming_shared.py` handles shared post-processing, while `kiro/streaming_openai.py` and `kiro/streaming_anthropic.py` format protocol-specific output.
- `kiro/auth.py`, `kiro/auth_storage.py`, and `kiro/auth_refresh.py` manage token lifecycle, credential loading, and refresh paths.
- `kiro/config.py` centralizes environment parsing and runtime settings. CLI args override environment variables, which override `.env`, which override defaults.
- `run.ps1` is the Windows management script for runtime files, log rotation, credential watching, health checks, and auto-restart.

## Configuration Notes

- Startup requires `PROXY_API_KEY`.
- Runtime files live under `.runtime/` (`run.pid`, `run.state`, `run.out.log`, `run.err.log`).
- Windows path settings like `KIRO_CREDS_FILE` and `KIRO_CLI_DB_FILE` are read raw from `.env` to avoid backslash escaping issues.
- Keepalive-related HTTP settings are intentionally conservative because of upstream SSL connection reuse issues.
- `/health` reports local state only; it does not probe upstream services.

## Testing Notes

- Test stack: `pytest` + `pytest-asyncio`.
- `tests/conftest.py` blocks real network calls, so tests stay isolated from external services.
- `pytest.ini` already sets `testpaths = tests` and `pythonpath = .`, so run pytest from the repo root.
- When changing auth, config, truncation, or streaming, run the relevant unit tests plus the affected route/streaming tests.

