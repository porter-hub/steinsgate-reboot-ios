#!/usr/bin/env python3
# NVN stage records from the nn::gfx shader files (BNSH) embedded in a module image, for the B shader chain
# (gfx/tools/dmsh_build.py). This is the records() step of references/06-graphics-nvn-metal.md for a game that keeps
# its shaders as BNSH in the executable instead of in a package; it reads no engine format beyond BNSH.
#
# Layout (checked on every file; any deviation is a [RED] line and the run fails):
#   A BNSH file starts with 'BNSH', u32 0, u32 version, u16 byte-order mark 0xfeff, and holds its size at +0x1c; the
#   'grsc' container block follows at +0x60. Offsets inside the file are relative to its start (the file's relocation
#   table turns them into pointers when the game loads it; the image holds the unrelocated offsets).
#   Each NVN stage is described by a code entry, 8-byte aligned:
#       u64 control offset, u64 code offset, u32 code size, u32 control size (0x880)
#   whose control offset points at a control section (0x880 bytes, starts 0x98761234) and whose code offset points at
#   the stage code (starts 0x12345678: 0x30 header + SPH + Maxwell code; compute code has the header but no SPH).
#   Every control section inside a file must be named by at least one code entry, and every control section in the
#   image must lie inside a BNSH file: otherwise this tool has misread the format, and says so instead of guessing.
#   Control +0x700 / +0x704: c1 start / end relative to the code start (dmsh_build.py reads c1 from there);
#   +0x714: NVN stage (0 vertex, 1 fragment, 2 geometry, 3 tess control, 4 tess evaluation, 5 compute); for graphics
#   stages it must agree with the SPH shader type (word 0 bits 10..13: 1 vertex, 2 tess control, 3 tess evaluation,
#   4 geometry, 5 pixel).
# Key = sha256(control || code)[:16 hex] with the code length from control +0x704 (which must equal the entry's code
# size), the same key the host computes in nvnProgramSetShaders (gfx/nvn/hle_nvn.c stage_key). The control section
# alone is not a key: one control section can be shared by many different stage programs (this port's game: 488
# programs under 94 control sections).
#
# Output (all game-derived data stays under work/):
#   <out>/packs/<vs|ps|gs|hs|ds|cs>.bin  records 'BTCDBTCD' + 0x30 zero bytes + code + control (dmsh_build.py records())
#   <out>/shader_map.tsv                 <pack> <index> <stage> <key> <origin>  (gfx/nvn/shader_map.tsv format)
#   <out>/shader_map.tsv.conflicts.tsv   header only (one key never maps to two different records: checked, else [RED])
#   Origin: <module>.rodata@<control offset> (the first place the record was seen).
# Usage: bnsh_nvn.py <flat module image> <out dir> [--module main]   (image: tools/nsolib.py extract)
#        bnsh_nvn.py --selftest                                       (synthetic files; last line BNSH SELFTEST OK)
import argparse, hashlib, os, struct, sys

CTRL, CODE, CTRL_LEN = struct.pack('<I', 0x98761234), struct.pack('<I', 0x12345678), 0x880
STAGES = [('vs', '顶点'), ('ps', '像素'), ('gs', '几何'), ('hs', '外壳'), ('ds', '域'), ('cs', '计算')]
SPH_STAGE = {1: 0, 2: 3, 3: 4, 4: 2, 5: 1}


def find_all(d, pat, lo=0, hi=None):
    hi = len(d) if hi is None else hi; out = []; i = d.find(pat, lo, hi)
    while i >= 0: out.append(i); i = d.find(pat, i + 1, hi)
    return out


def stage_key(ctl, code):
    return hashlib.sha256(ctl + code[:struct.unpack_from('<I', ctl, 0x704)[0]]).hexdigest()[:16]


