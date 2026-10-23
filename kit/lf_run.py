#!/usr/bin/env python3
# Run a kit tool with LF line endings for every text file it writes (kit/pack.py runs them all through this).
# Python on Windows writes "\r\n" for "\n" in text mode unless newline= is given, and the tools (several of them
# shared with the skill and locked, skill.lock) do not give it: the rewriter's tables, the shader map (whose bytes are
# part of the shader chain version) and the reflection JSON would differ from the Mac's, and the package with them.
# Only the default changes: a tool that passes newline= itself, or opens in binary mode, is untouched.
# Usage: lf_run.py <script.py> [args...]
import builtins, io, os, runpy, sys

_open = io.open


def open_lf(file, mode='r', buffering=-1, encoding=None, errors=None, newline=None, closefd=True, opener=None):
    if newline is None and 'b' not in mode and any(c in mode for c in 'wax+'): newline = '\n'
    return _open(file, mode, buffering, encoding, errors, newline, closefd, opener)


if __name__ == '__main__':
    if len(sys.argv) < 2: sys.exit('usage: lf_run.py <script.py> [args...]')
    builtins.open = io.open = open_lf
    script = os.path.abspath(sys.argv[1]); sys.argv = sys.argv[1:]; sys.path.insert(0, os.path.dirname(script))
    runpy.run_path(script, run_name='__main__')
