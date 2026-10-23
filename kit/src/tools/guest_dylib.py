#!/usr/bin/env python3
# Derived from d2r-mobile dm/tools/guest_dylib.py (MIT, (c) 2026 jhaizhou); generalized by switch-ios-port.
#
# Write the rewritten module images directly as one Apple-format dynamic library Guest.framework/Guest: it holds only segments, no symbols, and nothing other than the code.
# Uses only the Python standard library, calls no compiler, linker or any Apple tool, and produces byte-for-byte identical files on macOS / Linux / Windows.
# Who it is for: the shell install package (ios/tools/shell_build.sh) contains no game program; after users rewrite the module images from their own game files, the pack script calls this one
# to write this library and put it into the install package's Frameworks/, and then the whole package is signed with a sideloading tool. The host's loader (the DM_SHELL branch of host/loader.c) loads it and fetches the segments by section name.
# The section layout matches the embedded approach (ios/tools/gen_embed.py), so the loader does not need two variants:
#   __TEXT,__dm<name>x0   original text: [0, round16K(TEXT_END))
#   __DM<name>W,__img     writable segment: [round16K(TEXT_END), PATCH_OFF)
#   __TEXT,__dm<name>x1   patch area: [PATCH_OFF, round16K(end of file))
# Three points to note about the file format (all found by measurement on a real device / Mac):
#   · a dependency on /usr/lib/libSystem.B.dylib must be declared, otherwise the system refuses to load it ("must link with at least libSystem.dylib");
#   · every section is aligned to 16K and is a whole number of pages: the loader takes whole pages with vm_remap;
#   · __LINKEDIT goes last and stays at the end of the file: the signing tool appends the signature after it.
# Usage: guest_dylib.py <build output directory> <output directory> <module name...>     writes <output directory>/Guest.framework/{Guest,Info.plist}
import hashlib, os, struct, sys

PG = 0x4000
MIN_IOS = 26 << 16   # matches the project's minimum system version (ios/project.yml)


def r16(x): return (x + PG - 1) & ~(PG - 1)


def s16(s):
    b = s.encode(); assert len(b) <= 16, s; return b.ljust(16, b'\0')


def segment(name, addr, size, fileoff, filesize, prot, sects):
    b = struct.pack('<II16sQQQQiiII', 0x19, 72 + 80 * len(sects), s16(name), addr, size, fileoff, filesize, prot, prot, len(sects), 0)
    for sn, a, sz, flags in sects:
        b += struct.pack('<16s16sQQIIIIIIII', s16(sn), s16(name), a, sz, a, 14, 0, 0, flags, 0, 0, 0)   # file offset = address; alignment 2^14
    return b


def dylib_cmd(cmd, name):
    name += b'\0' * (8 - len(name) % 8)
    return struct.pack('<IIIIII', cmd, 24 + len(name), 24, 2, 0x10000, 0x10000) + name


def build(out, names):
    text, wsegs = [], []
    for n in names:
        hdr = dict(l[2:].split() for l in open(os.path.join(out, n + '.fixups.tsv')) if l.startswith('# '))
        te, po = int(hdr['TEXT_END'], 16), int(hdr['PATCH_OFF'], 16)
        img = open(os.path.join(out, n + '.patched.bin'), 'rb').read(); sz = len(img); a, end = r16(te), r16(sz)
        assert po % PG == 0 and a <= po <= end, (n, hex(te), hex(po), hex(sz))
        cut = lambda lo, hi: img[lo:min(hi, sz)].ljust(hi - lo, b'\0')
        text += [('__dm%sx0' % n, cut(0, a)), ('__dm%sx1' % n, cut(po, end))]
        wsegs.append(('__DM%sW' % n, cut(a, po)))
    off = PG; tsects = []; body = b''          # first page: file header and load commands
    for sn, b in text:
        tsects.append((sn, off, len(b), 0x80000400)); body += b; off += len(b)   # pure instructions section
    cmds = [segment('__TEXT', 0, off, 0, off, 5, tsects)]
    for sn, b in wsegs:
        cmds.append(segment(sn, off, len(b), off, len(b), 3, [('__img', off, len(b), 0)])); body += b; off += len(b)
    strtab = b'\0' * 8
    uuid = hashlib.sha256(body).digest()[:16]   # determined by content: the same input yields a byte-for-byte identical library on whatever machine it is built
    cmds += [segment('__LINKEDIT', off, PG, off, len(strtab), 1, []),
             dylib_cmd(0xd, b'@rpath/Guest.framework/Guest'),                    # LC_ID_DYLIB
             struct.pack('<II10I', 0x80000022, 48, *([0] * 10)),                 # LC_DYLD_INFO_ONLY: no relocations, no bindings, no exports
             struct.pack('<IIIIII', 0x2, 24, off, 0, off, len(strtab)),          # LC_SYMTAB: 0 symbols
             struct.pack('<II18I', 0xb, 80, *([0] * 18)),                        # LC_DYSYMTAB
             struct.pack('<II16s', 0x1b, 24, uuid),                              # LC_UUID
             struct.pack('<IIIIII', 0x32, 24, 2, MIN_IOS, MIN_IOS, 0),           # LC_BUILD_VERSION: platform 2 = iOS
             dylib_cmd(0xc, b'/usr/lib/libSystem.B.dylib')]                      # LC_LOAD_DYLIB
    lc = b''.join(cmds); assert 32 + len(lc) <= PG
    mh = struct.pack('<IiiIIIII', 0xfeedfacf, 0x0100000c, 0, 6, len(cmds), len(lc), 0x00100085, 0)   # arm64, dynamic library
    return (mh + lc).ljust(PG, b'\0') + body + strtab


INFO_PLIST = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>Guest</string>
<key>CFBundleIdentifier</key><string>%s</string>
<key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
<key>CFBundleName</key><string>Guest</string>
<key>CFBundlePackageType</key><string>FMWK</string>
<key>CFBundleShortVersionString</key><string>1.0</string>
<key>CFBundleVersion</key><string>1</string>
<key>MinimumOSVersion</key><string>26.0</string>
<key>CFBundleSupportedPlatforms</key><array><string>iPhoneOS</string></array>
</dict></plist>
'''


def write(out, dst, names, bundle_id='local.guest'):
    fw = os.path.join(dst, 'Guest.framework'); os.makedirs(fw, exist_ok=True)
    data = build(out, names)
    with open(os.path.join(fw, 'Guest'), 'wb') as f: f.write(data)
    with open(os.path.join(fw, 'Info.plist'), 'w', encoding='utf-8', newline='\n') as f: f.write(INFO_PLIST % bundle_id)
    return fw, len(data)


if __name__ == '__main__':
    if len(sys.argv) < 4: sys.exit('usage: guest_dylib.py <build output dir> <output dir> <module name...>')
    fw, n = write(sys.argv[1], sys.argv[2], sys.argv[3:])
    print('%s: %d bytes' % (fw, n))
