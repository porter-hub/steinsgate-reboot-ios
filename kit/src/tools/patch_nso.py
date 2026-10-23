#!/usr/bin/env python3
# Derived from d2r-mobile dm/tools/patch_nso.py (MIT, (c) 2026 jhaizhou); generalized by switch-ios-port.
# Generic NSO rewriter: Switch aarch64 module -> an image that executes natively under Apple's kernel.
# Usage: patch_nso.py <NSO file> <output dir> <module name> [--config game.json] [--ruleset NAME]
#
# Only the instructions that cannot run unchanged under XNU are touched:
#   x18 read/write          -> thread slot K18 (Apple owns x18 and may clobber it at any time)
#   mrs tpidr_el0           -> read slot KTLS;  msr tpidr_el0 -> write KTLS  (tpidr_el0 belongs to the OS)
#   mrs tpidrro_el0         -> read slot KTLR (Horizon ThreadLocalRegion pointer; on Apple tpidrro_el0 = TSD base)
#   svc #n                  -> host gate (no Horizon kernel)
#   mrs cntpct_el0/cntvct_el0 -> host gate (host answers as the 19.2 MHz Switch counter)
#   mrs cntfrq_el0          -> host gate answering 19.2 MHz (Apple reports 24 MHz; disable with "rewrite_cntfrq": false)
# Every rewritten word becomes a B into a patch area appended after the image; nothing is patched at run time.
# Thread slots are reached as  mrs tpidrro_el0 + key_page[idx]  : the key page (one RO 16K page appended to the
# image) holds pthread_key*8 per slot, written by the loader -- so the image never hard-codes a pthread key.
#
# Game-specific tables come from game.json, keyed by NSO build id (so a game update fails loudly, never silently):
#   "hooks": { "<build id>": {
#       "cuts":   [[off, "orig hex", "new hex", "reason", "all|host"], ...],   original bytes asserted first
#       "obs":    [[off, id, "orig word hex", "reason"], ...],                  observation gates (function entries)
#       "branch_gates": [[off, "orig word hex", target, svc_no, "reason"], ...], host decides via x8 (!=0 -> target)
#       "params": [[offA, "wordA", offB, "wordB", reg, "KEYNAME", "reason"], ...] movz/movk constant -> slot load
#       "rulesets": { "NAME": { same four keys } }                             extra sets enabled with --ruleset
#   } }
#   "param_keys": ["KRES0", ...]   extra slot names (must cover every KEY used in params)
# Outputs: <name>.patched.bin, .fixups.tsv (layout + key indices), .gates.tsv, .sites.tsv, .cuts.tsv, .params.tsv, .obs.tsv
import argparse, bisect, hashlib, json, os, re, struct, sys
from itertools import combinations
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nsolib import Nso
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

ap = argparse.ArgumentParser()
ap.add_argument('nso'); ap.add_argument('outdir'); ap.add_argument('name')
ap.add_argument('--config'); ap.add_argument('--ruleset', action='append', default=[])
A = ap.parse_args()
NAME = A.name
CFG = json.load(open(A.config, encoding='utf-8')) if A.config else {}

nso = Nso(A.nso)
IMG = bytearray(nso.img)
TEXT_END, RO_OFF, DATA_OFF, BUILD_ID = nso.text_end, nso.ro_off, nso.data_off, nso.build_id
KEY_PAGE_OFF = (len(IMG) + 0x3FFF) & ~0x3FFF
IMG.extend(bytes(KEY_PAGE_OFF - len(IMG) + 0x4000))
PATCH_OFF = (len(IMG) + 0xFFFF) & ~0xFFFF

hooks = CFG.get('hooks', {}).get(BUILD_ID, {})
if CFG.get('hooks') and not hooks:
    print(f'note: game.json has hooks but none for build id {BUILD_ID} ({NAME}); rewriting without them')
def merged(key):
    out = list(hooks.get(key, []))
    for rs in A.ruleset:
        sets = hooks.get('rulesets', {})
        if rs not in sets: sys.exit(f'ruleset {rs} not defined for build {BUILD_ID}')
        out += sets[rs].get(key, [])
    return out
cuts = [tuple(c) if len(c) == 5 else (*c, 'all') for c in merged('cuts')]
cuts = [(int(c[0], 0) if isinstance(c[0], str) else c[0], *c[1:]) for c in cuts]
for off, ob, nb, why, scope in cuts:
    ob, nb = bytes.fromhex(ob), bytes.fromhex(nb)
    assert len(ob) == len(nb) and IMG[off:off + len(ob)] == ob, f'cut original bytes differ at {off:#x}: {bytes(IMG[off:off + len(ob)]).hex()} (wrong build?)'
    IMG[off:off + len(nb)] = nb
SRC = bytes(IMG)       # the image after cuts and before any site is rewritten: what analyses must read

BASE_KEYS = ('K18', 'KTLS', 'KTLR', 'KGATE')
KEY_NAMES = BASE_KEYS + tuple(CFG.get('param_keys', []))
assert len(set(KEY_NAMES)) == len(KEY_NAMES) and len(KEY_NAMES) <= 16, KEY_NAMES
KEY_INDEX = {k: i for i, k in enumerate(KEY_NAMES)}
REWRITE_CNTFRQ = CFG.get('rewrite_cntfrq', True)

md = Cs(CS_ARCH_ARM64, CS_MODE_ARM); md.detail = True
patch = bytearray(); fix = []; gates = []; stats = {}
def stat(k): stats[k] = stats.get(k, 0) + 1
def here(): return PATCH_OFF + len(patch)
def emit(w, kind=None):
    if kind: fix.append((len(patch), kind))
    patch.extend(struct.pack('<I', w & 0xFFFFFFFF))

# ---- Encoders ----
def B(frm, to):
    off = to - frm; assert -(1 << 27) <= off < (1 << 27) and off % 4 == 0
    return 0x14000000 | ((off >> 2) & 0x3FFFFFF)
