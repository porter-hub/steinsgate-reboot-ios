#!/usr/bin/env python3
# Derived from d2r-mobile dm/gfx/tools/nometal_run.py (MIT, (c) 2026 jhaizhou); vendored by switch-ios-port.
# Runs the shader build chain on a machine without Apple's Metal compiler (Windows / Linux / a Mac without Xcode; for use by tools/d2rm_pack.py).
# Usage: nometal_run.py <script.py> [args...]   Example: nometal_run.py dmsh_build.py <package dir> <cache dir>
# How: not a single file of the build chain is modified (their contents feed into the translation chain version, and any change would force a whole new generation of the beta shader libraries); only, within this process, the one entry through which they call external commands
# (subprocess.run) is taken over: the commands below are recognized and answered here, and everything else is passed through unchanged:
#   xcrun -sdk <x> metal --version              → answers "none" (the compiler is recorded as unused in the tool fingerprint → the chain version forms its own branch, and its cache never mixes with normal mode's)
#   xcrun -sdk <x> metal -c <source> -o <intermediate file>    → copies the source as the intermediate file (line endings normalized to LF)
#   xcrun -sdk <x> metallib <intermediate file> -o <library>      → copies the intermediate file verbatim as the library
#   which <name>                                 → looked up by Python itself
#   cp -c <a> <b>                              → ordinary copy
# Result: the "library files" (*_b.metallib / *_bf.metallib) contain shader source. When the graphics layer of the shell (DM_SHELL build) finds that a library is not a compiled one, it compiles it on the device
# (gfx/nvn/nvn_mtl.m); the system caches the compiled result, and from the second launch on nothing is compiled again.
# The one check that is lost: in normal mode Apple's compiler also verifies along the way that every source compiles; here that is not verified, and one that fails to compile only reports a failure when it is used on the device.
#
# Kit mode (kit/tools/pack.py, on the user's machine; all three set together):
#   DMSH_KIT_LOCK=<file>      the tool check of dmsh_chain.py compares this machine's tools with the lock the kit was
#                             built from (dmsh_chain.lock records the development Mac's tools: Apple's compiler, brew's
#                             spirv-cross). It is replaced, not dropped: the translator dlls and source fingerprint as in
#                             dmsh_chain.lock, the kit's own spirv-cross build for this platform by hash, and no metal.
#                             The chain version itself is unchanged (it hashes the lock file, not the tools), so the shell
#                             built from this tree loads what the kit builds.
#   DMSH_SPIRV_CROSS=<binary> the kit's spirv-cross (one source revision built for every platform, kit/spirv-cross):
#                             every `spirv-cross ...` call goes to it.
#   XLATE=<dir>/dmsh_xlate.dll the translator as a framework-dependent .NET assembly: calls of XLATE or its app host
#                             (<dir>/dmsh_xlate[.exe], which the kit does not ship) run `dotnet <dll> ...`.
import hashlib, os, platform, runpy, shutil, subprocess, sys

_real = subprocess.run


def _done(args, out=''):
    return subprocess.CompletedProcess(args, 0, stdout=out, stderr='')


KIT_SC = os.environ.get('DMSH_SPIRV_CROSS'); KIT_XL = os.environ.get('XLATE', '') if os.environ.get('DMSH_KIT_LOCK') else ''


def platform_tag():   # darwin-arm64 / linux-x86_64 / linux-aarch64 / win32-amd64
    return sys.platform + '-' + platform.machine().lower()


