#Requires -Version 5.1
<#
.SYNOPSIS
    Kiro Gateway - 增强版管理脚本，支持自动重载。
.DESCRIPTION
    检测账号变化，自动重载凭证，管理网关生命周期。

    主要功能:
    - 自动监控凭证文件
    - 检测到账号切换时自动重启
    - 后台凭证监控
    - 账号变化通知
.EXAMPLE
    .\run.ps1
#>

$ErrorActionPreference = "Continue"

$Script:GatewayDir = $PSScriptRoot
$Script:EnvFile = Join-Path $Script:GatewayDir ".env"
$Script:CredsFile = Join-Path $env:USERPROFILE ".aws\sso\cache\kiro-auth-token.json"
$Script:RuntimeDir = Join-Path $Script:GatewayDir ".runtime"
$Script:PidFile = Join-Path $Script:RuntimeDir "run.pid"
$Script:StateFile = Join-Path $Script:RuntimeDir "run.state"
$Script:OutLogFile = Join-Path $Script:RuntimeDir "run.out.log"
$Script:ErrLogFile = Join-Path $Script:RuntimeDir "run.err.log"
$Script:WatcherJobName = "KiroGateway-CredWatcher"
$Script:HealthCheckFailures = 0
$Script:HealthCheckFailureThreshold = 5
$Script:LogRotateMaxSizeMB = 5
$Script:LogRotateKeepCount = 5
$Script:GatewayPort = 8000
$Script:ApiKey = ""
$Script:AutoReloadEnabled = $true
$Script:CheckIntervalSeconds = 10

function Ensure-RuntimeDirectory {
    # 所有运行期文件统一收口到 .runtime 目录，避免污染仓库根目录。
    if (-not (Test-Path -LiteralPath $Script:RuntimeDir)) {
        New-Item -ItemType Directory -Path $Script:RuntimeDir -Force | Out-Null
    }
}

function Write-StateLog {
    param([string]$Message)

    Ensure-RuntimeDirectory
    $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -LiteralPath $Script:StateFile -Value "[$timestamp] $Message" -Encoding UTF8
}

function Rotate-LogFile {
    param(
        [string]$FilePath,
        [string]$Label
    )

    if (-not (Test-Path -LiteralPath $FilePath)) {
        return
    }

    $fileItem = Get-Item -LiteralPath $FilePath -ErrorAction SilentlyContinue
    if (-not $fileItem) {
        return
    }

    $maxBytes = $Script:LogRotateMaxSizeMB * 1MB
    if ($fileItem.Length -lt $maxBytes) {
        return
    }

    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $archivePath = "$FilePath.$timestamp"
    Move-Item -LiteralPath $FilePath -Destination $archivePath -Force
    Write-StateLog "$Label 日志已轮转: $(Split-Path -Leaf $archivePath)"

    $archives = Get-ChildItem -Path "$FilePath.*" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending
    if ($archives) {
        $archives | Select-Object -Skip $Script:LogRotateKeepCount | Remove-Item -Force -ErrorAction SilentlyContinue
    }
}

function Rotate-LogsIfNeeded {
    Ensure-RuntimeDirectory
    Rotate-LogFile -FilePath $Script:OutLogFile -Label "stdout"
    Rotate-LogFile -FilePath $Script:ErrLogFile -Label "stderr"
}

function Load-Config {
    $Script:ApiKey = ""
    $Script:GatewayPort = 8000
    $Script:AutoReloadEnabled = $true

    if (-not (Test-Path -LiteralPath $Script:EnvFile)) {
        return
    }

    $content = Get-Content -LiteralPath $Script:EnvFile -Raw -ErrorAction SilentlyContinue
    if (-not $content) {
        return
    }

    $apiKeyMatch = [regex]::Match($content, '(?m)^\s*PROXY_API_KEY\s*=\s*"?([^"\r\n]+)"?\s*$')
    if ($apiKeyMatch.Success) {
        $Script:ApiKey = $apiKeyMatch.Groups[1].Value.Trim()
    }

    $portMatch = [regex]::Match($content, '(?m)^\s*SERVER_PORT\s*=\s*"?(\d+)"?\s*$')
    if ($portMatch.Success) {
        $portValue = [int]$portMatch.Groups[1].Value
        if ($portValue -gt 0 -and $portValue -lt 65536) {
            $Script:GatewayPort = $portValue
        }
    }
}

function Test-ProxyApiKeyConfigured {
    # 本地代理必须显式配置访问密钥，避免误以为存在安全默认值。
    if ([string]::IsNullOrWhiteSpace($Script:ApiKey)) {
        Write-Host ""
        Write-Host "未检测到 PROXY_API_KEY，拒绝继续。" -ForegroundColor Red
        Write-Host "请先在 .env 中配置 PROXY_API_KEY，然后再启动或测试网关。" -ForegroundColor Yellow
        return $false
    }

    return $true
}