def STP(t, t2, n, off): return 0xA9000000 | (((off // 8) & 0x7F) << 15) | (t2 << 10) | (n << 5) | t
def LDP(t, t2, n, off): return 0xA9400000 | (((off // 8) & 0x7F) << 15) | (t2 << 10) | (n << 5) | t
def STP_PRE(t, t2, n, off): return 0xA9800000 | (((off // 8) & 0x7F) << 15) | (t2 << 10) | (n << 5) | t
def LDP_POST(t, t2, n, off): return 0xA8C00000 | (((off // 8) & 0x7F) << 15) | (t2 << 10) | (n << 5) | t
def STUR(t, n, off): return 0xF8000000 | ((off & 0x1FF) << 12) | (n << 5) | t   # stur xt,[xn,#off] (off may be negative)
def LDUR(t, n, off): return 0xF8400000 | ((off & 0x1FF) << 12) | (n << 5) | t
def LDRX(t, n, imm12=0): return 0xF9400000 | (imm12 << 10) | (n << 5) | t
def STRX(t, n, imm12=0): return 0xF9000000 | (imm12 << 10) | (n << 5) | t
def MRS_TPIDRRO(t): return 0xD53BD060 | t
def ADRP(dd, pc, target):
    imm = ((target & ~0xFFF) - (pc & ~0xFFF)) >> 12; assert -(1 << 20) <= imm < (1 << 20)
    return 0x90000000 | ((imm & 3) << 29) | (((imm >> 2) & 0x7FFFF) << 5) | dd
def ADDI(dd, n, imm12, sh=0): return 0x91000000 | (sh << 22) | (imm12 << 10) | (n << 5) | dd
def ADDR(dd, n, m): return 0x8B000000 | (m << 16) | (n << 5) | dd
def SUBI(dd, n, imm12, sh=0): return 0xD1000000 | (sh << 22) | (imm12 << 10) | (n << 5) | dd
def BR(n): return 0xD61F0000 | (n << 5)
def BLR(n): return 0xD63F0000 | (n << 5)
NOP = 0xD503201F

def emit_key_addr(reg, kind):
    fix.append((len(patch), kind)); emit(ADRP(reg, here(), KEY_PAGE_OFF)); emit(LDRX(reg, reg, KEY_INDEX[kind]))
def emit_slot_addr(base, key_reg, kind):
    """base = tpidrro_el0 on entry; on exit base = &TSD[key(kind)]. key_reg is clobbered."""
    emit_key_addr(key_reg, kind); emit(ADDR(base, base, key_reg))

def dis(w, addr):
    l = list(md.disasm(struct.pack('<I', w), addr)); assert len(l) == 1, hex(w)
    return l[0]
def regset(ins):
    r, w = ins.regs_access()
    return {ins.reg_name(x) for x in r}, {ins.reg_name(x) for x in w}
def num(name):
    m = re.fullmatch(r'[xw](\d+)', name or ''); return int(m.group(1)) if m else None
def has18(ins): return re.search(r'\b[xw]18\b', ins.op_str) is not None
def regs_in(ins):
    s = {int(n) for n in re.findall(r'\b[xw](\d+)\b', ins.op_str)}
    try:
        rd, wr = regset(ins); s |= {num(n) for n in rd | wr} - {None}
    except Exception: pass
    return s
def pick(avoid, n=2):
    out = [r for r in range(0, 16) if r not in avoid and r != 18][:n]; assert len(out) == n; return out

def subst(ins, S):
    """x18/w18 -> xS/wS: change only the 5-bit register fields (bits 0/5/10/16), verified against the disassembly text."""
    w = struct.unpack('<I', ins.bytes)[0]
    want = re.sub(r'\b([xw])18\b', lambda m: f'{m.group(1)}{S}', ins.op_str)
    pos = [p for p in (0, 5, 10, 16) if (w >> p) & 31 == 18]
    for k in range(len(pos), 0, -1):
        for c in combinations(pos, k):
            nw = w
            for p in c: nw = (nw & ~(31 << p)) | (S << p)
            try: dd = dis(nw, ins.address)
            except AssertionError: continue
            if dd.mnemonic == ins.mnemonic and dd.op_str == want: return nw
    raise RuntimeError(f'subst fail {ins.address:x} {ins.mnemonic} {ins.op_str}')

# ---- Rewrite blocks ----
# Scratch registers are saved BELOW sp (Darwin honours a 128-byte red zone for signal delivery; compilers do
# not use that area in AAPCS64 code). Every block ends with a B back to the original next instruction.
def sp_delta(ins):
    m = re.search(r'\[sp, #(-?(?:0x)?[0-9a-f]+)\]!', ins.op_str)
    if m: return int(m.group(1), 0)
    m = re.search(r'\[sp\], #(-?(?:0x)?[0-9a-f]+)$', ins.op_str)
    if m: return int(m.group(1), 0)
    assert not re.match(r'(sp|wsp),', ins.op_str), f'writes sp: {ins.address:x} {ins.op_str}'
    return 0

def site_plain(ins):
    S, T = pick(regs_in(ins))
    dlt = sp_delta(ins)
    save = -16 if dlt == 0 else -112          # sp-writing instructions store below sp: park scratch deeper
    assert dlt == 0 or dlt >= -96, f'sp delta {dlt} at {ins.address:x}'
    start = here()
    emit(STP(S, T, 31, save)); emit(MRS_TPIDRRO(T)); emit_slot_addr(T, S, 'K18'); emit(LDRX(S, T))
    emit(subst(ins, S))
    emit(STRX(S, T))                 # always write back: capstone under-reports writes (e.g. adds w18)
    emit(LDP(S, T, 31, save - dlt)); emit(B(here(), ins.address + 4))
    return start

def site_cond(ins):
    w = struct.unpack('<I', ins.bytes)[0]
    S, T = pick(regs_in(ins))
    tgt = ins.operands[-1].imm
    start = here()
    emit(STP(S, T, 31, -16)); emit(MRS_TPIDRRO(T)); emit_slot_addr(T, S, 'K18'); emit(LDRX(S, T))
    m = ins.mnemonic
    taken = here() + 4 * 3
    off = (taken - here()) >> 2
    nw = ((w & 0xFF000000) | ((off & 0x7FFFF) << 5) | S) if m in ('cbz', 'cbnz') else ((w & 0xFFF80000) | ((off & 0x3FFF) << 5) | S)
    pc = here(); emit(nw)
    dd = dis(nw, pc); assert dd.mnemonic == m and dd.operands[-1].imm == taken
    emit(LDP(S, T, 31, -16)); emit(B(here(), ins.address + 4))
    assert here() == taken
    emit(LDP(S, T, 31, -16)); emit(B(here(), tgt))
    return start

def site_pcrel(ins):
    tgt = ins.operands[1].imm
    S, T = pick(set())
    start = here()
    emit(STP(S, T, 31, -16)); emit(MRS_TPIDRRO(T)); emit_slot_addr(T, S, 'K18')
    pc = here(); emit(ADRP(S, pc, tgt))
    if ins.mnemonic == 'adr': emit(ADDI(S, S, tgt & 0xFFF))
    emit(STRX(S, T))
    emit(LDP(S, T, 31, -16)); emit(B(here(), ins.address + 4))
    return start

def site_br(ins, base, count):
    """Jump table `br x18`, target = base + 4i: stash x16 in the red zone -> equally spaced landing pads -> restore -> target."""
    start = here()
    emit(STUR(16, 31, -16)); emit(STUR(17, 31, -24))
    emit(MRS_TPIDRRO(16)); emit_slot_addr(16, 17, 'K18'); emit(LDRX(16, 16)); emit(LDUR(17, 31, -24))
    padbase = start + 4 * 24
    delta = padbase - base
    op = ADDI if delta >= 0 else SUBI; dl = abs(delta)
    ops = []; hi, lo = dl >> 12, dl & 0xFFF
    while hi: h = min(hi, 0xFFF); ops.append(op(16, 16, h, 1)); hi -= h
    if lo: ops.append(op(16, 16, lo, 0))
    assert len(ops) <= 12
    for o in ops: emit(o)
    emit(BR(16))
    while here() < padbase: emit(NOP)
    tails = padbase + 4 * count
    for i in range(count): emit(B(here(), tails + 8 * i))
    for i in range(count):
        emit(0xF85F0000 | (31 << 5) | 16)      # ldur x16,[sp,#-16]
        emit(B(here(), base + 4 * i))
    return start

def site_sysreg_read(ins, kind):
    """mrs xN, tpidr_el0 / tpidrro_el0 -> xN = thread slot value."""
    N = ins.bytes[0] & 31
    tmp = 16 if N != 16 else 17
    start = here()
    emit(STUR(tmp, 31, -16)); emit(MRS_TPIDRRO(N)); emit_slot_addr(N, tmp, kind); emit(LDRX(N, N)); emit(LDUR(tmp, 31, -16))
    emit(B(here(), ins.address + 4))
    return start

# CTR_EL0 as the Switch's Cortex-A57 reports it (TRM reset value): IminLine = DminLine = 4 (64-byte lines), L1Ip PIPT,
# ERG = CWG = 4. Apple's kernel traps `mrs ctr_el0` from user mode (SIGILL, measured on macOS 27); the SDK reads it for
# its cache-line loops (SDK 16+ nn::os::FlushDataCache). Lines reported smaller than the host's are safe (redundant
# cache operations), larger would skip lines.
CTR_EL0_SWITCH = 0x8444C004
def site_ctr(ins, N):
    """mrs xN, ctr_el0 -> movz/movk xN = CTR_EL0_SWITCH; b back. verify/structck.py checks these three words exactly."""
    a = ins.address
    if N in (18, 31): raise RuntimeError(f'unsupported form at {a:x}: mrs x{N}, ctr_el0 -- add an encoder (references/02-binary-rewrite.md)')
    start = here()
    emit(0xD2800000 | (CTR_EL0_SWITCH & 0xFFFF) << 5 | N)               # movz xN, #lo16
    emit(0xF2A00000 | (CTR_EL0_SWITCH >> 16) << 5 | N)                  # movk xN, #hi16, lsl #16
    emit(B(here(), a + 4))
    return start

# System instructions that run natively in user mode on Apple silicon (measured on macOS 27 with a SIGILL probe, the same
# kernel as iOS): barriers, hints, NZCV / FPCR / FPSR, DCZID_EL0, and the user cache-maintenance operations. tpidr*,
# cnt* and ctr_el0 are rewrite sites above. Anything else in the system-instruction space is a hard error, so a new SDK
# register (CTR_EL0 arrived with SDK 16) fails here offline instead of as SIGILL on the device.
SYSREG_NATIVE = re.compile(r'^(nop|yield|wfe|wfi|sev|sevl|hint|dmb|dsb|isb|sb|ssbb|pssbb|clrex|csdb|bti|esb|dgh|'
                           r'xpaclri|pacia1716|pacib1716|autia1716|autib1716|paciaz|paciasp|pacibz|pacibsp|autiaz|autiasp|autibz|autibsp)\b'
                           r'|^(mrs|msr) .*\b(nzcv|fpcr|fpsr|dczid_el0)\b'
                           r'|^dc (civac|cvac|cvau|cvap|cvadp|zva)\b|^ic ivau\b')

def site_tpidr_write(ins):
    N = struct.unpack('<I', ins.bytes)[0] & 31
    (T,) = pick({N}, 1)
    start = here()
    emit(0xF81F0000 | (31 << 5) | T)           # stur xT,[sp,#-16]
    tmp = next(x for x in (16, 17) if x not in (N, T))
    emit(STUR(tmp, 31, -24)); emit(MRS_TPIDRRO(T)); emit_slot_addr(T, tmp, 'KTLS'); emit(STRX(N, T)); emit(LDUR(tmp, 31, -24))
    emit(0xF85F0000 | (31 << 5) | T)           # ldur xT,[sp,#-16]
    emit(B(here(), ins.address + 4))
    return start

def site_blr(ins):
    """blr x18 -> x16 = K18 slot value; x30 = the original return point; br x16.
    The callee returns straight into the original text, so unwinding (C++ / IL2CPP exceptions) sees the original
    call site and no patch-area frame exists. x30 is the key-page temporary until it takes the return address.
    Only x16 (IP0) differs from the original at callee entry: AAPCS64 lets a veneer corrupt IP0/IP1 between any
    caller and its callee, so no callee can depend on it (verify/equiv.py masks x16 for these sites, nothing else)."""
    start = here(); ret = ins.address + 4
    emit(MRS_TPIDRRO(16)); emit_slot_addr(16, 30, 'K18'); emit(LDRX(16, 16))
    emit(ADRP(30, here(), ret)); emit(ADDI(30, 30, ret & 0xFFF))
    emit(BR(16))
    return start

def site_sp_x18(ins):
    """mov sp, x18 -> sp = K18 slot value. x16/x17 are parked below the OLD sp and restored through x17 = old sp
    after sp has moved. Emitted only when sp_x18_proven() showed new sp <= old sp (a variable-length allocation):
    the parked pair then lies inside the newly allocated block, or at most 16 bytes below the new sp (red zone),
    so a signal frame can never overwrite it."""
    start = here()
    emit(STP(16, 17, 31, -16)); emit(MRS_TPIDRRO(16)); emit_slot_addr(16, 17, 'K18'); emit(LDRX(16, 16))
    emit(ADDI(17, 31, 0))                 # mov x17, sp   (old sp)
    emit(ADDI(31, 16, 0))                 # mov sp, x16   (new sp)
    emit(LDP(16, 17, 17, -16))            # restore both through the old sp
    emit(B(here(), ins.address + 4))
    return start

def emit_gate_call():
    """Common gate prologue (after stp x29,x30,[sp,#-16]!, before blr x30): x30 = gate address from slot KGATE.
    x29 is borrowed for the key page lookup and immediately restored from the stack: the host walks the x29
    chain (backtraces, caller identification), so dm_gate must see the guest's original x29."""
    emit(MRS_TPIDRRO(30)); emit_slot_addr(30, 29, 'KGATE'); emit(LDRX(30, 30)); emit(LDRX(29, 31))

def site_gate(ins, kind, arg):
    """svc / mrs cnt*_el0 -> host gate. The host identifies the gate by its return point (this block's ldp)."""
    start = here()
    emit(STP_PRE(29, 30, 31, -16))
    emit_gate_call()
    emit(BLR(30))
    ret = here()
    emit(LDP_POST(29, 30, 31, 16))
    emit(B(here(), ins.address + 4))
    gates.append((ret, kind, arg, ins.address))
    return start

BRANCHY = ('adr', 'adrp', 'b', 'bl', 'br', 'blr', 'ret', 'cbz', 'cbnz', 'tbz', 'tbnz')
def relocatable(ins):
    return ins.mnemonic not in BRANCHY and not ins.mnemonic.startswith('b.') \
        and not (ins.mnemonic.startswith('ldr') and '[' not in ins.op_str) and not has18(ins)

def site_obs(off, oid, word):
    """Observation gate: enter the host (arg = id), restore x29/x30, run the original instruction, jump back."""
    ins = dis(word, off)
    assert relocatable(ins), f'observation gate instruction cannot be relocated {off:#x} {ins.mnemonic} {ins.op_str}'
    start = here()
    emit(STP_PRE(29, 30, 31, -16))
    emit_gate_call()
    emit(BLR(30))
    ret = here()
    emit(LDP_POST(29, 30, 31, 16))
    emit(word)
    emit(B(here(), off + 4))
    gates.append((ret, 'obs', oid, off))
    return start

def site_branch_gate(off, word, tgt, svc):
    """Branch gate: save x8, enter the host as `svc` (host sets x8), x8 != 0 -> jump to tgt, else run the original and continue."""
    ins = dis(word, off)
    assert relocatable(ins), f'branch gate instruction cannot be relocated {off:#x} {ins.mnemonic} {ins.op_str}'
    start = here()
    emit(STP_PRE(8, 31, 31, -16))
    emit(STP_PRE(29, 30, 31, -16))
    emit_gate_call(); emit(BLR(30))
    ret = here(); emit(LDP_POST(29, 30, 31, 16))
    emit(0x34000068)                               # cbz w8, +12
    emit(LDP_POST(8, 31, 31, 16)); emit(B(here(), tgt))
    emit(LDP_POST(8, 31, 31, 16)); emit(word); emit(B(here(), off + 4))
    gates.append((ret, 'svc', svc, off))
    return start

# ---- Exclusive windows ----
# A slot load/store between ldxr and stxr clears the exclusive monitor -> the loop can livelock.
# So an ldxr..stxr[+cbz] span (< 16 instructions) containing x18 is copied whole, with x18 loaded once before.
EXCL_LD = ('ldxr', 'ldaxr', 'ldxp', 'ldaxp', 'ldxrb', 'ldaxrb', 'ldxrh', 'ldaxrh')
EXCL_ST = ('stxr', 'stlxr', 'stxp', 'stlxp', 'stxrb', 'stlxrb', 'stxrh', 'stlxrh')
def find_windows(insl):
    out = []
    for i, ins in enumerate(insl):
        if ins.mnemonic not in EXCL_LD: continue
        j = i
        while j < len(insl) and insl[j].mnemonic not in EXCL_ST: j += 1
        if j >= len(insl) or j - i >= 16: continue
        if j + 1 < len(insl) and insl[j + 1].mnemonic in ('cbz', 'cbnz'): j += 1
        seg = insl[i:j + 1]
        if any(has18(x) for x in seg): out.append(seg)
    return out

# Negative control ONLY (tests/test_rewriter.py): reproduce the r40 exit order so the verifiers must go red.
NEG_WINEXIT = os.environ.get('PATCH_NSO_NEG_WINEXIT') == '1'
if NEG_WINEXIT: print('WARNING PATCH_NSO_NEG_WINEXIT=1: emitting the KNOWN-BAD r40 window exit order (negative control)', file=sys.stderr)

def site_window(seg):
    used = set()
    for x in seg: used |= regs_in(x)
    S, T = pick(used)
    lo, hi = seg[0].address, seg[-1].address + 4
    start = here()
    emit(STP(S, T, 31, -16)); emit(MRS_TPIDRRO(T)); emit_slot_addr(T, S, 'K18'); emit(LDRX(S, T))
    copy0 = here(); n = len(seg)
    exits = copy0 + 4 * n
    ex_targets = []; words = []
    for k, x in enumerate(seg):
        w = struct.unpack('<I', x.bytes)[0]
        if has18(x): w = subst(x, S)
        m = x.mnemonic
        if m in ('cbz', 'cbnz', 'tbz', 'tbnz') or m.startswith('b.'):
            tgt = x.operands[-1].imm
            if lo <= tgt < hi: newt = copy0 + (tgt - lo)
            else: ex_targets.append(tgt); newt = exits + 16 * (len(ex_targets) - (1 if NEG_WINEXIT else 0))   # stub 0 is the fall-out (see below)
            pc = copy0 + 4 * k; off = (newt - pc) >> 2
            if m in ('tbz', 'tbnz'): w = (w & 0xFFF8001F) | ((off & 0x3FFF) << 5)
            else: w = (w & 0xFF00001F) | ((off & 0x7FFFF) << 5)
            dd = dis(w, pc); assert dd.operands[-1].imm == newt and dd.mnemonic == m
        else:
            assert m not in ('adr', 'adrp', 'b', 'bl', 'br', 'blr', 'ret') and not (m == 'ldr' and '[' not in x.op_str), hex(x.address)
        words.append(w)
    for w in words: emit(w)
    # Exit stub order (d2r-mobile r41): when the last instruction of the copy has executed, control falls through into
    # the stub that physically follows the copy, so stub 0 MUST be the fall-out back to hi and the targets outside the
    # window come after it. Up to r40 the fall-out was emitted last: in a compare-and-swap window (b.ne to outside), a
    # successful store-exclusive then fell into the "compare not equal" exit -- the operation ran twice, lock-free lists
    # were popped twice. Random equivalence trials never drew the "equal" direction, so equiv.py now forces both
    # directions of every window branch, and structck.py checks stub 0 statically.
    for t in (ex_targets + [hi] if NEG_WINEXIT else [hi] + ex_targets):
        emit(STRX(S, T)); emit(LDP(S, T, 31, -16)); emit(B(here(), t)); emit(NOP)
    return start, lo, hi

# ---- Relative jump tables (ldrsw xI,[xB,xI,lsl #2]; add xI,xI,xB; br xI) ----
# The table holds int32 offsets from its own base xB. The rewrite reuses site_br's landing pads over the target
# range [lo, hi] (every 4-byte address in it maps to itself), which is exact only if every run-time target lies in
# that range. recognise_rel_table() therefore PROVES the premises and returns None (-> hard error) otherwise.
_relocs = None
def reloc_info():
    global _relocs
    if _relocs is None:
        rl = nso.relocations()
        _relocs = (sorted(o for o, *_ in rl), {a for o, t, nm, a in rl if t == 1027})   # (patched offsets, RELATIVE targets)
    return _relocs

def writes_reg(x, r):
    """Conservative: may instruction x write x<r>/w<r>? Text based (capstone's write sets miss writes)."""
    m, ops = x.mnemonic, x.op_str
    if m in ('bl', 'blr', 'svc'): return r <= 18 or r == 30             # caller-saved registers do not survive
    if re.search(rf'\[x{r}\b[^\]]*\]!', ops) or re.search(rf'\[x{r}\], ', ops): return True   # base writeback
    if m.startswith(('stxr', 'stlxr', 'stxp', 'stlxp')): return re.match(rf'w{r}\b', ops) is not None
    if m.startswith(('st', 'prfm')) or m in ('cmp', 'cmn', 'tst', 'ccmp', 'ccmn', 'fcmp', 'fcmpe', 'b', 'br', 'ret',
                                            'cbz', 'cbnz', 'tbz', 'tbnz', 'msr', 'nop', 'hint') or m.startswith('b.'):
        return False
    regs = [int(n) for n in re.findall(r'\b[xw](\d+)\b', ops.split('[')[0])]
    if not regs: return False
    multi = m.startswith(('ld', 'swp', 'cas'))                           # pairs and LSE atomics write several
    return r in (regs if multi else regs[:1])

def branch_targets_in(insl, fa, fb):
    return {x.operands[-1].imm for x in insl if (x.mnemonic in ('b', 'cbz', 'cbnz', 'tbz', 'tbnz') or x.mnemonic.startswith('b.'))
            and fa <= x.operands[-1].imm < fb}

def straight_def(insl, k, r, labels, lim=16):
    """Index of the last instruction before insl[k] that writes x<r>, walking back in straight-line code only."""
    for i in range(k - 1, max(-1, k - 1 - lim), -1):
        if writes_reg(insl[i], r): return i
        if insl[i].address in labels: return None
    return None

def rel_table_raw(insl, k, labels):
    """Shape of the br at insl[k]: `cmp wI,#N; b.hi; ... ldrsw xI,[xB,xI,lsl #2]; add xI,xI,xB; br xI` with nothing
    branching into (cmp, br] and nothing writing xI between the b.hi and the ldrsw.
    Returns (I, B, N, index of the ldrsw, index of the cmp) or None."""
    if k < 4: return None
    ld, ad, br = insl[k - 2], insl[k - 1], insl[k]
    I = num(br.op_str)
    m = re.fullmatch(rf'x{I}, \[x(\d+), x{I}, lsl #2\]', ld.op_str) if (I is not None and ld.mnemonic == 'ldrsw') else None
    if not m: return None
    B = int(m.group(1))
    if B == I or ad.mnemonic != 'add' or ad.op_str != f'x{I}, x{I}, x{B}': return None
    for i in range(k - 3, max(-1, k - 12), -1):
        x = insl[i]
        if x.mnemonic == 'cmp' and insl[i + 1].mnemonic == 'b.hi':
            mm = re.fullmatch(rf'w{I}, #(0x[0-9a-f]+|\d+)', x.op_str)
            if not mm: return None
            if any(insl[i].address < t <= br.address for t in labels): return None
            if any(writes_reg(insl[q], I) for q in range(i + 2, k - 2)): return None
            return I, B, int(mm.group(1), 0), k - 2, i
        if writes_reg(x, I): return None
    return None

def table_base(insl, idx, at, B, labels, preds, fa):
    """Table base on every path reaching insl[at]: the unique add xB,xB,#lo whose own input is a unique adrp xB."""
    def reaching_defs(k0):
        defs, seen, stack = set(), set(), [insl[k0].address]
        while stack:
            y = stack.pop()
            if y == fa: return None                                    # the function entry reaches it undefined
            ps = preds.get(y)
            if not ps: return None                                     # entered by no visible edge
            for p in ps:
                if p in seen: continue
                seen.add(p)
                if writes_reg(insl[idx[p]], B): defs.add(p)
                else: stack.append(p)
        return defs
    d = reaching_defs(at)
    if not d or len(d) != 1: return None
    add = insl[idx[next(iter(d))]]
    mm = re.fullmatch(rf'x{B}, x{B}, #(0x[0-9a-f]+|\d+)', add.op_str) if add.mnemonic == 'add' else None
    if not mm: return None
    d2 = reaching_defs(idx[add.address])
    if not d2 or len(d2) != 1: return None
    ap = insl[idx[next(iter(d2))]]
    if ap.mnemonic != 'adrp' or not ap.op_str.startswith(f'x{B},'): return None
    return ap.operands[1].imm + int(mm.group(1), 0)

def guess_base(insl, kl, B):
    """Straight-line guess of the table base: nearest `add xB,xB,#lo` before insl[kl] and the adrp feeding it."""
    ka = next((i for i in range(kl - 1, max(-1, kl - 64), -1) if writes_reg(insl[i], B)), None)
    if ka is None: return None
    mm = re.fullmatch(rf'x{B}, x{B}, #(0x[0-9a-f]+|\d+)', insl[ka].op_str) if insl[ka].mnemonic == 'add' else None
    if not mm: return None
    kp = next((i for i in range(ka - 1, max(-1, ka - 64), -1) if writes_reg(insl[i], B)), None)
    if kp is None or insl[kp].mnemonic != 'adrp' or not insl[kp].op_str.startswith(f'x{B},'): return None
    return insl[kp].operands[1].imm + int(mm.group(1), 0)

def rel_table_targets(T, N):
    if not (RO_OFF <= T and T + 4 * (N + 1) <= DATA_OFF): return None
    offs, _ = reloc_info(); i = bisect.bisect_left(offs, T)
    if i < len(offs) and offs[i] < T + 4 * (N + 1): return None    # the loader would rewrite the table
    return [T + struct.unpack_from('<i', IMG, T + 4 * e)[0] for e in range(N + 1)]

def recognise_rel_table(insl, j, fa, fb):
    """`ldrsw x18,[xB,x18,lsl #2]; add x18,x18,xB; br x18` -> ((lo, n), None): every possible target is lo + 4k, k < n.
    (None, reason) unless all premises are proven:
      - the function has no landing pads (LSDA) and takes no code address inside itself (computed goto);
      - every other `br` in it is a provable relative table or not a table at all (tail call);
      - xB is adrp+add of a read-only, relocation-free table in ro on EVERY path reaching the ldrsw (backward walk
        over the function's CFG; the entry must not reach it undefined);
      - the index is bounded by `cmp w18,#N; b.hi`, was written as a 32-bit value (upper half zero), and nothing
        branches into the span between that write and the br;
      - every target is 4-aligned inside the function."""
    try:
        if nso.lsda_of(fa): return None, 'function has landing pads (LSDA)'
    except ValueError as e:
        return None, f'eh_frame: {e}'
    labels = branch_targets_in(insl, fa, fb)
    raw = rel_table_raw(insl, j, labels)
    if not raw or raw[0] != 18: return None, 'not cmp/b.hi + ldrsw/add/br on x18'
    _, B, N, kl, kc = raw
    # no code address of this function taken anywhere (computed goto / address tables we cannot see through)
    _, rel_targets = reloc_info()
    if any(fa < t < fb for t in rel_targets): return None, 'a relocation stores a code address of this function'
    lo_pg, hi_pg = fa & ~0xFFF, (fb + 0xFFF) & ~0xFFF
    for x in insl:
        if x.mnemonic == 'adr' and fa <= x.operands[1].imm < fb: return None, f'adr takes a code address at {x.address:#x}'
        if x.mnemonic == 'adrp' and lo_pg <= x.operands[1].imm < hi_pg: return None, f'adrp to the pages of this function at {x.address:#x}'
    idx = {x.address: k for k, x in enumerate(insl)}
    # CFG predecessors inside the function; indirect branches: provable tables add edges, a `br` right after a
    # register add (a table shape we cannot prove) refuses, anything else is a tail call
    base_preds, tables = {}, {}
    for k, x in enumerate(insl):
        m = x.mnemonic
        if m in ('b', 'cbz', 'cbnz', 'tbz', 'tbnz') or m.startswith('b.'):
            t = x.operands[-1].imm
            if fa <= t < fb: base_preds.setdefault(t, []).append(x.address)
        if m == 'br':
            r = rel_table_raw(insl, k, labels)
            if r is None:
                if k and insl[k - 1].mnemonic == 'add' and insl[k - 1].op_str.startswith(x.op_str + ','): return None, f'unprovable table-like br at {x.address:#x}'
                continue
            tables[k] = r
        if m not in ('b', 'br', 'ret', 'brk', 'udf', 'hlt') and x.address + 4 < fb:
            base_preds.setdefault(x.address + 4, []).append(x.address)
    # fixpoint: each table's base is proven over the CFG that includes every table's edges. Start from the
    # straight-line guess (nearest adrp+add of xB); a wrong guess can only make a proof fail, never pass.
    targets = {}
    for k, (_, b2, n2, kl2, _) in tables.items():
        T0 = guess_base(insl, kl2, b2)
        tg0 = rel_table_targets(T0, n2) if T0 is not None else None
        if tg0 is None: return None, f'no straight-line table base for the br at {insl[k].address:#x}'
        targets[k] = tuple(tg0)
    for _ in range(8):
        preds = {a: list(v) for a, v in base_preds.items()}
        for k, tg in targets.items():
            for t in tg: preds.setdefault(t, []).append(insl[k].address)
        new = {}
        for k, (_, b2, n2, kl2, _) in tables.items():
            T2 = table_base(insl, idx, kl2, b2, labels, preds, fa)
            tg2 = rel_table_targets(T2, n2) if T2 is not None else None
            if tg2 is None or not all(fa <= t < fb and t % 4 == 0 for t in tg2): return None, f'table base not proven on every path for the br at {insl[k].address:#x}'
            new[k] = tuple(tg2)
        if new == targets: break
        targets = new
    else:
        return None, 'table edges do not converge'
    tg = targets[j]
    # the index: last written as a 32-bit value before the cmp, nothing branching in between (table targets included)
    all_labels = labels | {t for v in targets.values() for t in v}
    kd = straight_def(insl, kc, 18, all_labels)
    if kd is None or not insl[kd].op_str.startswith('w18,'): return None, 'index not written as a 32-bit value in straight-line code'
    if any(insl[kd].address < t <= insl[j].address for t in all_labels): return None, 'a branch lands between the index write and the br'
    lo, hi = min(tg), max(tg)
    if (hi - lo) // 4 + 1 > 4096: return None, 'target range wider than 4096 instructions'
    return (lo, (hi - lo) // 4 + 1), None

# ---- Function boundaries (eh_frame_hdr) ----
starts = nso.function_starts()
start_set = set(starts)

# ---- Linear scan for every rewrite site (exact encodings first, capstone text for x18) ----
# Assumes .text contains no data islands; if your module has literal pools in text, exclude them here.
kinds = {}
for o in range(0, TEXT_END, 4):
    w = struct.unpack_from('<I', IMG, o)[0]
    if w & 0xFFE0001F == 0xD4000001: kinds[o] = ('svc', (w >> 5) & 0xFFFF); continue
    if w & 0xFFFFFFE0 == 0xD53BD040: kinds[o] = ('tpidr_r', None); continue
    if w & 0xFFFFFFE0 == 0xD51BD040: kinds[o] = ('tpidr_w', None); continue
    if w & 0xFFFFFFE0 == 0xD53BD060: kinds[o] = ('tpidrro_r', None); continue
    if w & 0xFFFFFFE0 == 0xD53BE020: kinds[o] = ('cntpct', w & 31); continue
    if w & 0xFFFFFFE0 == 0xD53BE040: kinds[o] = ('cntvct', w & 31); continue
    if REWRITE_CNTFRQ and w & 0xFFFFFFE0 == 0xD53BE000: kinds[o] = ('cntfrq', w & 31); continue
    if w & 0xFFFFFFE0 == 0xD53B0020 and not os.environ.get('PATCH_NSO_NO_CTR'): kinds[o] = ('ctr', w & 31); continue
    if w & 0xFFC00000 == 0xD5000000:                   # any other system instruction: allow-listed or a hard error
        si = next(md.disasm(IMG[o:o + 4], o), None)
        t = f'{si.mnemonic} {si.op_str}'.strip() if si else f'.word {w:#010x}'
        if not (SYSREG_NATIVE.match(t) or (os.environ.get('PATCH_NSO_NO_CTR') and 'ctr_el0' in t)):
            raise RuntimeError(f'system instruction at {o:x} is neither a rewrite class nor known to run in user mode: {t} -- '
                               'measure it on the device / Mac, then add an encoder or extend SYSREG_NATIVE (references/02-binary-rewrite.md)')
        continue
    # x18/w18 can only sit in the register fields at bits 0/5/10/16: skip the disassembler for every other word
    # (d2r-mobile v1.0.18; the site set is unchanged, the scan of a 27 MB .text gets several times faster)
    if not any((w >> p_) & 31 == 18 for p_ in (0, 5, 10, 16)): continue
    for ins in md.disasm(IMG[o:o + 4], o):
        if has18(ins): kinds[o] = ('x18', None)

def func_of(a):
    i = bisect.bisect_right(starts, a) - 1
    return (starts[i], starts[i + 1] if i + 1 < len(starts) else TEXT_END) if i >= 0 else (a, a + 4)
done = set(); windows = []; jumps = {}
funcs = sorted({func_of(a) for a, (k, _) in kinds.items() if k == 'x18'})
for fa, fb in funcs:
    insl = list(md.disasm(bytes(IMG[fa:fb]), fa))
    ws = find_windows(insl)
    for wseg in ws:
        windows.append(wseg); done |= {x.address for x in wseg}
    for j, ins in enumerate(insl):
        if ins.mnemonic == 'br' and ins.op_str == 'x18':
            base = width = None
            ctx = insl[max(0, j - 8):j]
            for p in reversed(ctx):
                if p.mnemonic == 'adr' and p.op_str.startswith('x18,'): base = p.operands[1].imm; break
            for p in reversed(ctx):
                if p.mnemonic == 'add' and p.op_str.startswith('x18, x18,') and 'lsl #2' in p.op_str:
                    idx = num(p.reg_name(p.operands[2].reg))
                    for q in reversed(ctx):
                        if q.mnemonic in ('ldrb', 'ldrsb') and num(q.reg_name(q.operands[0].reg)) == idx: width = 256
                        if q.mnemonic in ('ldrh', 'ldrsh') and num(q.reg_name(q.operands[0].reg)) == idx: width = 65536
                        if width: break
                    break
            if not (base and width):
                rel, why = recognise_rel_table(insl, j, fa, fb)
                if rel: base, width = rel; stat('br_rel')
            assert base and width, f'jump table shape not recognised at {ins.address:x}: write an encoder, do not guess' + (f' (relative table: {why})' if not (base and width) and why else '')
            jumps[ins.address] = (base, width)
    inwin = {x.address for wseg in ws for x in wseg}; heads = {wseg[0].address for wseg in ws}
    for x in insl:
        if x.mnemonic in ('b', 'bl', 'cbz', 'cbnz', 'tbz', 'tbnz') or x.mnemonic.startswith('b.'):
            t = x.operands[-1].imm
            assert not (t in inwin and t not in heads), f'branch into the middle of an exclusive window at {x.address:x}'

calls = []                                 # blr x18 sites: sites.tsv "C" rows (equiv masks only x16 there)

def sp_x18_proven(a):
    """`mov sp, x18` at a is a variable-length allocation: in straight-line code before it, x18 was last written by
    `sub x18, x18, xK` and before that by `mov x18, sp`, nothing in between writes sp or is a branch target, and
    the function has no landing pads. Then new sp = old sp - xK <= old sp (xK is an unsigned size; a size larger
    than the stack address faults in the original too). Returns None if proven, else the reason."""
    fa, fb = func_of(a)
    try:
        if nso.lsda_of(fa): return 'function has landing pads (LSDA)'
    except ValueError as e:
        return f'eh_frame: {e}'
    insl = list(md.disasm(SRC[fa:fb], fa)); k = next(i for i, x in enumerate(insl) if x.address == a)
    labels = branch_targets_in(insl, fa, fb) | {b + 4 * i for b, n in jumps.values() if fa <= b < fb for i in range(n)}
    ks = straight_def(insl, k, 18, labels, lim=32)
    if ks is None or insl[ks].mnemonic != 'sub' or not re.fullmatch(r'x18, x18, x\d+', insl[ks].op_str):
        return 'x18 not last written by sub x18, x18, xK in straight-line code'
    km = straight_def(insl, ks, 18, labels, lim=32)
    if km is None or insl[km].mnemonic != 'mov' or insl[km].op_str != 'x18, sp':
        return 'the sub does not start from mov x18, sp in straight-line code'
    for x in insl[km + 1:k]:
        if re.match(r'(sp|wsp),', x.op_str) or re.search(r'\[sp\b[^\]]*\]!|\[sp\], ', x.op_str): return f'sp written at {x.address:#x}'
    if any(insl[km].address < t <= a for t in labels): return 'a branch lands between mov x18, sp and the site'
    return None
def put_branch(at, to): struct.pack_into('<I', IMG, at, B(at, to))
def in_window(o): return any(seg[0].address <= o < seg[-1].address + 4 for seg in windows)
for wseg in windows:
    st, lo, hi = site_window(wseg); stat('window'); put_branch(lo, st)
for a in sorted(kinds):
    if a in done: continue
    k, arg = kinds[a]
    ins = dis(struct.unpack_from('<I', IMG, a)[0], a)
    if k == 'x18':
        m = ins.mnemonic
        if m == 'br': st = site_br(ins, *jumps[a]); stat('br')
        elif m in ('cbz', 'cbnz', 'tbz', 'tbnz'): st = site_cond(ins); stat('cond')
        elif m in ('adr', 'adrp'): st = site_pcrel(ins); stat('pcrel')
        elif m == 'blr' and ins.op_str == 'x18': st = site_blr(ins); stat('blr'); calls.append(a)
        elif m == 'mov' and ins.op_str == 'sp, x18':
            why = sp_x18_proven(a)
            if why: raise RuntimeError(f'unsupported x18 form at {a:x}: mov sp, x18 ({why}) -- add an encoder (references/02-binary-rewrite.md)')
            st = site_sp_x18(ins); stat('sp_x18')
        elif m in ('blr', 'ret', 'b', 'bl') or (m == 'ldr' and '[' not in ins.op_str):
            raise RuntimeError(f'unsupported x18 form at {a:x}: {m} {ins.op_str} -- add an encoder (references/02-binary-rewrite.md)')
        else: st = site_plain(ins); stat('plain')
    elif k == 'tpidr_r': st = site_sysreg_read(ins, 'KTLS'); stat(k)
    elif k == 'tpidrro_r': st = site_sysreg_read(ins, 'KTLR'); stat(k)
    elif k == 'tpidr_w': st = site_tpidr_write(ins); stat(k)
    elif k == 'svc': st = site_gate(ins, 'svc', arg); stat(k)
    elif k in ('cntpct', 'cntvct', 'cntfrq'): st = site_gate(ins, k, arg); stat(k)
    elif k == 'ctr': st = site_ctr(ins, arg); stat(k)
    put_branch(a, st)

def free_site(o):
    return o < TEXT_END and o not in kinds and not in_window(o)

# ---- Branch gates (host decides via x8) ----
for e in merged('branch_gates'):
    off, word, tgt, svc, why = int(str(e[0]), 0), int(e[1], 16), int(str(e[2]), 0), int(str(e[3]), 0), e[4]
    assert struct.unpack_from('<I', IMG, off)[0] == word, f'branch gate original word differs at {off:#x}'
    assert free_site(off), f'branch gate collides with a rewrite site {off:#x}'
    assert svc >= 0x80, f'branch gate {off:#x}: host call numbers start at 0x80'
    st = site_branch_gate(off, word, tgt, svc)
    cuts.append((off, struct.pack('<I', word).hex(), struct.pack('<I', B(off, st)).hex(), why, 'host'))
    put_branch(off, st); stat('branch_gate')

# ---- Observation gates ----
obs = merged('obs')
for e in obs:
    off, oid, ow, why = int(str(e[0]), 0), int(e[1]), e[2], e[3]
    assert 0 < oid < 64, f'observation gate id {oid} out of range (host OBS_MAX = 64)'
    w = struct.unpack_from('<I', IMG, off)[0]
    assert f'{w:08x}' == ow.lower(), f'observation gate original word differs at {off:#x}: {w:08x}'
    # Function entries only (unless "mid" is given): dm_gate restores every register, but host C code expects
    # AAPCS64 argument state, which only holds at an entry.
    assert off in start_set or (len(e) > 4 and e[4] == 'mid'), f'observation gate {off:#x} is not a function entry'
    assert free_site(off), f'observation gate collides with a rewrite site {off:#x}'
    put_branch(off, site_obs(off, oid, w)); stat('obs')

# ---- Parameter slots: a movz/movk constant pair -> load from a thread slot ----
# The first instruction jumps to a trampoline that loads the slot and returns to A+4; B becomes nop.
# xR must not be touched between A and B (asserted). Watch for compiler-derived masks of the same constant
# downstream (d2r-mobile hit one: `and x9, x25, #0x740` folded from two known widths) -- verify/structck.py checks.
params = merged('params')
for e in params:
    oa, wa, ob_, wb, r, key, why = int(str(e[0]), 0), e[1], int(str(e[2]), 0), e[3], int(e[4]), e[5], e[6]
    assert key in KEY_INDEX and key not in BASE_KEYS, f'param key {key} not declared in game.json param_keys'
    for o, w0 in ((oa, wa), (ob_, wb)):
        w = struct.unpack_from('<I', IMG, o)[0]
        assert f'{w:08x}' == w0.lower(), f'param original word differs at {o:#x}: {w:08x}'
        assert free_site(o), f'param collides with a rewrite site {o:#x}'
    ia, ib = dis(int(wa, 16), oa), dis(int(wb, 16), ob_)
    assert ia.mnemonic in ('mov', 'movz') and ib.mnemonic == 'movk' and ia.op_str.split(',')[0] in (f'x{r}', f'w{r}') \
        and ib.op_str.split(',')[0] in (f'x{r}', f'w{r}'), f'param pair at {oa:#x}/{ob_:#x} is not movz/movk x{r}: {ia.mnemonic} {ia.op_str} / {ib.mnemonic} {ib.op_str}'
    for o in range(oa + 4, ob_, 4):
        ins = dis(struct.unpack_from('<I', IMG, o)[0], o)
        assert r not in regs_in(ins), f'x{r} used between the param pair at {o:#x} {ins.mnemonic} {ins.op_str}'
    start = here()
    tmp = 16 if r != 16 else 17
    emit(STUR(tmp, 31, -16)); emit(MRS_TPIDRRO(r)); emit_slot_addr(r, tmp, key); emit(LDRX(r, r)); emit(LDUR(tmp, 31, -16)); emit(B(here(), oa + 4))
    struct.pack_into('<I', IMG, oa, B(oa, start)); struct.pack_into('<I', IMG, ob_, NOP); stat('param')

# ---- Outputs ----
os.makedirs(A.outdir, exist_ok=True)
P = lambda ext: os.path.join(A.outdir, f'{NAME}.{ext}')
open(P('patched.bin'), 'wb').write(IMG + bytearray(PATCH_OFF - len(IMG)) + patch)
with open(os.path.join(A.outdir, 'build_src.sha256'), 'w') as f:
    f.write(hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest() + '  patch_nso.py\n')
with open(P('fixups.tsv'), 'w') as f:
    f.write(f'# PATCH_OFF\t{PATCH_OFF:#x}\n# KEY_PAGE_OFF\t{KEY_PAGE_OFF:#x}\n# KEY_PAGE_SIZE\t0x4000\n# TEXT_END\t{TEXT_END:#x}\n# RO_OFF\t{RO_OFF:#x}\n# DATA_OFF\t{DATA_OFF:#x}\n')
    for k in KEY_NAMES: f.write(f'# KEY_{k}_IDX\t{KEY_INDEX[k]}\n')
with open(P('gates.tsv'), 'w') as f:
    for ret, k, arg, orig in gates: f.write(f'{ret:#x}\t{k}\t{arg}\t{orig:#x}\n')
with open(P('sites.tsv'), 'w') as f:
    for a in sorted(kinds):
        if a not in done: f.write(f'{a:#x}\t{kinds[a][0]}\n')
    for wseg in windows: f.write(f'{wseg[0].address:#x}\tW\t{wseg[-1].address + 4:#x}\n')
    for a, (b, c) in jumps.items(): f.write(f'{a:#x}\tJ\t{b:#x}\t{c}\n')
    for a in calls: f.write(f'{a:#x}\tC\n')
with open(P('cuts.tsv'), 'w') as f:
    f.write(f'# BUILD_ID\t{BUILD_ID}\n')
    for off, ob, nb, why, scope in cuts: f.write(f'{off:#x}\t{ob}\t{nb}\t{scope}\t{why}\n')
with open(P('params.tsv'), 'w') as f:
    f.write(f'# BUILD_ID\t{BUILD_ID}\n')
    for e in params: f.write(f'{int(str(e[0]), 0):#x}\t{int(str(e[2]), 0):#x}\tx{e[4]}\t{e[5]}\t{e[6]}\n')
with open(P('obs.tsv'), 'w') as f:
    f.write(f'# BUILD_ID\t{BUILD_ID}\n')
    for e in obs: f.write(f'{int(str(e[0]), 0):#x}\t{e[1]}\t{e[2]}\t{e[3]}\n')
print(NAME, BUILD_ID, stats, 'cuts', len(cuts), 'obs', len(obs), 'patch', len(patch), 'PATCH_OFF', hex(PATCH_OFF), 'slot_refs', len(fix), 'gates', len(gates))
