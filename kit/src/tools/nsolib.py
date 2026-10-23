#!/usr/bin/env python3
# switch-ios-port: NSO parsing library (generalized from d2r-mobile dm/tools/{nso.py,fn.py,patch_nso.py}, MIT).
# One place for everything the tools need to know about a Switch executable module:
#   NSO header -> flat image (LZ4 segments + bss), segment bounds, build id (module id),
#   MOD0 -> .dynamic -> dynsym / dynstr / RELA / JMPREL, imports vs exports,
#   .eh_frame_hdr -> sorted function starts.
# CLI:
#   nsolib.py info <nso>                 header, segments, build id, counts
#   nsolib.py extract <nso> <out.bin>    write the flat image (what sdis.py and friends read)
#   nsolib.py imports <nso>              one undefined symbol per line
#   nsolib.py exports <nso>              defined symbols: "offset size name"
import os, struct, sys

R_AARCH64_ABS64, R_AARCH64_GLOB_DAT, R_AARCH64_JUMP_SLOT, R_AARCH64_RELATIVE = 257, 1025, 1026, 1027
DT = {'NULL': 0, 'PLTRELSZ': 2, 'STRTAB': 5, 'SYMTAB': 6, 'RELA': 7, 'RELASZ': 8, 'INIT': 12, 'JMPREL': 23, 'INIT_ARRAY': 25, 'INIT_ARRAYSZ': 27,
      'RELRSZ': 35, 'RELR': 36, 'RELRENT': 37}
# Relocation formats the tools do not decode: refuse instead of silently reading an incomplete set (DT_REL / DT_RELSZ,
# Android's packed DT_ANDROID_REL(A) / DT_ANDROID_RELR). DT_RELR (SDK 20+ modules) is decoded by relr_offsets().
DT_UNSUPPORTED_RELOC = {17: 'DT_REL', 18: 'DT_RELSZ', 0x6000000f: 'DT_ANDROID_REL', 0x60000011: 'DT_ANDROID_RELA',
                        0x6fffe000: 'DT_ANDROID_RELR'}


