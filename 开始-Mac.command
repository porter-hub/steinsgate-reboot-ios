#!/bin/bash
# Double-click in Finder (the first time: right-click -> Open, the file is not from the App Store).
cd "$(dirname "$0")"
bash kit/bootstrap.sh "$@"
echo
read -n 1 -s -r -p "按任意键关闭这个窗口…"
