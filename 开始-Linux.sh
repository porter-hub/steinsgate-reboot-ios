#!/bin/bash
# In a terminal: bash 开始-Linux.sh
cd "$(dirname "$0")"
exec bash kit/bootstrap.sh "$@"