class Nso:
    """A decompressed NSO module. `img` is the flat memory image (offset == address relative to load base)."""

    def __init__(self, path_or_bytes):
        d = open(path_or_bytes, 'rb').read() if isinstance(path_or_bytes, str) else bytes(path_or_bytes)
        if d[:4] != b'NSO0':
            raise ValueError('not an NSO (magic %r); decrypt/extract the ExeFS first (hactool / nxdumptool)' % d[:4])
        self.raw = d
        flags = struct.unpack_from('<I', d, 0xc)[0]
        self.segs = [struct.unpack_from('<III', d, 0x10 + i * 0x10) for i in range(3)]   # (file_off, mem_off, size) for text, ro, data
        self.bss = struct.unpack_from('<I', d, 0x3c)[0]
        csz = struct.unpack_from('<III', d, 0x60)
        img = bytearray(self.segs[2][1] + self.segs[2][2] + self.bss)
        for i, (fo, mo, sz) in enumerate(self.segs):
            seg = d[fo:fo + csz[i]]
            if flags >> i & 1:
                import lz4.block
                seg = lz4.block.decompress(seg, uncompressed_size=sz)
            img[mo:mo + sz] = seg
        self.img = img
        self.text_end = self.segs[0][2]
        self.ro_off, self.data_off = self.segs[1][1], self.segs[2][1]
        self.build_id = d[0x40:0x50].hex()      # first 16 bytes of the 0x20-byte module id: the key for every per-build table
        self.module_id = d[0x40:0x60].hex()
        self._dyn = None

    # ---------- MOD0 / dynamic ----------
    @property
    def mod0(self):
        m0 = struct.unpack_from('<I', self.img, 4)[0]
        if self.img[m0:m0 + 4] != b'MOD0':
            raise ValueError('no MOD0 header')
        return m0

    def dynamic(self):
        if self._dyn is None:
            m0 = self.mod0
            o = m0 + struct.unpack_from('<i', self.img, m0 + 4)[0]
            t = {}
            while True:
                tag, v = struct.unpack_from('<qQ', self.img, o); o += 16
                if tag == 0: break
                t.setdefault(tag, v)
            self._dyn = t
        return self._dyn

    def cstr(self, off):
        return self.img[off:self.img.index(b'\0', off)].decode('utf-8', 'replace')

    def symbols(self):
        """[(index, name, value, size, shndx, info)] from .dynsym (index 0 skipped)."""
        t = self.dynamic()
        sym, strt = t[DT['SYMTAB']], t[DT['STRTAB']]
        n = (strt - sym) // 24        # NSO modules place .dynstr right after .dynsym
        out = []
        for i in range(1, n):
            nm, info, oth, shn, val, sz = struct.unpack_from('<IBBHQQ', self.img, sym + i * 24)
            out.append((i, self.cstr(strt + nm), val, sz, shn, info))
        return out

    def exports(self):
        return [(v, s, n) for _, n, v, s, shn, _ in self.symbols() if shn and n]

    def relr_offsets(self):
        """Offsets relocated by DT_RELR (packed R_AARCH64_RELATIVE, implicit addend = the word stored there).
        Each 8-byte entry is either an even address (relocate it; the next bitmap starts one word after it) or an odd
        bitmap: bit i (1..63) relocates where + (i - 1) * 8, then where advances by 63 words. SDK 20+ modules use it
        for most of their relative relocations (STEINS;GATE sdk: 15,011 of them, none visible in RELA)."""
        t = self.dynamic()
        base, size = t.get(DT['RELR'], 0), t.get(DT['RELRSZ'], 0)
        if not base:
            return []
        if t.get(DT['RELRENT'], 8) != 8 or size % 8:
            raise ValueError('DT_RELR with entry size %r / table size %#x: not the 8-byte format' % (t.get(DT['RELRENT']), size))
        out, where = [], None
        for k in range(size // 8):
            w = struct.unpack_from('<Q', self.img, base + 8 * k)[0]
            if w & 1 == 0:
                out.append(w); where = w + 8
            else:
                if where is None:
                    raise ValueError('DT_RELR starts with a bitmap entry (no base address)')
                b, i = w >> 1, 0
                while b:
                    if b & 1: out.append(where + 8 * i)
                    b >>= 1; i += 1
                where += 63 * 8
        return out

    def relocations(self):
        """[(offset, type, symbol_name_or_None, addend)] from RELA, JMPREL and DT_RELR (as R_AARCH64_RELATIVE with the
        implicit addend read from the image). Unknown relocation formats raise instead of returning an incomplete set:
        the rewriter's jump-table proofs and nvn_slots.py rely on seeing every relocation."""
        t = self.dynamic(); syms = {i: n for i, n, *_ in self.symbols()}
        bad = [nm for tag, nm in DT_UNSUPPORTED_RELOC.items() if tag in t]
        if bad:
            raise ValueError('unsupported relocation format %s in .dynamic: add a decoder (and a test) first' % ', '.join(bad))
        out = []
        for base_tag, size_tag in ((DT['RELA'], DT['RELASZ']), (DT['JMPREL'], DT['PLTRELSZ'])):
            base, size = t.get(base_tag, 0), t.get(size_tag, 0)
            for k in range(size // 24):
                off, info, add = struct.unpack_from('<QQq', self.img, base + k * 24)
                typ, si = info & 0xffffffff, info >> 32
                out.append((off, typ, syms.get(si) if si else None, add))
        if not os.environ.get('NSOLIB_NO_RELR'):   # negative-control switch only (tests/test_rewriter.py): the pre-0.5.1 behaviour
            for off in self.relr_offsets():
                out.append((off, R_AARCH64_RELATIVE, None, struct.unpack_from('<q', self.img, off)[0]))
        return out

    def imports(self):
        """Names referenced by named relocations but not defined in this module."""
        defined = {n for _, n, v, s, shn, _ in self.symbols() if shn}
        return sorted({n for _, typ, n, _ in self.relocations() if n and typ != R_AARCH64_RELATIVE and n not in defined})

    def referenced(self):
        """All names referenced by named relocations (defined here or not) -- what HLE_FORCE can intercept."""
        return sorted({n for _, typ, n, _ in self.relocations() if n and typ != R_AARCH64_RELATIVE})

    # ---------- function starts ----------
    def _ehdr_table(self):
        """[(function start, FDE offset)] from .eh_frame_hdr's binary search table (MOD0 +16)."""
        img = self.img; m0 = self.mod0
        ehs = m0 + struct.unpack_from('<i', img, m0 + 16)[0]

        def rd(o, enc):
            fmt, app = enc & 0xF, enc & 0x70
            v, sz = {0x3: (struct.unpack_from('<I', img, o)[0], 4), 0xB: (struct.unpack_from('<i', img, o)[0], 4),
                     0x4: (struct.unpack_from('<Q', img, o)[0], 8), 0xC: (struct.unpack_from('<q', img, o)[0], 8)}[fmt]
            if app == 0x10: v += o
            if app == 0x30: v += ehs
            return v, sz
        _, penc, cenc, tenc = img[ehs:ehs + 4]
        o = ehs + 4
        _, s = rd(o, penc); o += s
        n, s = rd(o, cenc); o += s
        out = []
        for _ in range(n):
            a, s = rd(o, tenc); o += s
            f, s = rd(o, tenc); o += s
            out.append((a, f))
        return out

    def function_starts(self):
        """Sorted function start offsets from .eh_frame_hdr's binary search table (MOD0 +16)."""
        return sorted({a for a, _ in self._ehdr_table() if a < self.text_end})

    def lsda_of(self, start):
        """LSDA address of the function starting at `start` (it has C++ landing pads, which the unwinder enters without
        any branch instruction), 0 if its FDE has none. Raises ValueError if the CIE/FDE cannot be parsed."""
        if not hasattr(self, '_fde'): self._fde = dict(self._ehdr_table())
        if start not in self._fde: raise ValueError(f'no FDE for {start:#x}')
        img = self.img

        def uleb(o):
            v = sh = 0
            while True:
                b = img[o]; o += 1; v |= (b & 0x7F) << sh; sh += 7
                if not b & 0x80: return v, o
        def ptr(o, enc):
            if enc == 0xFF: return 0, o
            fmt, app = enc & 0xF, enc & 0x70
            if app not in (0, 0x10): raise ValueError(f'pointer application {app:#x} unsupported')
            if fmt == 0x1 or fmt == 0x9: v, o2 = uleb(o)
            else:
                f, n = {0x0: ('<Q', 8), 0x2: ('<H', 2), 0x3: ('<I', 4), 0x4: ('<Q', 8), 0xA: ('<h', 2), 0xB: ('<i', 4), 0xC: ('<q', 8)}[fmt]
                v, o2 = struct.unpack_from(f, img, o)[0], o + n
            return (v + o if app == 0x10 and v else v), o2
        fde = self._fde[start]
        ln, cid = struct.unpack_from('<II', img, fde)
        if ln in (0, 0xFFFFFFFF) or cid == 0: raise ValueError(f'FDE {fde:#x} malformed')
        cie = fde + 4 - cid
        cln, zero, ver = struct.unpack_from('<IIB', img, cie)
        if zero != 0 or ver not in (1, 3): raise ValueError(f'CIE {cie:#x} malformed')
        o = cie + 9; aug = img[o:img.index(b'\0', o)].decode(); o += len(aug) + 1
        _, o = uleb(o); _, o = uleb(o)                       # code / data alignment (sleb read as uleb: size only)
        o = o + 1 if ver == 1 else uleb(o)[1]                # return address register
        renc = lenc = None
        if aug.startswith('z'):
            _, o = uleb(o)
            for c in aug[1:]:
                if c == 'R': renc = img[o]; o += 1
                elif c == 'L': lenc = img[o]; o += 1
                elif c == 'P': penc = img[o]; _, o = ptr(o + 1, penc)
                elif c in 'SB': pass
                else: raise ValueError(f'CIE {cie:#x}: augmentation {aug!r} unsupported')
        if lenc is None: return 0
        o = fde + 8
        _, o = ptr(o, renc if renc is not None else 0)
        _, o = ptr(o, (renc if renc is not None else 0) & 0x0F)
        _, o = uleb(o)
        lsda, _ = ptr(o, lenc)
        return lsda

    def strings(self, lo=None, hi=None, minlen=6):
        """Printable ASCII strings in [lo, hi) (default: ro segment)."""
        lo = self.ro_off if lo is None else lo
        hi = self.data_off if hi is None else hi
        cur, start = [], lo
        for i in range(lo, hi):
            c = self.img[i]
            if 0x20 <= c < 0x7f:
                if not cur: start = i
                cur.append(c)
            else:
                if len(cur) >= minlen: yield start, bytes(cur).decode()
                cur = []


def main(argv):
    if len(argv) < 3: sys.exit(__doc__ or 'see header')
    cmd, n = argv[1], Nso(argv[2])
    if cmd == 'info':
        print('build_id', n.build_id)
        for name, (fo, mo, sz) in zip(('text', 'ro', 'data'), n.segs): print(f'{name:5s} mem {mo:#x} size {sz:#x}')
        print('bss', hex(n.bss), 'image', hex(len(n.img)))
        try:
            print('symbols', len(n.symbols()), 'relocations', len(n.relocations()), 'imports', len(n.imports()), 'functions', len(n.function_starts()))
        except Exception as e:
            print('dynamic parse failed:', e)
    elif cmd == 'extract':
        open(argv[3], 'wb').write(n.img); print('wrote', argv[3], hex(len(n.img)))
    elif cmd == 'imports':
        print('\n'.join(n.imports()))
    elif cmd == 'exports':
        for v, s, nm in sorted(n.exports()): print(f'{v:#x} {s:#x} {nm}')
    else:
        sys.exit('unknown command ' + cmd)


if __name__ == '__main__':
    main(sys.argv)