function Save-Config {
    $lines = @()
    if (Test-Path -LiteralPath $Script:EnvFile) {
        $lines = Get-Content -LiteralPath $Script:EnvFile -ErrorAction SilentlyContinue
    }

    $foundApiKey = $false
    $foundPort = $false
    $newLines = @()

    foreach ($line in $lines) {
        if ($line -match '^\s*PROXY_API_KEY\s*=') {
            $newLines += "PROXY_API_KEY=`"$Script:ApiKey`""
            $foundApiKey = $true
            continue
        }
        if ($line -match '^\s*SERVER_PORT\s*=') {
            $newLines += "SERVER_PORT=$Script:GatewayPort"
            $foundPort = $true
            continue
        }
        $newLines += $line
    }

    if (-not $foundApiKey) {
        $newLines += "PROXY_API_KEY=`"$Script:ApiKey`""
    }
    if (-not $foundPort) {
        $newLines += "SERVER_PORT=$Script:GatewayPort"
    }

    $newLines | Set-Content -LiteralPath $Script:EnvFile -Encoding UTF8
}

function Test-PortInUse {
    param([int]$Port)
    $result = netstat -ano | Select-String -Pattern "LISTENING\s+\d+\s*$" | Select-String -Pattern "[:\.]$Port\s"
    return $null -ne $result
}

function Get-PortProcess {
    param([int]$Port)
    $connections = netstat -ano | Select-String -Pattern "[:\.]$Port\s" | ForEach-Object {
        ($_ -split '\s+')[-1]
    } | Select-Object -Unique

    foreach ($connPid in $connections) {
        if (-not [string]::IsNullOrWhiteSpace($connPid) -and $connPid -ne "0") {
            $proc = Get-Process -Id ([int]$connPid) -ErrorAction SilentlyContinue
            if ($proc) {
                return $proc
            }
        }
    }
    return $null
}

function Get-AccountInfo {
    if (-not (Test-Path -LiteralPath $Script:CredsFile)) {
        return @{
            Found = $false
            Message = "未找到凭证文件。请先在 Kiro IDE 中登录。"
        }
    }

    try {
        $content = Get-Content -LiteralPath $Script:CredsFile -Raw | ConvertFrom-Json
        $expiresAt = $content.expiresAt -as [DateTime]
        if (-not $expiresAt) {
            return @{
                Found = $false
                Message = "凭证文件中的过期时间无效。"
            }
        }

        $local = $expiresAt.ToLocalTime()
        $now = Get-Date

        # Create unique account identifier
        $accountId = if ($content.profileArn) {
            $content.profileArn
        } elseif ($content.clientIdHash) {
            $content.clientIdHash
        } else {
            # Fallback: hash of access token (first 16 chars)
            if ($content.accessToken) {
                $content.accessToken.Substring(0, [Math]::Min(16, $content.accessToken.Length))
            } else {
                "unknown"
            }
        }

        return @{
            Found = $true
            AccountId = $accountId
            AuthMethod = $content.authMethod
            Provider = $content.provider
            ProfileArn = $content.profileArn
            ExpiresAtLocal = $local.ToString("yyyy-MM-dd HH:mm:ss")
            ExpiresAtUtc = $expiresAt
            IsExpired = ($local -lt $now)
            IsExpiringSoon = ($local -lt $now.AddMinutes(30))
            FileHash = (Get-FileHash -LiteralPath $Script:CredsFile -Algorithm MD5).Hash
        }
    } catch {
        return @{
            Found = $false
            Message = "解析凭证文件失败: $($_.Exception.Message)"
        }
    }
}

function Save-AccountState {
    param($AccountInfo)

    Ensure-RuntimeDirectory

    if (-not $AccountInfo.Found) {
        return
    }

    $state = @{
        AccountId = $AccountInfo.AccountId
        FileHash = $AccountInfo.FileHash
        SavedAt = (Get-Date).ToString("o")
        ExpiresAtUtc = $AccountInfo.ExpiresAtUtc.ToString("o")
    }

    $state | ConvertTo-Json | Set-Content -LiteralPath $Script:StateFile -Encoding UTF8
}

function Get-SavedAccountState {
    if (-not (Test-Path -LiteralPath $Script:StateFile)) {
        return $null
    }

    try {
        $state = Get-Content -LiteralPath $Script:StateFile -Raw | ConvertFrom-Json
        return @{
            AccountId = $state.AccountId
            FileHash = $state.FileHash
            SavedAt = [DateTime]::Parse($state.SavedAt)
            ExpiresAtUtc = [DateTime]::Parse($state.ExpiresAtUtc)
        }
    } catch {
        return $null
    }
}

function Test-AccountChanged {
    $current = Get-AccountInfo
    if (-not $current.Found) {
        return $false
    }

    $saved = Get-SavedAccountState
    if (-not $saved) {
        return $false
    }

    # Check if account ID changed (account switch)
    if ($current.AccountId -ne $saved.AccountId) {
        return $true
    }

    # Check if file hash changed (credentials updated)
    if ($current.FileHash -ne $saved.FileHash) {
        return $true
    }

    return $false
}

function Stop-Gateway {
    param([switch]$Silent)

    if (-not $Silent) {
        Write-Host ""
        Write-Host "正在停止网关..." -ForegroundColor Yellow
    }

    # Stop by PID file
    if (Test-Path -LiteralPath $Script:PidFile) {
        $pidText = (Get-Content -LiteralPath $Script:PidFile -Raw -ErrorAction SilentlyContinue).Trim()
        if ($pidText -match '^\d+$') {
            $pidValue = [int]$pidText
            $proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
            if ($proc) {
                Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
                Start-Sleep -Milliseconds 800
            }
        }
        Remove-Item -LiteralPath $Script:PidFile -Force -ErrorAction SilentlyContinue
    }

    # Stop by port (in case PID file is missing or stale)
    $maxRetries = 3
    for ($i = 0; $i -lt $maxRetries; $i++) {
        if (Test-PortInUse -Port $Script:GatewayPort) {
            $proc = Get-PortProcess -Port $Script:GatewayPort
            if ($proc) {
                if (-not $Silent) {
                    Write-Host "正在停止进程 $($proc.Name) (PID: $($proc.Id))..." -ForegroundColor Gray
                }
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                Start-Sleep -Milliseconds 800
            } else {
                break
            }
        } else {
            break
        }
    }

    # Final verification
    if (Test-PortInUse -Port $Script:GatewayPort) {
        if (-not $Silent) {
            Write-Host "警告: 端口 $Script:GatewayPort 在停止尝试后仍在使用中。" -ForegroundColor Yellow
        }
        return $false
    }

    if (-not $Silent) {
        Write-Host "网关已停止。" -ForegroundColor Green
    }
    return $true
}

function Stop-CredWatcher {
    $job = Get-Job -Name $Script:WatcherJobName -ErrorAction SilentlyContinue
    if ($job) {
        Stop-Job -Name $Script:WatcherJobName -ErrorAction SilentlyContinue
        Remove-Job -Name $Script:WatcherJobName -Force -ErrorAction SilentlyContinue
    }
}

function Start-CredWatcher {
    # Stop existing watcher if any
    Stop-CredWatcher

    $watcherScript = {
        param($CredsFile, $StateFile, $CheckInterval)

        function Get-CurrentAccountInfo {
            if (-not (Test-Path -LiteralPath $CredsFile)) {
                return $null
            }

            try {
                $content = Get-Content -LiteralPath $CredsFile -Raw | ConvertFrom-Json
                $accountId = if ($content.profileArn) {
                    $content.profileArn
                } elseif ($content.clientIdHash) {
                    $content.clientIdHash
                } else {
                    if ($content.accessToken) {
                        $content.accessToken.Substring(0, [Math]::Min(16, $content.accessToken.Length))
                    } else {
                        "unknown"
                    }
                }

                return @{
                    AccountId = $accountId
                    FileHash = (Get-FileHash -LiteralPath $CredsFile -Algorithm MD5).Hash
                }
            } catch {
                return $null
            }
        }

        function Get-SavedState {
            if (-not (Test-Path -LiteralPath $StateFile)) {
                return $null
            }

            try {
                $state = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
                return @{
                    AccountId = $state.AccountId
                    FileHash = $state.FileHash
                }
            } catch {
                return $null
            }
        }

        while ($true) {
            Start-Sleep -Seconds $CheckInterval

            $current = Get-CurrentAccountInfo
            $saved = Get-SavedState

            if ($current -and $saved) {
                if ($current.AccountId -ne $saved.AccountId) {
                    Write-Output "ACCOUNT_CHANGED|$($saved.AccountId)|$($current.AccountId)"
                } elseif ($current.FileHash -ne $saved.FileHash) {
                    Write-Output "CREDENTIALS_UPDATED|$($current.AccountId)"
                }
            }
        }
    }

    $job = Start-Job -Name $Script:WatcherJobName -ScriptBlock $watcherScript -ArgumentList $Script:CredsFile, $Script:StateFile, $Script:CheckIntervalSeconds
    return $job
}

function Check-CredWatcherEvents {
    $job = Get-Job -Name $Script:WatcherJobName -ErrorAction SilentlyContinue
    if (-not $job) {
        return
    }

    $output = Receive-Job -Name $Script:WatcherJobName -ErrorAction SilentlyContinue
    if (-not $output) {
        return
    }

    foreach ($line in $output) {
        if ($line -match '^ACCOUNT_CHANGED\|(.+)\|(.+)$') {
            $oldAccount = $Matches[1]
            $newAccount = $Matches[2]

            Write-Host ""
            Write-Host "========================================" -ForegroundColor Cyan
            Write-Host "检测到账号切换！" -ForegroundColor Yellow
            Write-Host "旧账号: $oldAccount" -ForegroundColor Gray
            Write-Host "新账号: $newAccount" -ForegroundColor Green
            Write-Host "========================================" -ForegroundColor Cyan
            Write-Host ""
            Write-Host "正在使用新账号重启网关..." -ForegroundColor Yellow

            $null = Stop-Gateway -Silent
            Start-Sleep -Milliseconds 500

            $account = Get-AccountInfo
            if ($account.Found -and -not $account.IsExpired) {
                Save-AccountState -AccountInfo $account
                Start-GatewayProcess
                Write-Host "网关重启成功！" -ForegroundColor Green
            } else {
                Write-Host "重启失败: 凭证无效或已过期。" -ForegroundColor Red
            }

        } elseif ($line -match '^CREDENTIALS_UPDATED\|(.+)$') {
            $accountId = $Matches[1]

            Write-Host ""
            Write-Host "账号凭证已更新: $accountId" -ForegroundColor Cyan
            Write-Host "正在重启网关以应用更改..." -ForegroundColor Yellow

            $null = Stop-Gateway -Silent
            Start-Sleep -Milliseconds 500

            $account = Get-AccountInfo
            if ($account.Found -and -not $account.IsExpired) {
                Save-AccountState -AccountInfo $account
                Start-GatewayProcess
                Write-Host "网关重启成功！" -ForegroundColor Green
            } else {
                Write-Host "重启失败: 凭证无效或已过期。" -ForegroundColor Red
            }
        }
    }
}

function Start-GatewayProcess {
    Ensure-RuntimeDirectory
    Rotate-LogsIfNeeded

    $env:PYTHONIOENCODING = "utf-8"
    $env:SERVER_PORT = "$Script:GatewayPort"

    $pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $pythonExe) {
        Write-Host "未在 PATH 中找到 python。" -ForegroundColor Red
        return $false
    }

    $mainPy = Join-Path $Script:GatewayDir "main.py"

    try {
        $process = Start-Process -FilePath $pythonExe `
            -ArgumentList $mainPy `
            -WorkingDirectory $Script:GatewayDir `
            -RedirectStandardOutput $Script:OutLogFile `
            -RedirectStandardError $Script:ErrLogFile `
            -WindowStyle Hidden `
            -PassThru

        if ($process) {
            $process.Id | Set-Content -LiteralPath $Script:PidFile -Encoding UTF8
            return $true
        }
    } catch {
        Write-Host "启动网关失败: $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }

    return $false
}

function Get-ManagedGatewayProcess {
    # 从 PID 文件读取当前由脚本启动的网关进程。
    if (-not (Test-Path -LiteralPath $Script:PidFile)) {
        return $null
    }

    $pidText = (Get-Content -LiteralPath $Script:PidFile -Raw -ErrorAction SilentlyContinue).Trim()
    if ($pidText -notmatch '^\d+$') {
        return $null
    }

    return Get-Process -Id ([int]$pidText) -ErrorAction SilentlyContinue
}

function Test-GatewayHealth {
    # 使用健康检查确认应用已经完成启动，而不是只看端口是否监听。
    try {
        $health = Invoke-RestMethod `
            -Uri "http://localhost:$Script:GatewayPort/health" `
            -TimeoutSec 5 `
            -ErrorAction Stop

        return $health
    } catch {
        return $null
    }
}

function Wait-GatewayReady {
    param(
        [int]$TimeoutSeconds = 30,
        [int]$PollIntervalMilliseconds = 500
    )

    # 后台启动后可能还要加载模型和认证，不能只睡 2 秒就下结论。
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $deadline) {
        $gatewayProcess = Get-ManagedGatewayProcess
        if (-not $gatewayProcess) {
            return @{
                Ready = $false
                Reason = "process_not_found"
            }
        }

        $health = Test-GatewayHealth
        if ($null -ne $health -and $health.status -eq "healthy") {
            return @{
                Ready = $true
                Reason = "health_check_passed"
                ProcessId = $gatewayProcess.Id
            }
        }

        Start-Sleep -Milliseconds $PollIntervalMilliseconds
    }

    $gatewayProcess = Get-ManagedGatewayProcess
    if (-not $gatewayProcess) {
        return @{
            Ready = $false
            Reason = "process_exited"
        }
    }

    if (Test-PortInUse -Port $Script:GatewayPort) {
        return @{
            Ready = $true
            Reason = "port_listening_after_timeout"
            ProcessId = $gatewayProcess.Id
        }
    }

    return @{
        Ready = $false
        Reason = "startup_timeout"
        ProcessId = $gatewayProcess.Id
    }
}

function Invoke-ManagedGatewayHealthCheck {
    $gatewayProcess = Get-ManagedGatewayProcess
    if (-not $gatewayProcess) {
        $Script:HealthCheckFailures = 0
        return
    }

    $health = Test-GatewayHealth
    if ($null -ne $health -and $health.status -eq "healthy") {
        if ($Script:HealthCheckFailures -gt 0) {
            Write-StateLog "健康检查恢复正常。"
        }
        $Script:HealthCheckFailures = 0
        return
    }

    $Script:HealthCheckFailures += 1
    $failureReason = "health endpoint unavailable"
    if ($null -ne $health) {
        if ($health.auth) {
            $failureReason = "status=$($health.status); auth_expired=$($health.auth.expired); expiring_soon=$($health.auth.expiring_soon); refresh_failures=$($health.auth.refresh_failures)"
            if ($health.auth.last_refresh_error_message) {
                $failureReason += "; refresh_error=$($health.auth.last_refresh_error_message)"
            }
        } else {
            $failureReason = "status=$($health.status)"
        }
    }
    Write-StateLog "健康检查失败 #$($Script:HealthCheckFailures)：$failureReason"

    if ($Script:HealthCheckFailures -lt $Script:HealthCheckFailureThreshold) {
        return
    }

    Write-Host ""
    Write-Host "健康检查连续失败，正在重启网关..." -ForegroundColor Yellow
    Write-StateLog "健康检查连续失败，触发自动重启。"

    $null = Stop-Gateway -Silent
    Start-Sleep -Milliseconds 500

    if (Start-GatewayProcess) {
        $startupResult = Wait-GatewayReady
        if ($startupResult.Ready) {
            $Script:HealthCheckFailures = 0
            Write-StateLog "自动重启成功。"
            Write-Host "网关自动重启成功。" -ForegroundColor Green
        } else {
            Write-StateLog "自动重启后仍未就绪: $($startupResult.Reason)"
            Write-Host "网关自动重启后仍未就绪，请查看日志。" -ForegroundColor Red
        }
    } else {
        Write-StateLog "自动重启失败：无法启动网关进程。"
        Write-Host "网关自动重启失败，请查看日志。" -ForegroundColor Red
    }
}

function Start-Gateway {
    param([switch]$Foreground)

    if (-not (Test-ProxyApiKeyConfigured)) {
        return
    }

    if (Test-PortInUse -Port $Script:GatewayPort) {
        $proc = Get-PortProcess -Port $Script:GatewayPort
        Write-Host ""
        Write-Host "端口 $Script:GatewayPort 已被占用。" -ForegroundColor Yellow
        if ($proc) {
            Write-Host "进程: $($proc.Name) (PID: $($proc.Id))" -ForegroundColor Gray
        }
        $action = Read-Host "输入 S 停止该进程并继续，其他键取消"
        if ($action -ieq "s") {
            $stopped = Stop-Gateway
            if (-not $stopped) {
                Write-Host "停止现有网关失败。请手动停止。" -ForegroundColor Red
                return
            }
            Start-Sleep -Milliseconds 500
        } else {
            return
        }
    }

    $account = Get-AccountInfo
    if (-not $account.Found) {
        Write-Host ""
        Write-Host $account.Message -ForegroundColor Red
        return
    }
    if ($account.IsExpired) {
        Write-Host ""
        Write-Host "凭证已于 $($account.ExpiresAtLocal) 过期。请在 Kiro IDE 中重新登录。" -ForegroundColor Red
        return
    }
    if ($account.IsExpiringSoon) {
        Write-Host ""
        Write-Host "警告: 凭证将于 $($account.ExpiresAtLocal) 过期。" -ForegroundColor Yellow
    }

    Write-Host ""
    if ($Foreground) {
        Write-Host "正在以前台模式启动网关 http://localhost:$Script:GatewayPort ..." -ForegroundColor Cyan
        Write-Host "按 Ctrl+C 停止网关并返回菜单。" -ForegroundColor Yellow
        Write-Host ""
    } else {
        Write-Host "正在以后台模式启动网关 http://localhost:$Script:GatewayPort ..." -ForegroundColor Cyan
    }

    # Save current account state
    Save-AccountState -AccountInfo $account

    if ($Foreground) {
        # Foreground mode: run in current terminal with live logs
        $env:PYTHONIOENCODING = "utf-8"
        $env:SERVER_PORT = "$Script:GatewayPort"

        $pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
        if (-not $pythonExe) {
            Write-Host "未在 PATH 中找到 python。" -ForegroundColor Red
            return
        }

        $mainPy = Join-Path $Script:GatewayDir "main.py"

        try {
            Push-Location $Script:GatewayDir
            & $pythonExe $mainPy
        } catch {
            Write-Host "网关已停止: $($_.Exception.Message)" -ForegroundColor Yellow
        } finally {
            Pop-Location -ErrorAction SilentlyContinue
        }
    } else {
        # Background mode: run as hidden process
        $started = Start-GatewayProcess
        if (-not $started) {
            Write-Host "启动网关进程失败。" -ForegroundColor Red
            return
        }

        # 轮询等待网关就绪，避免启动稍慢时误报失败。
        $startupResult = Wait-GatewayReady

        if ($startupResult.Ready) {
            Write-Host "网关启动成功！" -ForegroundColor Green
            if ($startupResult.ProcessId) {
                Write-Host "进程 PID: $($startupResult.ProcessId)" -ForegroundColor Gray
            }
            Write-Host "日志: .runtime/run.out.log / .runtime/run.err.log" -ForegroundColor Gray

            if ($Script:AutoReloadEnabled) {
                Write-Host ""
                Write-Host "自动重载已启用。正在监控凭证变化..." -ForegroundColor Cyan
                Start-CredWatcher | Out-Null
            }
        } else {
            Write-Host "网关启动失败。请查看日志了解详情。" -ForegroundColor Red
            Write-Host "失败原因: $($startupResult.Reason)" -ForegroundColor DarkYellow
            if (Test-Path -LiteralPath $Script:PidFile) {
                Remove-Item -LiteralPath $Script:PidFile -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

function Test-API {
    Write-Host ""
    Write-Host "正在测试 API..." -ForegroundColor Cyan

    if (-not (Test-ProxyApiKeyConfigured)) {
        return
    }

    try {
        $health = Invoke-RestMethod -Uri "http://localhost:$Script:GatewayPort/health" -TimeoutSec 5
        Write-Host "健康检查通过。版本=$($health.version)" -ForegroundColor Green
        if ($null -ne $health.request_limiter) {
            Write-Host "并发限制: limit=$($health.request_limiter.limit), available=$($health.request_limiter.available_slots), queue_timeout=$($health.request_limiter.queue_timeout_seconds)s" -ForegroundColor Gray
        }
        if ($null -ne $health.models) {
            Write-Host "模型缓存: count=$($health.models.count), stale=$($health.models.cache_stale)" -ForegroundColor Gray
        }
        if ($null -ne $health.auth) {
            Write-Host "认证状态: initialized=$($health.auth.initialized), type=$($health.auth.type), region=$($health.auth.region)" -ForegroundColor Gray
            Write-Host "Token 状态: expired=$($health.auth.expired), expiring_soon=$($health.auth.expiring_soon), refresh_failures=$($health.auth.refresh_failures)" -ForegroundColor Gray
            if ($health.auth.expires_in_seconds -ne $null) {
                Write-Host "Token 剩余有效期: $($health.auth.expires_in_seconds)s" -ForegroundColor Gray
            }
            if ($health.auth.last_refresh_at) {
                Write-Host "最近刷新时间: $($health.auth.last_refresh_at)" -ForegroundColor Gray
            }
            if ($health.auth.last_refresh_error_at) {
                Write-Host "最近刷新失败: $($health.auth.last_refresh_at) | $($health.auth.last_refresh_error_message)" -ForegroundColor DarkYellow
            }
        }
        if ($null -ne $health.errors_by_type) {
            Write-Host "错误分类: auth=$($health.errors_by_type.auth), rate_limit=$($health.errors_by_type.rate_limit), timeout=$($health.errors_by_type.timeout), upstream=$($health.errors_by_type.upstream), validation=$($health.errors_by_type.validation), internal=$($health.errors_by_type.internal)" -ForegroundColor Gray
        }
        if ($null -ne $health.http_client) {
            Write-Host "连接池: max=$($health.http_client.max_connections), keepalive=$($health.http_client.max_keepalive_connections), expiry=$($health.http_client.keepalive_expiry_seconds)s" -ForegroundColor Gray
            Write-Host "超时: connect=$($health.http_client.timeouts.connect)s, read=$($health.http_client.timeouts.read)s, write=$($health.http_client.timeouts.write)s, pool=$($health.http_client.timeouts.pool)s" -ForegroundColor Gray
        }
    } catch {
        Write-Host "健康检查失败。网关是否正在运行？" -ForegroundColor Red
        return
    }

    try {
        $models = Invoke-RestMethod `
            -Uri "http://localhost:$Script:GatewayPort/v1/models" `
            -Headers @{ Authorization = "Bearer $Script:ApiKey" } `
            -TimeoutSec 10

        Write-Host "模型列表获取成功。数量=$($models.data.Count)" -ForegroundColor Green
        foreach ($model in $models.data) {
            Write-Host "  - $($model.id)"
        }
    } catch {
        Write-Host "模型请求失败: $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Set-ApiKey {
    Write-Host ""
    Write-Host "当前 API 密钥: $Script:ApiKey" -ForegroundColor Gray
    $newKey = Read-Host "输入新的 API 密钥 (留空取消)"
    if ([string]::IsNullOrWhiteSpace($newKey)) {
        Write-Host "已取消。" -ForegroundColor Gray
        return
    }
    $Script:ApiKey = $newKey
    Save-Config
    Write-Host "API 密钥已更新。" -ForegroundColor Green
}

function Set-Port {
    Write-Host ""
    $newPort = Read-Host "输入网关端口 (1-65535, 当前=$Script:GatewayPort)"
    if ($newPort -notmatch '^\d+$') {
        Write-Host "端口无效。" -ForegroundColor Red
        return
    }
    $portValue = [int]$newPort
    if ($portValue -lt 1 -or $portValue -gt 65535) {
        Write-Host "端口无效。" -ForegroundColor Red
        return
    }
    $Script:GatewayPort = $portValue
    Save-Config
    Write-Host "端口已更新为 $Script:GatewayPort。" -ForegroundColor Green
}

function Toggle-AutoReload {
    $Script:AutoReloadEnabled = -not $Script:AutoReloadEnabled
    Write-Host ""
    if ($Script:AutoReloadEnabled) {
        Write-Host "自动重载已启用。" -ForegroundColor Green
        if (Test-PortInUse -Port $Script:GatewayPort) {
            Write-Host "正在启动凭证监控..." -ForegroundColor Cyan
            Start-CredWatcher | Out-Null
        }
    } else {
        Write-Host "自动重载已禁用。" -ForegroundColor Yellow
        Stop-CredWatcher
    }
}

function Show-ClaudeConfig {
    Write-Host ""
    Write-Host "在 Claude Code settings.json 中使用以下配置:" -ForegroundColor Cyan
    Write-Host ""

    if (-not (Test-ProxyApiKeyConfigured)) {
        return
    }

    $json = @"
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "$Script:ApiKey",
    "ANTHROPIC_BASE_URL": "http://localhost:$Script:GatewayPort",
    "ANTHROPIC_MODEL": "claude-sonnet-4.5"
  }
}
"@
    Write-Host $json
}

