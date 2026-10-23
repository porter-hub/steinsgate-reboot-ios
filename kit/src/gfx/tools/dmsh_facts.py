#!/usr/bin/env python3
# Derived from d2r-mobile dm/gfx/tools/dmsh_facts.py (MIT, (c) 2026 jhaizhou), via engage-port; the PC-reflection
# freeze (D2R's PC twin) is left out: this port answers from run-time captures (gfx/tools/rt_facts.py).
# Answer table for translation queries, nvn/shader_facts.tsv: one row per shader key with the few facts the translator
# asks for but that the Maxwell code does not carry: dimension of textures / images, image format, component types of
# vertex attributes and fragment outputs, compute workgroup size and shared memory. The table ships with the source;
# building reads only this table, and keys not in it are answered with the translator's defaults (listed in the build
# summary).
# Row format: key <TAB> JSON (keys sorted, compact; the same input gives a byte-identical table).
# Usage: dmsh_facts.py    print the row count and how many rows carry each fact
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
TABLE = os.path.join(HERE, '../nvn/shader_facts.tsv')


def load():
    """key -> fact dict; {} when the table does not exist"""
    if not os.path.exists(TABLE): return {}
    return {k: json.loads(v) for k, v in (l.rstrip('\n').split('\t', 1) for l in open(TABLE) if l.strip() and not l.startswith('#'))}


def row(key, facts): return key + '\t' + json.dumps(facts, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n'


if __name__ == '__main__':
    if sys.argv[1:]: sys.exit('usage: dmsh_facts.py (no arguments)')
    t = load(); print(f'{len(t)} keys: ' + ', '.join(f'{n} {sum(bool(f.get(n)) for f in t.values())}' for n in ('tex', 'img', 'attr', 'fout', 'local', 'shared')))
