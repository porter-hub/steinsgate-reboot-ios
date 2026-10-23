# switch-ios-port: thin adapter so the verification tools share the rewriter's NSO decoding (tools/nsolib.py).
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
from nsolib import Nso


def load(path):
    return bytes(Nso(path).img)


def build_id(path):
    return Nso(path).build_id


def header(out_dir, mod):
    h = {}
    for line in open(f'{out_dir}/{mod}.fixups.tsv'):
        if not line.startswith('#'): break
        k, v = line[1:].split(); h[k] = int(v, 0)      # 0x prefix = offset, decimal = key index
    return h
