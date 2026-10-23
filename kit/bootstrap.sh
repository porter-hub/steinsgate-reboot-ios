#!/bin/bash
# Installer kit, macOS and Linux (Start-macOS.command / start-linux.sh call this): make sure uv (which brings its own Python)
# and the .NET 10 runtime are there, offering the official installers when they are not (always asking first; nothing
# is installed system-wide: uv goes to ~/.local/bin, .NET to ~/.dotnet), then run kit/pack.py with pinned packages.
set -u
cd "$(dirname "$0")/.."
say() { printf '%s\n' "$*"; }
ask() { local a; printf '%s [Y/n] ' "$1"; read -r a; [ -z "$a" ] || [[ $a == [Yy]* ]]; }
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$HOME/.dotnet:$PATH"
say "== 检查环境 =="
if command -v uv > /dev/null; then say "✓ uv $(uv --version | cut -d' ' -f2)"
else
    say "✗ 没有 uv（用来自动准备 Python 3.14，官方网站 https://docs.astral.sh/uv/）"
    if ask "  要我用 uv 的官方安装脚本装到你的用户目录（~/.local/bin，不需要管理员密码）吗？"; then
        curl -LsSf https://astral.sh/uv/install.sh | sh || { say "安装 uv 失败。请按 https://docs.astral.sh/uv/getting-started/installation/ 手动安装后再运行。"; exit 1; }
    else say "请按 https://docs.astral.sh/uv/getting-started/installation/ 安装 uv 后再运行。"; exit 1; fi
fi
has_rt() { command -v dotnet > /dev/null && dotnet --list-runtimes 2> /dev/null | grep -q '^Microsoft.NETCore.App 10\.'; }
if has_rt; then say "✓ .NET 10 运行时"
else
    say "✗ 没有 .NET 10 运行时（解包和着色器工具要用，官方网站 https://dotnet.microsoft.com/download/dotnet/10.0）"
    if ask "  要我用微软的官方脚本装到你的用户目录（~/.dotnet，不需要管理员密码）吗？"; then
        t=$(mktemp) && curl -sSL https://dot.net/v1/dotnet-install.sh -o "$t" && bash "$t" --channel 10.0 --runtime dotnet --install-dir "$HOME/.dotnet" > /dev/null \
            || { say "安装 .NET 失败。请从 https://dotnet.microsoft.com/download/dotnet/10.0 下载「.NET Runtime 10」手动安装后再运行。"; exit 1; }
        rm -f "$t"; export DOTNET_ROOT=$HOME/.dotnet
        has_rt || { say "装完了但还是找不到 .NET 10。请重开一个终端再运行。"; exit 1; }
    else say "请从 https://dotnet.microsoft.com/download/dotnet/10.0 下载「.NET Runtime 10」安装后再运行。"; exit 1; fi
fi
# Python 3.14 and the two packages the tools need, pinned (kit/requirements.txt); uv caches them after the first run
exec uv run --no-project --quiet --python 3.14 --with-requirements kit/requirements.txt python -X utf8 kit/pack.py "$@"
