# Write a copy of the user's prod.keys that holds only what NCA decryption needs, for hactool 1.4.0.
# Why: hactool 1.4.0 (2020) aborts on newer prod.keys entries ("Key (...) must be 32 hex digits!") and, before
# aborting, prints every unknown entry WITH ITS VALUE. Feeding it this filtered copy avoids both.
# Usage: filter_keys.py <prod.keys> <out file>     (prints counts and key NAMES only, never values)
# Put <out file> in a temporary directory outside the repo, pass it to hactool with -k ... --disablekeywarns,
# and delete it right after. Never commit it, never print it.
import re, sys
src, dst = sys.argv[1], sys.argv[2]
want = re.compile(r'^(header_key|header_kek_source|header_key_source|aes_kek_generation_source|aes_key_generation_source|'
                  r'key_area_key_(application|ocean|system)_source|key_area_key_(application|ocean|system)_[0-9a-f]{2}|'
                  r'master_key_[0-9a-f]{2}|master_kek_[0-9a-f]{2}|titlekek_source|titlekek_[0-9a-f]{2})$')
kept = dropped = 0
out = []
for line in open(src, encoding='utf-8', errors='replace'):
    m = re.match(r'\s*([A-Za-z0-9_]+)\s*=\s*([0-9A-Fa-f]+)\s*$', line)
    if m and want.match(m.group(1).lower()) and len(m.group(2)) in (32, 64):
        out.append(f'{m.group(1).lower()} = {m.group(2)}\n'); kept += 1
    else:
        dropped += 1
open(dst, 'w', newline='\n').write(''.join(out))
print(f'kept={kept} dropped={dropped}')
masters = sorted(l.split()[0] for l in out if l.startswith('master_key_'))
print('master keys:', masters[0] if masters else None, '..', masters[-1] if masters else None, f'({len(masters)})')