def run(args, *a, **k):
    text = k.get('text') or k.get('universal_newlines')
    enc = (lambda s: s) if text else (lambda s: s.encode())
    if isinstance(args, (list, tuple)) and args:
        c = [str(x) for x in args]
        if KIT_SC and c[0] == 'spirv-cross': return _real([KIT_SC, *c[1:]], *a, **k)
        if KIT_XL.endswith('.dll') and os.path.splitext(c[0])[0] in (KIT_XL[:-4], os.path.join(os.path.dirname(KIT_XL), 'dmsh_xlate')):
            return _real(['dotnet', KIT_XL, *c[1:]], *a, **k)
        if c[0] == 'xcrun' and 'metal' in c and '--version' in c: return _done(args, enc('none\n'))
        if c[0] == 'xcrun' and 'metal' in c and '-c' in c and '-o' in c:  # source = the first argument after metal that is neither an option nor the value of -o (the iOS command also carries a version option after -c)
            o = c.index('-o'); src = [x for i, x in enumerate(c[c.index('metal') + 1:], c.index('metal') + 1) if not x.startswith('-') and i != o + 1]
            if len(src) != 1: raise RuntimeError('nometal_run：认不出源码参数 %r' % c)
            with open(src[0], 'rb') as f: b = f.read()
            with open(c[o + 1], 'wb') as f: f.write(b.replace(b'\r\n', b'\n'))  # On Windows spirv-cross writes sources with CRLF line endings: normalize before they go into the install package, so the same input gives the same package on every system
            return _done(args, enc(''))
        if c[0] == 'xcrun' and 'metallib' in c and '-o' in c: shutil.copyfile(c[c.index('metallib') + 1], c[c.index('-o') + 1]); return _done(args, enc(''))
        if c[0] == 'xcrun': raise RuntimeError('nometal_run：没认出的 xcrun 命令 %r' % c)
        if c[0] == 'which' and len(c) == 2: return _done(args, enc(((KIT_SC if KIT_SC and c[1] == 'spirv-cross' else shutil.which(c[1])) or '') + '\n'))
        if c[0] == 'cp' and len(c) == 4 and c[1] == '-c': shutil.copyfile(c[2], c[3]); return _done(args, enc(''))
    return _real(args, *a, **k)


def kit_check_tools():
    """Kit mode's replacement for dmsh_chain.check_tools: same answer shape (a list of mismatches, empty = OK)."""
    import dmsh_chain
    want = dict(l.rstrip('\n').split('\t', 1) for l in open(os.environ['DMSH_KIT_LOCK'], encoding='utf-8') if '\t' in l and not l.startswith('#'))
    sha16 = lambda p: hashlib.sha256(open(p, 'rb').read()).hexdigest()[:16] if os.path.isfile(p) else 'missing'
    now = {d: sha16(os.path.join(os.path.dirname(KIT_XL), d)) for d in ('dmsh_xlate.dll', 'Ryujinx.Graphics.Shader.dll', 'Ryujinx.Common.dll')}
    now['xlate_source'] = run([os.path.join(os.path.dirname(KIT_XL), 'dmsh_xlate'), '--source-sha'], capture_output=True, text=True).stdout.strip()
    now['spirv-cross ' + platform_tag()] = sha16(KIT_SC or '')
    bad = [f'{k}：锁 {want.get(k)} / 实际 {v}' for k, v in now.items() if want.get(k) != v]
    if now['xlate_source'] != dmsh_chain.xlate_source_sha(): bad.append(f'翻译器源指纹 {now["xlate_source"]} 与随附源 {dmsh_chain.xlate_source_sha()} 不符')
    return bad


if __name__ == '__main__':
    if len(sys.argv) < 2: sys.exit(__doc__ or '用法：nometal_run.py <脚本.py> [参数…]')
    subprocess.run = run
    script = os.path.abspath(sys.argv[1]); sys.argv = sys.argv[1:]; sys.path.insert(0, os.path.dirname(script))
    if os.environ.get('DMSH_KIT_LOCK'):
        if not (KIT_SC and KIT_XL.endswith('.dll')): sys.exit('[RED] nometal_run：DMSH_KIT_LOCK 要和 DMSH_SPIRV_CROSS、XLATE=<dll> 一起设')
        import dmsh_chain; dmsh_chain.check_tools = kit_check_tools
    runpy.run_path(script, run_name='__main__')
