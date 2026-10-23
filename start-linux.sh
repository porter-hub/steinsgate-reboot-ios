#!/bin/bash
# In a terminal: bash start-linux.sh
cd "$(dirname "$0")"
exec bash kit/bootstrap.sh "$@"
