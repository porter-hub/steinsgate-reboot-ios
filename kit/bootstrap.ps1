# Installer kit, Windows (开始-Windows.bat calls this): make sure uv (which brings its own Python) and the .NET 10
# runtime are there, offering the official installers when they are not (always asking first; per user, no
# administrator: uv to %USERPROFILE%\.local\bin, .NET to %LOCALAPPDATA%\Microsoft\dotnet), then run kit\pack.py.
# Saved as UTF-8 with a byte order mark: Windows PowerShell 5.1 reads a script without one in the ANSI code page.
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12   # older Windows 10: the installers are HTTPS-only
Set-Location (Split-Path -Parent $PSScriptRoot)
function Ask($q) { $a = Read-Host "$q [Y/n]"; return ($a -eq '' -or $a -match '^[Yy]') }
$dn = Join-Path $env:LOCALAPPDATA 'Microsoft\dotnet'
$env:Path = "$env:USERPROFILE\.local\bin;$dn;$env:Path"
Write-Host '== 检查环境 =='
if (Get-Command uv -ErrorAction SilentlyContinue) { Write-Host "✓ uv" }
else {
    Write-Host '✗ 没有 uv（用来自动准备 Python 3.14，官方网站 https://docs.astral.sh/uv/）'
    if (Ask '  要我用 uv 的官方安装脚本装到你的用户目录（不需要管理员权限）吗？') {
        powershell -NoProfile -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
        $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Write-Host '安装 uv 失败。请按 https://docs.astral.sh/uv/getting-started/installation/ 手动安装后再运行。'; exit 1 }
    } else { Write-Host '请按 https://docs.astral.sh/uv/getting-started/installation/ 安装 uv 后再运行。'; exit 1 }
}
function HasRt { try { return [bool]((& dotnet --list-runtimes 2>$null) -match '^Microsoft\.NETCore\.App 10\.') } catch { return $false } }
if (HasRt) { Write-Host '✓ .NET 10 运行时' }
else {
    Write-Host '✗ 没有 .NET 10 运行时（解包和着色器工具要用，官方网站 https://dotnet.microsoft.com/download/dotnet/10.0）'
    if (Ask '  要我用微软的官方脚本装到你的用户目录（不需要管理员权限）吗？') {
        $s = Join-Path $env:TEMP 'dotnet-install.ps1'
        Invoke-WebRequest -UseBasicParsing https://dot.net/v1/dotnet-install.ps1 -OutFile $s
        & powershell -NoProfile -ExecutionPolicy ByPass -File $s -Channel 10.0 -Runtime dotnet -InstallDir $dn | Out-Null
        $env:DOTNET_ROOT = $dn
        if (-not (HasRt)) { Write-Host '安装 .NET 失败。请从 https://dotnet.microsoft.com/download/dotnet/10.0 下载「.NET Runtime 10」手动安装后再运行。'; exit 1 }
    } else { Write-Host '请从 https://dotnet.microsoft.com/download/dotnet/10.0 下载「.NET Runtime 10」安装后再运行。'; exit 1 }
}
& uv run --no-project --quiet --python 3.14 --with-requirements kit\requirements.txt python -X utf8 kit\pack.py @args
exit $LASTEXITCODE