def bnsh_files(img):
    """-> [(start, size)] of every BNSH file in the image; [RED] on a header that does not check out"""
    out, bad = [], []
    for s in find_all(img, b'BNSH'):
        if s % 8: continue   # a string or a coincidence: real files are aligned (the BNSH alignment field is >= 8)
        if img[s + 4:s + 8] != bytes(4) or img[s + 0xc:s + 0xe] != b'\xff\xfe' or img[s + 0x60:s + 0x64] != b'grsc':
            bad.append(f'{s:#x}: header (zero word / byte-order mark / grsc block)'); continue
        size = struct.unpack_from('<I', img, s + 0x1c)[0]
        if not 0x60 < size <= len(img) - s: bad.append(f'{s:#x}: size {size:#x}'); continue
        out.append((s, size))
    return out, bad


def records(img, module='main'):
    """-> ({key: (stage, code + control, origin)}, problems)"""
    files, bad = bnsh_files(img)
    recs, used_ctl = {}, set()
    for s, size in files:
        f = img[s:s + size]
        ctls = set(find_all(f, CTRL))
        named = set()
        for e in range(0, size - 0x18, 8):
            co, xo, xsz, csz = struct.unpack_from('<QQII', f, e)
            if csz != CTRL_LEN or co not in ctls or not (0 < xsz <= size - xo) or f[xo:xo + 4] != CODE: continue
            ctl, code = f[co:co + CTRL_LEN], f[xo:xo + xsz]
            st = struct.unpack_from('<I', ctl, 0x714)[0]
            c1o, c1t = struct.unpack_from('<2I', ctl, 0x700)
            if st > 5: bad.append(f'{s + co:#x}: control +0x714 stage {st}'); continue
            if not (0x30 <= c1o <= c1t <= xsz): bad.append(f'{s + co:#x}: c1 range {c1o:#x}..{c1t:#x} code {xsz:#x}'); continue
            if st != 5:
                sph = SPH_STAGE.get((struct.unpack_from('<I', code, 0x30)[0] >> 10) & 15)
                if sph != st: bad.append(f'{s + co:#x}: control says stage {st}, SPH says {sph}'); continue
            if struct.unpack_from('<I', ctl, 0x704)[0] != xsz: bad.append(f'{s + co:#x}: code size {xsz:#x} but control +0x704 says {struct.unpack_from("<I", ctl, 0x704)[0]:#x}'); continue
            named.add(co)
            key = stage_key(ctl, code); rec = code + ctl
            if key in recs:
                if recs[key][1] != rec: bad.append(f'key {key}: two different records ({recs[key][2]}, {module}.rodata@{s + co:#x})')
                continue
            recs[key] = (st, rec, f'{module}.rodata@{s + co:#x}')
        for co in sorted(ctls - named): bad.append(f'BNSH @{s:#x}: control section @{s + co:#x} named by no code entry')
        used_ctl |= {s + co for co in ctls}
    for o in find_all(img, CTRL):
        if o % 4 == 0 and o not in used_ctl: bad.append(f'control section @{o:#x} outside every BNSH file')
    return recs, bad, len(files)


def write(recs, out):
    os.makedirs(os.path.join(out, 'packs'), exist_ok=True)
    rows, per = [], {}
    for s, (tag, zh) in enumerate(STAGES):
        keys = sorted(k for k, v in recs.items() if v[0] == s)
        if not keys: continue
        with open(os.path.join(out, 'packs', tag + '.bin'), 'wb') as f:
            for i, k in enumerate(keys):
                f.write(b'BTCDBTCD' + bytes(0x30) + recs[k][1])
                rows.append(f'{tag}.bin\t{i}\t{zh}\t{k}\t{recs[k][2]}\n')
        per[tag] = len(keys)
    with open(os.path.join(out, 'shader_map.tsv'), 'w', encoding='utf-8') as f:
        f.write('# NVN stage records from embedded BNSH files (gfx/tools/bnsh_nvn.py): pack <TAB> index <TAB> stage <TAB> key = sha256(control + code)[:16] <TAB> origin\n')
        f.writelines(rows)
    with open(os.path.join(out, 'shader_map.tsv.conflicts.tsv'), 'w', encoding='utf-8') as f:
        f.write('# key <TAB> chosen pairing (none: gfx/tools/bnsh_nvn.py refuses conflicting records)\n')
    return per


