#!/usr/bin/env python3
# Installer kit self-test: everything that can be checked on this computer without game files. Runs in CI on Windows,
# macOS and Linux (.github/workflows/selftest.yml), and for a user with `Start-Windows.bat --selftest` /
# `bash start-linux.sh --selftest` / `bash Start-macOS.command --selftest` (pack.py hands --selftest to this file).
# Each check prints one line; the last line is SELFTEST OK or SELFTEST FAILED (n of m).
import hashlib, json, os, platform, re, shutil, subprocess, sys, tempfile, zipfile

KIT = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(KIT); SRC = os.path.join(KIT, 'src')
TAG = sys.platform + '-' + platform.machine().lower()   # gfx/tools/nometal_run.py platform_tag
LOCK = dict(l.rstrip('\n').split('\t', 1) for l in open(os.path.join(KIT, 'dmsh_kit.lock'), encoding='utf-8') if '\t' in l and not l.startswith('#'))
sha16 = lambda p: hashlib.sha256(open(p, 'rb').read()).hexdigest()[:16]
XL = os.path.join(KIT, 'dotnet', 'dmsh_xlate', 'dmsh_xlate.dll'); EX = os.path.join(KIT, 'dotnet', 'extractor', 'extractor.dll')
SC = os.path.join(KIT, 'spirv-cross', TAG, 'spirv-cross' + ('.exe' if sys.platform == 'win32' else ''))
results = []


def check(name):
    def wrap(fn):
        try: results.append((True, name, fn() or ''))
        except Exception as e: results.append((False, name, f'{type(e).__name__}: {e}'))
        return fn
    return wrap


def run(*a, **k): return subprocess.run(list(a), capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600, **k)


@check('python')
def _():
    assert sys.version_info >= (3, 12), platform.python_version()
    import capstone, lz4.block, PIL  # noqa: F401
    return f'{platform.python_version()}, capstone {capstone.__version__}, lz4, Pillow {PIL.__version__}'


@check('.NET 10 runtime')
def _():
    rt = [l for l in run('dotnet', '--list-runtimes').stdout.splitlines() if l.startswith('Microsoft.NETCore.App 10.')]
    assert rt, 'no Microsoft.NETCore.App 10.x'
    return rt[-1].split()[1]


@check(f'spirv-cross ({TAG})')
def _():
    assert os.path.isfile(SC), f'no binary for {TAG}'
    want = LOCK.get('spirv-cross ' + TAG); assert sha16(SC) == want, f'hash {sha16(SC)}, lock {want}'
    out = run(SC, '--revision'); rev = (out.stdout + out.stderr).strip()
    assert 'f11ba9f' in rev, rev
    return rev


@check('shader translator')
def _():
    for d in ('dmsh_xlate.dll', 'Ryujinx.Graphics.Shader.dll', 'Ryujinx.Common.dll'):
        assert sha16(os.path.join(os.path.dirname(XL), d)) == LOCK[d], f'{d} differs from the lock'
    got = run('dotnet', XL, '--source-sha').stdout.strip()
    assert got == LOCK['xlate_source'], f'source fingerprint {got!r}, lock {LOCK["xlate_source"]}'
    return got


@check('extractor')
def _():
    r = run('dotnet', EX)   # no arguments: prints its usage and exits non-zero once the runtime and LibHac load
    assert r.returncode != 0 and 'Usage: extractor' in r.stdout + r.stderr, (r.stdout + r.stderr).strip()[:200]


@check('kit tool lock (nometal_run kit mode)')
def _():
    env = dict(os.environ, DMSH_KIT_LOCK=os.path.join(KIT, 'dmsh_kit.lock'), DMSH_SPIRV_CROSS=SC, XLATE=XL)
    code = "import sys; sys.path.insert(0, 'gfx/tools'); import nometal_run as n; b = n.kit_check_tools(); print('\\n'.join(b)); sys.exit(1 if b else 0)"
    r = run(sys.executable, '-X', 'utf8', '-I', '-c', code, cwd=SRC, env=env)
    assert r.returncode == 0, (r.stdout + r.stderr).strip()[:300]


