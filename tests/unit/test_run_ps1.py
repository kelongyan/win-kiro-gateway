import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_PS1 = REPO_ROOT / "run.ps1"


def run_pwsh(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["pwsh", "-NoProfile", "-Command", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


class TestRunPs1:
    def test_wait_gateway_ready_tolerates_slow_startup_by_default(self):
        script = f"""
$ErrorActionPreference = 'Stop'
. '{RUN_PS1}'

$script:fakeNow = [datetime]'2026-04-24T21:39:35'

function Get-Date {{
    return $script:fakeNow
}}

function Start-Sleep {{
    param([int]$Milliseconds)
    $script:fakeNow = $script:fakeNow.AddMilliseconds($Milliseconds)
}}

function Get-ManagedGatewayProcess {{
    return [pscustomobject]@{{ Id = 32500 }}
}}

function Test-PortInUse {{
    param([int]$Port)
    return $false
}}

function Test-GatewayHealth {{
    if ($script:fakeNow -ge [datetime]'2026-04-24T21:40:10') {{
        return [pscustomobject]@{{ status = 'healthy' }}
    }}

    return $null
}}

$result = Wait-GatewayReady
"READY=$($result.Ready)"
"REASON=$($result.Reason)"
"""

        result = run_pwsh(script)

        assert result.returncode == 0, result.stderr
        assert "READY=True" in result.stdout
        assert "REASON=health_check_passed" in result.stdout

    def test_show_menu_hides_account_status_and_expiry_lines(self):
        script = f"""
$ErrorActionPreference = 'Stop'
. '{RUN_PS1}'

function Load-Config {{}}
function Check-CredWatcherEvents {{}}
function Rotate-LogsIfNeeded {{}}
function Invoke-ManagedGatewayHealthCheck {{}}
function Test-PortInUse {{ param([int]$Port) return $false }}
function Get-Job {{ return $null }}
function Get-AccountInfo {{
    return [pscustomobject]@{{
        Found = $true
        IsExpired = $false
        IsExpiringSoon = $true
        AuthMethod = 'social'
        Provider = 'Google'
        ExpiresAtLocal = '2026-04-24 22:00:39'
        AccountId = 'arn:aws:codewhisperer:us-east-1:699475941385:profile/test'
    }}
}}

Show-Menu
"""

        result = run_pwsh(script)

        assert result.returncode == 0, result.stderr
        assert "账号: social/Google | 即将过期" not in result.stdout
        assert "过期时间: 2026-04-24 22:00:39" not in result.stdout
        assert "ID: arn:aws:codewhisperer:us-east-1:699475941385:pr" in result.stdout

    def test_start_gateway_does_not_print_expiring_soon_warning(self):
        script = f"""
$ErrorActionPreference = 'Stop'
. '{RUN_PS1}'

$script:GatewayPort = 8000

function Test-ProxyApiKeyConfigured {{ return $true }}
function Test-PortInUse {{ param([int]$Port) return $false }}
function Save-AccountState {{ param($AccountInfo) }}
function Start-GatewayProcess {{ return $true }}
function Wait-GatewayReady {{
    return [pscustomobject]@{{
        Ready = $true
        Reason = 'health_check_passed'
        ProcessId = 32500
    }}
}}
function Start-CredWatcher {{ return $null }}
function Get-AccountInfo {{
    return [pscustomobject]@{{
        Found = $true
        IsExpired = $false
        IsExpiringSoon = $true
        ExpiresAtLocal = '2026-04-24 22:00:39'
        AccountId = 'arn:aws:codewhisperer:us-east-1:699475941385:profile/test'
    }}
}}

Start-Gateway
"""

        result = run_pwsh(script)

        assert result.returncode == 0, result.stderr
        assert "警告: 凭证将于 2026-04-24 22:00:39 过期。" not in result.stdout
        assert "http://localhost:8000" in result.stdout