function Show-Menu {
    Load-Config

    # Check for credential watcher events
    if ($Script:AutoReloadEnabled) {
        Check-CredWatcherEvents
    }

    Rotate-LogsIfNeeded
    Invoke-ManagedGatewayHealthCheck

    $runningStatus = if (Test-PortInUse -Port $Script:GatewayPort) { "运行中" } else { "已停止" }
    $runningColor = if ($runningStatus -eq "运行中") { "Green" } else { "Gray" }
    $account = Get-AccountInfo

    Write-Host ""
    Write-Host "================ Kiro Gateway V2 ================" -ForegroundColor Cyan
    Write-Host "状态: $runningStatus | 端口: $Script:GatewayPort" -ForegroundColor $runningColor
    Write-Host "基础 URL: http://localhost:$Script:GatewayPort"
    Write-Host "API 密钥: $Script:ApiKey"

    $autoReloadStatus = if ($Script:AutoReloadEnabled) { "已启用" } else { "已禁用" }
    $autoReloadColor = if ($Script:AutoReloadEnabled) { "Green" } else { "Gray" }
    Write-Host "自动重载: $autoReloadStatus" -ForegroundColor $autoReloadColor
    if ($runningStatus -eq "运行中") {
        Write-Host "健康失败次数: $($Script:HealthCheckFailures)/$($Script:HealthCheckFailureThreshold)" -ForegroundColor Gray
    }

    $watcherStatus = if (Get-Job -Name $Script:WatcherJobName -ErrorAction SilentlyContinue) { "运行中" } else { "未运行" }
    if ($Script:AutoReloadEnabled -and $runningStatus -eq "运行中") {
        Write-Host "监控器: $watcherStatus" -ForegroundColor Gray
    }
    Write-Host ""

    if ($account.Found) {
        $statusText = if ($account.IsExpired) { "已过期" } elseif ($account.IsExpiringSoon) { "即将过期" } else { "正常" }
        $statusColor = if ($account.IsExpired) { "Red" } elseif ($account.IsExpiringSoon) { "Yellow" } else { "Green" }
        Write-Host "账号: $($account.AuthMethod)/$($account.Provider) | $statusText" -ForegroundColor $statusColor
        Write-Host "过期时间: $($account.ExpiresAtLocal)"

        if ($account.AccountId) {
            $shortId = if ($account.AccountId.Length -gt 50) {
                $account.AccountId.Substring(0, 47) + "..."
            } else {
                $account.AccountId
            }
            Write-Host "ID: $shortId" -ForegroundColor Gray
        }
    } else {
        Write-Host "账号: $($account.Message)" -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host "1) 启动网关 (后台模式)"
    Write-Host "F) 启动网关 (前台模式，实时日志)"
    Write-Host "2) 停止网关"
    Write-Host "3) 测试 API"
    Write-Host "4) 刷新账号检查"
    Write-Host "5) 切换自动重载 (当前: $autoReloadStatus)"
    Write-Host "6) 设置 API 密钥"
    Write-Host "7) 设置端口"
    Write-Host "8) 显示 Claude Code 配置"
    Write-Host "9) 查看日志"
    Write-Host "0) 退出"
}

function Show-Logs {
    Ensure-RuntimeDirectory

    Write-Host ""
    Write-Host "最近的错误日志 (最后 20 行):" -ForegroundColor Cyan
    if (Test-Path -LiteralPath $Script:ErrLogFile) {
        Get-Content -LiteralPath $Script:ErrLogFile -Tail 20 -ErrorAction SilentlyContinue | ForEach-Object {
            Write-Host $_ -ForegroundColor Gray
        }
    } else {
        Write-Host "未找到错误日志。" -ForegroundColor Gray
    }
}

function Read-MenuChoice {
    Write-Host "选择: " -NoNewline
    try {
        $keyInfo = [System.Console]::ReadKey($true)
        $char = [string]$keyInfo.KeyChar
        Write-Host $char
        return $char
    } catch {
        try {
            return Read-Host "选择"
        } catch {
            return $null
        }
    }
}

function Main {
    Load-Config
    $running = $true

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Kiro Gateway V2 - 增强版" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "新功能:" -ForegroundColor Green
    Write-Host "  - 账号切换自动重载" -ForegroundColor Gray
    Write-Host "  - 后台凭证监控" -ForegroundColor Gray
    Write-Host "  - 自动重启网关" -ForegroundColor Gray
    Write-Host ""

    while ($running) {
        Show-Menu
        $choice = Read-MenuChoice
        if ([string]::IsNullOrWhiteSpace($choice)) {
            Start-Sleep -Milliseconds 250
            continue
        }

        switch ($choice) {
            "1" { Start-Gateway }
            "f" { Start-Gateway -Foreground }
            "F" { Start-Gateway -Foreground }
            "2" {
                $null = Stop-Gateway
                Stop-CredWatcher
            }
            "3" { Test-API }
            "4" {
                $account = Get-AccountInfo
                if ($account.Found) {
                    Write-Host ""
                    Write-Host "账号刷新完成。" -ForegroundColor Green
                    Write-Host "过期时间: $($account.ExpiresAtLocal)" -ForegroundColor Gray

                    # Check if account changed
                    if (Test-AccountChanged) {
                        Write-Host ""
                        Write-Host "账号自上次启动后已更改！" -ForegroundColor Yellow
                        if (Test-PortInUse -Port $Script:GatewayPort) {
                            $restart = Read-Host "使用新账号重启网关？(y/n)"
                            if ($restart -ieq "y") {
                                $null = Stop-Gateway
                                Start-Sleep -Milliseconds 500
                                Start-Gateway
                            }
                        }
                    }
                } else {
                    Write-Host ""
                    Write-Host $account.Message -ForegroundColor Red
                }
            }
            "5" { Toggle-AutoReload }
            "6" { Set-ApiKey }
            "7" { Set-Port }
            "8" { Show-ClaudeConfig }
            "9" { Show-Logs }
            "0" {
                if (Test-PortInUse -Port $Script:GatewayPort) {
                    Write-Host ""
                    $confirm = Read-Host "网关仍在运行。仍要退出吗？(y/n)"
                    if ($confirm -ieq "y") {
                        Stop-CredWatcher
                        $running = $false
                    }
                } else {
                    Stop-CredWatcher
                    $running = $false
                }
            }
            default {
                if (-not [string]::IsNullOrWhiteSpace($choice)) {
                    Write-Host ""
                    Write-Host "无效的选择。" -ForegroundColor Red
                }
            }
        }
    }

    Write-Host ""
    Write-Host "再见！" -ForegroundColor Cyan
}

# Cleanup on exit
trap {
    Stop-CredWatcher
}

# 直接执行脚本时进入交互菜单；被 dot-source 时只导出函数，便于复用和验证。
if ($MyInvocation.InvocationName -ne '.') {
    Main
}