def synth(stages, orphan=False):
    """a synthetic image with one BNSH file holding the given stages (for --selftest)"""
    f = bytearray(0x4000); f[0:4] = b'BNSH'; f[0xc:0xe] = b'\xff\xfe'; f[0x60:0x64] = b'grsc'
    struct.pack_into('<I', f, 0x1c, len(f))
    for i, st in enumerate(stages):
        co, xo, xsz = 0x400 + i * 0x900, 0x2000 + i * 0x200, 0x100
        ctl = bytearray(CTRL_LEN); ctl[:4] = CTRL; struct.pack_into('<3I', ctl, 0x700, 0x30, xsz, 0); struct.pack_into('<I', ctl, 0x714, st)
        ctl[0x10] = i   # distinct controls, distinct keys
        f[co:co + CTRL_LEN] = ctl
        code = bytearray(xsz); code[:4] = CODE
        if st != 5: struct.pack_into('<I', code, 0x30, {0: 1, 1: 5}[st] << 10)
        f[xo:xo + xsz] = code
        if not (orphan and i == len(stages) - 1): struct.pack_into('<QQII', f, 0x100 + i * 0x18, co, xo, xsz, CTRL_LEN)
    return bytes(0x100) + bytes(f) + bytes(0x100)


def selftest():
    fails = []
    def want(name, cond):
        print(('PASS ' if cond else 'FAIL ') + name)
        if not cond: fails.append(name)
    recs, bad, n = records(synth([0, 1, 1]))
    want('one file, three stages paired', n == 1 and len(recs) == 3 and not bad)
    want('stages from control +0x714', sorted(v[0] for v in recs.values()) == [0, 1, 1])
    want('origin is the control offset', all(v[2].startswith('main.rodata@0x') for v in recs.values()))
    img = bytearray(synth([1, 1])); a, b2 = [i for i in range(len(img)) if img[i:i + 4] == CTRL][:2]
    img[b2 + 0x10] = img[a + 0x10]   # the same control section for two different codes
    x2 = [i for i in range(len(img)) if img[i:i + 4] == CODE][1]; img[x2 + 0x40] = 0xab
    recs, bad, _ = records(bytes(img))
    want('one control with two codes gives two keys', len(recs) == 2 and not bad)
    _, bad, _ = records(synth([0, 1], orphan=True))
    want('NEG a control no code entry names is refused', any('named by no code entry' in b for b in bad))
    img = bytearray(synth([0, 1])); o = img.find(CTRL); struct.pack_into('<I', img, o + 0x714, 1)
    _, bad, _ = records(bytes(img))
    want('NEG control stage disagreeing with the SPH is refused', any('SPH says' in b for b in bad))
    img = bytearray(synth([0])) + bytearray(CTRL) + bytes(CTRL_LEN)
    _, bad, _ = records(bytes(img))
    want('NEG a control outside every BNSH file is refused', any('outside every BNSH' in b for b in bad))
    print('BNSH SELFTEST OK' if not fails else 'BNSH SELFTEST FAILED')
    return not fails


def main():
    if sys.argv[1:] == ['--selftest']: sys.exit(0 if selftest() else 1)
    a = argparse.ArgumentParser(); a.add_argument('image'); a.add_argument('out'); a.add_argument('--module', default='main')
    a = a.parse_args()
    img = open(a.image, 'rb').read()
    recs, bad, nfiles = records(img, a.module)
    for b in bad: print('[RED] ' + b)
    if bad: print(f'BNSH FAILED ({len(bad)} problems)'); sys.exit(1)
    per = write(recs, a.out)
    print(f'BNSH OK {nfiles} files, {len(recs)} unique stage records ' + ' '.join(f'{k}={v}' for k, v in per.items()))


if __name__ == '__main__': main()