@check('LF text writes (lf_run.py)')
def _():
    d = tempfile.mkdtemp(); probe = os.path.join(d, 'probe.py'); out = os.path.join(d, 'out.tsv')
    open(probe, 'w', newline='\n').write("import sys\nwith open(sys.argv[1], 'w') as f: f.write('a\\tb\\nc\\n')\n")
    r = run(sys.executable, '-X', 'utf8', '-P', '-E', os.path.join(KIT, 'lf_run.py'), probe, out)
    data = open(out, 'rb').read(); shutil.rmtree(d)
    assert r.returncode == 0 and data == b'a\tb\nc\n', f'{data!r} {r.stderr[:200]}'


@check('shell app')
def _():
    with zipfile.ZipFile(os.path.join(KIT, 'shell', 'Port-shell.zip')) as z:
        names = set(z.namelist()); man = z.read('Payload/Port.app/dm/shell_manifest.txt').decode()
    for need in ('Payload/Port.app/Port', 'Payload/Port.app/Info.plist'): assert need in names, need
    chain = re.search(r'^shaders msl/b-(\w+) (\d+)$', man, re.M); assert chain, 'no shader line in the manifest'
    header = open(os.path.join(SRC, 'gfx', 'nvn', 'dmsh_chain.h'), encoding='utf-8').read()
    assert f'"{chain.group(1)}"' in header, f'shell chain {chain.group(1)} is not the tools\' chain'
    icons = [l.split()[1] for l in man.splitlines() if l.startswith('icon ')]
    assert icons and all('Payload/Port.app/' + i in names for i in icons), 'icon placeholders'
    return f'chain b-{chain.group(1)}, {chain.group(2)} shader files expected, {len(icons)} icons'


@check('expected outputs (expect.json)')
def _():
    e = json.load(open(os.path.join(KIT, 'expect.json'), encoding='utf-8'))
    for g in ('exefs', 'out', 'guest', 'msl', 'tables', 'app', 'ipa', 'data', 'romfs'): assert g in e, f'no group {g}'
    with zipfile.ZipFile(os.path.join(KIT, 'shell', 'Port-shell.zip')) as z: man = z.read('Payload/Port.app/dm/shell_manifest.txt').decode()
    n = int(re.search(r'^shaders \S+ (\d+)$', man, re.M).group(1))
    assert len(e['msl']) == n, f'{len(e["msl"])} shader hashes, the shell expects {n}'
    return f'{sum(len(v) for k, v in e.items() if k != "romfs")} hashes'


@check('pack.py preflight (no game files: must stop at step 2)')
def _():
    empty = tempfile.mkdtemp()
    env = dict(os.environ, SG_KIT_SELFTEST='1')   # pack.py: skips the free-space check only (CI disks are small)
    r = run(sys.executable, '-X', 'utf8', os.path.join(KIT, 'pack.py'), '--inbox', empty, env=env)
    shutil.rmtree(empty); out = re.sub(r'\x1b\[[0-9;]*m', '', r.stdout + r.stderr)
    assert r.returncode == 1 and '[2/8]' in out and '缺东西' in out and '环境还没准备好' not in out, out.strip()[-400:]


def main():
    print(f'SteinsGate Reboot kit self-test ({open(os.path.join(KIT, "VERSION"), encoding="utf-8").read().strip()}, {TAG})')
    for ok, name, msg in results: print(f'  {"ok  " if ok else "FAIL"} {name}' + (f': {msg}' if msg else ''))
    bad = sum(1 for r in results if not r[0])
    print('SELFTEST OK' if not bad else f'SELFTEST FAILED ({bad} of {len(results)})')
    return 1 if bad else 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
