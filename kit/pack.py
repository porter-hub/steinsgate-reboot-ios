#!/usr/bin/env python3
# The installer kit's packer (docs/INSTALL.md): the user's own base + update NSP and prod.keys -> a finished iPhone
# package (output/SteinsGate.ipa) and the game data to copy in (output/game-data/*.tar: plain files, which every file-sharing
# tool can copy; the app unpacks them from its Documents folder on the next launch, ios/Sources/main.m). Runs on Windows, macOS and Linux
# (Python from uv, .NET 10 runtime; kit/bootstrap.* checks both first). Nothing leaves the machine.
#
# Steps, each checked item by item against kit/expect.json (SHA-256 of every output, recorded when the kit was built
# from the same files on the development Mac and run on the iPhone there): find the two packages by hash, extract
# (keys filtered into a private temporary directory, deleted right after; values never printed), rewrite the three
# modules (tools/patch_nso.py), write the guest library (tools/guest_dylib.py), translate the shaders to MSL source
# (gfx/tools/nometal_run.py kit mode around dmsh_build.py), fill the content-free shell, zip it (stored, so the same
# files give the same package on every system), pack the RomFS into tars of about 1 GiB (fixed metadata, sorted: the
# same bytes everywhere; each file is deleted once it is in a tar, so the disk never holds the data twice). A step whose outputs already match is skipped, so a rerun is quick.
#
# Usage: pack.py [--inbox <dir>] [--keys <prod.keys>] [--record]
#   --inbox: where the NSPs and prod.keys are (default ../game-files; any file name, found by content)
#   --keys: the key file, when it is not in the inbox
#   --record: kit build only (scripts/kit_build.sh): write expect.json from this run instead of checking against it
import hashlib, json, os, platform, shutil, stat, subprocess, sys, tempfile, time, zipfile

KIT = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(KIT); SRC = os.path.join(KIT, 'src')
WORK = os.path.join(ROOT, 'work'); OUTD = os.path.join(ROOT, 'output'); INBOX = os.path.join(ROOT, 'game-files')
EXPECT = os.path.join(KIT, 'expect.json'); RECORD = '--record' in sys.argv
if '--inbox' in sys.argv: INBOX = os.path.abspath(sys.argv[sys.argv.index('--inbox') + 1])
KEYS = os.path.abspath(sys.argv[sys.argv.index('--keys') + 1]) if '--keys' in sys.argv else None
GAME = json.load(open(os.path.join(SRC, 'game.json'), encoding='utf-8')); MODS = GAME['modules']
WIN = sys.platform == 'win32'
G, R, Y, B, X = ('', '', '', '', '') if WIN and not os.environ.get('WT_SESSION') else ('\033[32m', '\033[31m', '\033[33m', '\033[1m', '\033[0m')
rec = {} if RECORD else json.load(open(EXPECT, encoding='utf-8'))


def say(s): print(s, flush=True)
def step(n, s): say(f'\n{B}[{n}/8] {s}{X}')
def ok(s): say(f'  {G}✓{X} {s}')
def die(s, hint=''):
    say(f'\n{R}✗ {s}{X}' + (f'\n  {hint}' if hint else '')); say(f'\n{R}没有完成。{X}问题解决后再运行一次Start 脚本：已经做好的步骤会自动跳过。'); sys.exit(1)


def sha(p, chunk=1 << 22, progress=None):
    h = hashlib.sha256(); done = 0; total = os.path.getsize(p); t = time.time()
    with open(p, 'rb') as f:
        while b := f.read(chunk):
            h.update(b); done += len(b)
            if progress and time.time() - t > 1: t = time.time(); print(f'\r  {progress} {done * 100 // total}%', end='', flush=True)
    if progress: print('\r' + ' ' * (len(progress) + 8) + '\r', end='')
    return h.hexdigest()


def check(group, files, base, partial=False):
    """Every file of a group against expect.json (--record: remember them). files: names relative to base.
    partial: some recorded files were left out on purpose (the user's own icon); the rest must still match."""
    got = {f: sha(os.path.join(base, f)) for f in sorted(files)}
    if RECORD: rec[group] = got; return
    want = rec[group] if not partial else {f: h for f, h in rec[group].items() if f in got}
    missing = sorted(set(want) - set(got)); extra = sorted(set(got) - set(want)); bad = sorted(f for f in got if f in want and got[f] != want[f])
    if missing or extra or bad:
        die(f'{group}：和安装工具预期的结果不一致（缺 {len(missing)}，多 {len(extra)}，不同 {len(bad)}）',
            '例：' + ', '.join((missing + extra + bad)[:3]) + '\n  这通常说明输入文件或工具版本不对。请把这一屏截图发给提供安装包的人。')


def matches(group, base):
    """True when the outputs of a group already match (a rerun skips that step)."""
    if RECORD or group not in rec: return False
    return all(os.path.isfile(os.path.join(base, f)) and sha(os.path.join(base, f)) == h for f, h in rec[group].items())


PYRUN = [sys.executable, '-X', 'utf8', '-P', '-E', os.path.join(KIT, 'lf_run.py')]   # UTF-8, no user paths, LF text files (lf_run.py)


def py(args, log, env=None):
    """A kit tool under this Python, output to a log file."""
    with open(log, 'w', encoding='utf-8') as f:
        return subprocess.run([*PYRUN, *args], cwd=SRC, stdout=f, stderr=subprocess.STDOUT, env=env).returncode


def tail(log, n=6):
    try: return '\n  '.join(open(log, encoding='utf-8', errors='replace').read().splitlines()[-n:])
    except OSError: return ''


# ---------- 1. prerequisites, all listed before anything runs ----------
def platform_tag(): return sys.platform + '-' + platform.machine().lower()


def prerequisites():
    step(1, '检查环境')
    probs = []
    if sys.version_info < (3, 12): probs.append(f'Python 版本 {platform.python_version()} 太旧（需要 3.12 以上；Start 脚本会用 uv 自动准备）')
    try: import capstone, lz4.block, PIL  # noqa: F401
    except ImportError as e: probs.append(f'缺 Python 组件 {e.name}（Start 脚本会用 uv 自动装；直接运行 pack.py 时请改用Start 脚本）')
    try:
        rts = subprocess.run(['dotnet', '--list-runtimes'], capture_output=True, text=True).stdout
        if 'Microsoft.NETCore.App 10.' not in rts: probs.append('没有 .NET 10 运行时（Start 脚本会帮你装，或从 https://dotnet.microsoft.com/download/dotnet/10.0 下载 .NET Runtime 10）')
    except OSError: probs.append('找不到 dotnet（.NET 10 运行时；Start 脚本会帮你装）')
    sc = os.path.join(KIT, 'spirv-cross', platform_tag(), 'spirv-cross' + ('.exe' if WIN else ''))
    if not os.path.isfile(sc): probs.append(f'这个系统（{platform_tag()}）没有附带的着色器工具；支持 Windows x64、macOS Apple 芯片、Linux x64 / arm64')
    for need in ('shell/Port-shell.zip', 'dotnet/extractor/extractor.dll', 'dotnet/dmsh_xlate/dmsh_xlate.dll', 'dmsh_kit.lock') + (() if RECORD else ('expect.json',)):
        if not os.path.exists(os.path.join(KIT, need)): probs.append(f'安装工具不完整：缺 kit/{need}（请重新解压整个安装包）')
    os.makedirs(WORK, exist_ok=True); free = shutil.disk_usage(WORK).free
    have = os.path.isdir(os.path.join(OUTD, 'game-data'))
    if free < (2 if have else 9) * 2 ** 30: probs.append(f'磁盘空间不够：这个文件夹所在的盘只剩 {free / 2 ** 30:.1f} GB，至少需要 {2 if have else 9} GB')
    if probs: die('环境还没准备好：\n  • ' + '\n  • '.join(probs))
    ok(f'Python {platform.python_version()}、.NET 10、着色器工具（{platform_tag()}）、磁盘剩余 {free / 2 ** 30:.0f} GB')
    return sc


# ---------- 2. inputs, found by content ----------
def inputs():
    step(2, '查找游戏文件和 prod.keys')
    if not os.path.isdir(INBOX): os.makedirs(INBOX, exist_ok=True)
    want = {GAME['inputs']['base_nsp_sha256']: 'base', GAME['inputs']['update_nsp_sha256']: 'update'}
    cache_p = os.path.join(WORK, 'inputs.verified.json')
    try: cache = json.load(open(cache_p, encoding='utf-8'))
    except (OSError, ValueError): cache = {}
    found, keys, other = {}, [], []
    for d, _, fs in os.walk(INBOX):
        for f in fs:
            p = os.path.join(d, f); low = f.lower()
            if low.endswith('.keys'): keys.append(p); continue
            if not low.endswith('.nsp'): continue
            st = os.stat(p); ck = f'{p}|{st.st_size}|{st.st_mtime_ns}'
            h = cache.get(ck) or sha(p, progress=f'校验 {f}'); cache[ck] = h
            if h in want: found[want[h]] = p
            else: other.append(f)
    json.dump(cache, open(cache_p, 'w', encoding='utf-8'))
    for f in other: say(f'  {Y}!{X} 跳过 {f}：不是本作需要的那两个文件（只认中文版 v1.0.3 的本体和更新）')
    # prod.keys by content (a title.keys may sit next to it): the one with the NCA header key; values are not printed
    def is_prod(p):
        try: return any(l.split('=')[0].strip().lower() == 'header_key' for l in open(p, encoding='utf-8', errors='replace'))
        except OSError: return False
    keys = [KEYS] if KEYS else sorted((k for k in keys if is_prod(k)), key=lambda k: os.path.basename(k).lower() != 'prod.keys')
    miss = [x for x in ('base', 'update') if x not in found]
    if miss or not keys:
        die('game-files 文件夹里缺东西：' + '、'.join(({'base': '游戏本体 NSP', 'update': '1.0.3 更新 NSP'}[m] for m in miss)) + ('、prod.keys' if not keys else ''),
            f'把这三个文件放进：{INBOX}\n  只支持 NSP（不支持 XCI）；文件名随意，按内容识别。')
    ok(f'本体：{os.path.basename(found["base"])}'); ok(f'更新：{os.path.basename(found["update"])}'); ok(f'密钥：{os.path.basename(keys[0])}（只读取解密需要的条目，不会显示内容）')
    return found['base'], found['update'], keys[0]


# ---------- 3. extract ----------
def extract(base, upd, keys):
    step(3, '解包游戏（约 6 GB，几分钟）')
    exefs = os.path.join(WORK, 'game', 'ExeFS'); romfs = os.path.join(WORK, 'game', 'RomFS')
    if matches('exefs', exefs) and (matches('data', DATA) or romfs_ok(romfs, quiet=True)): ok('已经解包过，跳过'); return exefs, romfs
    new = os.path.join(WORK, 'game.new'); shutil.rmtree(new, ignore_errors=True)
    kd = tempfile.mkdtemp(prefix='sgkeys'); os.chmod(kd, 0o700)   # private: only the filtered keys, deleted right after
    log = os.path.join(WORK, 'extract.log')
    try:
        kp = os.path.join(kd, 'prod.keys')
        if py(['scripts/analysis/filter_keys.py', keys, kp], os.path.join(WORK, 'keys.log')): die('读取 prod.keys 失败', tail(os.path.join(WORK, 'keys.log'), 2))
        os.chmod(kp, 0o600)
        for l in open(os.path.join(WORK, 'keys.log'), encoding='utf-8'): say('  密钥：' + l.strip())   # names and counts only
        with open(log, 'w', encoding='utf-8') as f:
            rc = subprocess.run(['dotnet', os.path.join(KIT, 'dotnet', 'extractor', 'extractor.dll'), kp, base, upd, new, '--program-id', GAME['program_id']],
                                stdout=f, stderr=subprocess.STDOUT).returncode
    finally: shutil.rmtree(kd, ignore_errors=True)
    if rc or not open(log, encoding='utf-8', errors='replace').read().rstrip().endswith('EXTRACT OK'):
        die('解包失败（多半是 prod.keys 太旧，或文件不完整）', tail(log))
    m = json.load(open(os.path.join(new, 'metadata.json'), encoding='utf-8'))
    if int(m['program_id'], 16) != int(GAME['program_id'], 16): die(f'不是本作：program id {m["program_id"]}')
    shutil.rmtree(os.path.join(WORK, 'game'), ignore_errors=True); os.replace(new, os.path.join(WORK, 'game'))
    check('exefs', [f for f in os.listdir(exefs) if f in MODS], exefs)
    if RECORD: rec['romfs'] = {'files': m['romfs_files'], 'bytes': m['romfs_bytes'], 'marker': GAME_MARKER}
    romfs_ok(romfs)
    ok(f'版本 {m["display_version"]}，游戏数据 {m["romfs_files"]} 个文件 / {m["romfs_bytes"] / 2 ** 30:.2f} GB')
    return exefs, romfs


GAME_MARKER = 'script_info.psb.m'   # host/game_config.h GAME_ROMFS_MARKER: the app finds the data folder by it
DATA = os.path.join(OUTD, 'game-data'); PART = 1 << 30


def romfs_ok(romfs, quiet=False):
    if not os.path.isfile(os.path.join(romfs, GAME_MARKER)):
        if quiet: return False
        die('解出的游戏数据不完整（没有标记文件）')
    if RECORD: return True
    n = b = 0
    for d, _, fs in os.walk(romfs):
        for f in fs: n += 1; b += os.path.getsize(os.path.join(d, f))
    want = rec['romfs']; good = (n, b) == (want['files'], want['bytes'])
    if not good and not quiet: die(f'解出的游戏数据不完整：{n} 个文件 / {b} 字节，应为 {want["files"]} / {want["bytes"]}')
    return good


# ---------- 4. rewrite the modules ----------
def rewrite(exefs):
    step(4, '改写游戏程序（离线，每个模块几秒）')
    out = os.path.join(WORK, 'out'); keep = [f'{m}.{x}' for m in MODS for x in ('patched.bin', 'fixups.tsv', 'gates.tsv')]
    if matches('out', out): ok('已经改写过，跳过'); return out
    os.makedirs(out, exist_ok=True)
    for m in MODS:
        log = os.path.join(WORK, f'rewrite-{m}.log')
        if py(['tools/patch_nso.py', os.path.join(exefs, m), out, m, '--config', 'game.json'], log): die(f'改写模块 {m} 失败', tail(log))
        ok(f'模块 {m}')
    check('out', keep, out)
    return out


# ---------- 5. shaders ----------
def shaders(sc):
    step(5, '翻译着色器（487 个，约半分钟）')
    sh = os.path.join(WORK, 'shaders'); img = os.path.join(WORK, 'img'); nvn = os.path.join(SRC, 'gfx', 'nvn')
    with zipfile.ZipFile(os.path.join(KIT, 'shell', 'Port-shell.zip')) as z: man = z.read('Payload/Port.app/dm/shell_manifest.txt').decode()
    chain = [l.split()[1] for l in man.splitlines() if l.startswith('shaders ')][0]   # msl/b-<V>: the version the shell was built for
    ios = os.path.join(sh, 'ios', os.path.basename(chain))
    if matches('msl', ios) and matches('tables', os.path.join(sh, 'tables')): ok('已经翻译过，跳过'); return ios, os.path.join(sh, 'tables')
    os.makedirs(img, exist_ok=True)
    if py(['tools/nsolib.py', 'extract', os.path.join(WORK, 'game', 'ExeFS', 'main'), os.path.join(img, 'main.bin')], os.path.join(WORK, 'img.log')): die('读取主程序失败', tail(os.path.join(WORK, 'img.log')))
    shutil.rmtree(sh, ignore_errors=True); os.makedirs(sh)
    log = os.path.join(WORK, 'shaders.log')
    if py(['gfx/tools/bnsh_nvn.py', os.path.join(img, 'main.bin'), sh], log): die('提取着色器失败', tail(log))
    for f in ('shader_map.tsv', 'shader_map.tsv.conflicts.tsv'): shutil.copyfile(os.path.join(sh, f), os.path.join(nvn, f))   # part of the chain version
    v = subprocess.run([*PYRUN, 'gfx/tools/dmsh_chain.py'], cwd=SRC, capture_output=True, text=True).stdout.strip()
    if 'b-' + v != os.path.basename(chain): die(f'着色器链版本 {v} 和安装包里的空壳（{os.path.basename(chain)}）不一致', '安装工具和游戏文件不配套。')
    env = dict(os.environ, J=str(os.cpu_count() or 4), IOSOUT=os.path.join(sh, 'ios'), DMSH_CAS=os.path.join(sh, 'cas'), DMSH_KIT_LOCK=os.path.join(KIT, 'dmsh_kit.lock'),
               DMSH_SPIRV_CROSS=sc, XLATE=os.path.join(KIT, 'dotnet', 'dmsh_xlate', 'dmsh_xlate.dll'), DM_ROOT=WORK)
    if py(['gfx/tools/nometal_run.py', 'gfx/tools/dmsh_build.py', os.path.join(sh, 'packs'), os.path.join(sh, 'cache')], log, env): die('翻译着色器失败', tail(log, 10))
    tables = os.path.join(sh, 'tables'); os.makedirs(tables, exist_ok=True)
    if py(['gfx/tools/c1_guard.py', os.path.join(sh, 'cache', os.path.basename(chain)), os.path.join(tables, 'c1_full.tsv')], os.path.join(WORK, 'c1.log')): die('着色器检查表失败', tail(os.path.join(WORK, 'c1.log')))
    body = open(os.path.join(tables, 'c1_full.tsv'), encoding='utf-8').read().split('\n', 1)[1]   # line 1 = chain + time stamp
    open(os.path.join(tables, 'c1_full.body'), 'w', encoding='utf-8', newline='\n').write(body)
    shutil.copyfile(os.path.join(sh, 'shader_map.tsv'), os.path.join(tables, 'shader_map.tsv'))
    check('msl', os.listdir(ios), ios); check('tables', ['shader_map.tsv', 'c1_full.body'], tables)
    ok('487 个着色器（源码；iPhone 第一次启动时编译一次）')
    return ios, tables


# ---------- the app icon: the game's own, from the user's files ----------
# The kit cannot carry it (it is game art), so the shell has placeholders and this puts the game's icon in their place:
# an image named icon.png / icon.jpg in the inbox if there is one (the user's own choice), else the icon of the user's
# extracted Control data (Simplified Chinese first). Pillow decodes and scales; the PNGs are written here with stored
# deflate blocks, so their bytes do not depend on the zlib a system has.
def png_stored(path, img):
    import struct, zlib
    w, h = img.size; raw = b''.join(b'\0' + img.crop((0, y, w, y + 1)).tobytes() for y in range(h))
    blocks = b''.join(bytes([1 if o + 65535 >= len(raw) else 0]) + struct.pack('<HH', n, n ^ 0xffff) + raw[o:o + n]
                      for o in range(0, len(raw), 65535) for n in [min(65535, len(raw) - o)])
    z = b'\x78\x01' + blocks + struct.pack('>I', zlib.adler32(raw))
    chunk = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    with open(path, 'wb') as f: f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) + chunk(b'IDAT', z) + chunk(b'IEND', b''))


def icons(app):
    from PIL import Image
    own = [os.path.join(INBOX, f) for f in sorted(os.listdir(INBOX)) if f.lower() in ('icon.png', 'icon.jpg', 'icon.jpeg')] if os.path.isdir(INBOX) else []
    ctl = [os.path.join(WORK, 'game', 'Control', f'icon_{l}.dat') for l in ('SimplifiedChinese', 'TraditionalChinese', 'Japanese', 'AmericanEnglish')]
    src = next((p for p in own + ctl if os.path.isfile(p)), None)
    if not src: die('找不到游戏图标（解包结果里没有 Control 图标）')
    im = Image.open(src).convert('RGB'); s0 = min(im.size)
    im = im.crop(((im.width - s0) // 2, (im.height - s0) // 2, (im.width - s0) // 2 + s0, (im.height - s0) // 2 + s0))
    n = 0
    for line in open(os.path.join(app, 'dm', 'shell_manifest.txt'), encoding='utf-8'):
        f = line.split()
        if len(f) == 3 and f[0] == 'icon': png_stored(os.path.join(app, f[1]), im.resize((int(f[2]), int(f[2])), Image.LANCZOS)); n += 1
    ok(f'图标：{"你自己的 " + os.path.basename(src) if src in own else "游戏自带的图标"}（{n} 个尺寸）')
    return src in own


# ---------- 6. the package ----------
def assemble(out, ios, tables):
    step(6, '组装 iPhone 安装包')
    stage = os.path.join(WORK, 'stage'); shutil.rmtree(stage, ignore_errors=True); os.makedirs(stage)
    with zipfile.ZipFile(os.path.join(KIT, 'shell', 'Port-shell.zip')) as z: z.extractall(stage)
    app = os.path.join(stage, 'Payload', 'Port.app')
    if not os.path.isfile(os.path.join(app, 'dm', 'shell_manifest.txt')): die('空壳安装包损坏（kit/shell/Port-shell.zip）')
    sys.path.insert(0, os.path.join(SRC, 'tools')); import guest_dylib
    guest_dylib.write(out, os.path.join(app, 'Frameworks'), MODS)
    check('guest', ['Guest', 'Info.plist'], os.path.join(app, 'Frameworks', 'Guest.framework'))
    dm = os.path.join(app, 'dm')
    for m in MODS:
        for x in ('fixups.tsv', 'gates.tsv'): shutil.copyfile(os.path.join(out, f'{m}.{x}'), os.path.join(dm, f'{m}.{x}'))
    shutil.copyfile(os.path.join(tables, 'shader_map.tsv'), os.path.join(dm, 'shader_map.tsv'))
    # c1_guard.py stamps its header with the time; the app only checks "# chain b-<V>" (nvn_mtl.m), so the package gets
    # the header without it: the same package on every run
    head = open(os.path.join(tables, 'c1_full.tsv'), encoding='utf-8').readline().split()[:3]
    with open(os.path.join(dm, 'c1_full.tsv'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(' '.join(head) + '\n' + open(os.path.join(tables, 'c1_full.body'), encoding='utf-8').read())
    shutil.copytree(ios, os.path.join(app, 'msl', os.path.basename(ios)))
    guest = sha(os.path.join(app, 'Frameworks', 'Guest.framework', 'Guest'))
    kitv = open(os.path.join(KIT, 'VERSION'), encoding='utf-8').read().strip()
    open(os.path.join(dm, 'pack_info.txt'), 'w', encoding='utf-8', newline='\n').write(f'kit {kitv} guest {guest[:16]}\n')   # line 1: the shader prewarm stamp
    for line in open(os.path.join(dm, 'shell_manifest.txt'), encoding='utf-8'):   # what main.m pack_check will ask for
        f = line.split()
        if len(f) >= 2 and f[0] == 'framework' and not (os.path.isfile(os.path.join(app, f[1])) and os.path.getsize(os.path.join(app, f[1]))): die(f'安装包缺 {f[1]}')
        if len(f) >= 2 and f[0] == 'table' and not os.path.isfile(os.path.join(app, f[1])): die(f'安装包缺 {f[1]}')   # a table may be empty (a module without gates)
        if len(f) == 3 and f[0] == 'shaders' and len(os.listdir(os.path.join(app, f[1]))) != int(f[2]): die(f'安装包里的着色器数量不对：{f[1]}')
    own_icon = icons(app)
    # every file of the package against the recording; the icons too, unless the user chose their own picture
    names = [os.path.relpath(os.path.join(d, f), stage).replace(os.sep, '/') for d, _, fs in os.walk(stage) for f in fs]
    icon_files = {'Payload/Port.app/' + l.split()[1] for l in open(os.path.join(dm, 'shell_manifest.txt'), encoding='utf-8') if l.startswith('icon ')}
    check('app', [n for n in names if not (own_icon and n in icon_files)], stage, partial=own_icon)
    os.makedirs(OUTD, exist_ok=True); ipa = os.path.join(OUTD, 'SteinsGate.ipa'); tmp = ipa + '.tmp'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_STORED) as z:   # stored + fixed times + sorted: the same bytes on every system
        for d, ds, fs in sorted(os.walk(stage)):
            ds.sort()
            for f in sorted(fs):
                p = os.path.join(d, f); rel = os.path.relpath(p, stage).replace(os.sep, '/')
                zi = zipfile.ZipInfo(rel, (1980, 1, 1, 0, 0, 0)); zi.create_system = 3
                zi.external_attr = ((0o100755 if rel in ('Payload/Port.app/Port',) else 0o100644) << 16)
                with open(p, 'rb') as src: z.writestr(zi, src.read())
    os.replace(tmp, ipa)
    if not own_icon: check('ipa', ['SteinsGate.ipa'], OUTD)   # with the user's own icon only its files differ (checked above)
    ok(f'output/SteinsGate.ipa（{os.path.getsize(ipa) / 2 ** 20:.0f} MB）')
    return ipa


# ---------- 7. the game data ----------
def data(romfs):
    step(7, '打包游戏数据（约 5.8 GB，几分钟）')
    if matches('data', DATA): ok('已经打包过，跳过'); shutil.rmtree(romfs, ignore_errors=True); return
    import tarfile
    files = sorted(os.path.relpath(os.path.join(d, f), romfs).replace(os.sep, '/') for d, _, fs in os.walk(romfs) for f in fs)
    parts, cur, acc = [], [], 0
    for rel in files:   # whole files, about 1 GiB a part (one larger file gets a part of its own)
        sz = os.path.getsize(os.path.join(romfs, rel))
        if cur and acc + sz > PART: parts.append(cur); cur, acc = [], 0
        cur.append(rel); acc += sz
    if cur: parts.append(cur)
    shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
    names = []
    for i, part in enumerate(parts, 1):
        name = f'sg-data-{i}-of-{len(parts)}.tar'; names.append(name); tmp = os.path.join(DATA, name + '.tmp')
        with tarfile.open(tmp, 'w', format=tarfile.USTAR_FORMAT) as t:
            for rel in part:   # the app's untar (main.m) takes regular files; directories are made on the way
                p = os.path.join(romfs, rel); ti = tarfile.TarInfo(rel); ti.size = os.path.getsize(p); ti.mode = 0o644; ti.mtime = 0
                with open(p, 'rb') as f: t.addfile(ti, f)
                os.remove(p)
        os.replace(tmp, os.path.join(DATA, name)); print(f'\r  {i} / {len(parts)}', end='', flush=True)
    print('\r', end='')
    shutil.rmtree(romfs, ignore_errors=True)
    check('data', names, DATA)
    ok(f'output/game-data：{len(names)} 个文件，共 {sum(os.path.getsize(os.path.join(DATA, n)) for n in names) / 2 ** 30:.2f} GB')


# ---------- 8. what next ----------
def next_steps(ipa):
    step(8, '完成！接下来把它装到 iPhone 上')
    files = '「Apple 设备」App（Microsoft Store 里免费下载；装了 iTunes 也可以）' if WIN else ('访达' if sys.platform == 'darwin' else '第三方工具（Linux 没有官方文件共享：例如 libimobiledevice 的 ifuse --documents，未经测试）')
    say(f'''
  安装包：{ipa}
  游戏数据：{DATA}（里面的 .tar 文件）

  1. iPhone：设置 → 隐私与安全性 → 开发者模式 → 打开（会重启）。需要 iOS 26 以上、A14 或更新的芯片。
  2. 电脑上打开 Sideloadly（https://sideloadly.io，Windows / Mac）{'；Linux 没有 Sideloadly，可用 AltServer-Linux 等工具（未经测试）' if sys.platform.startswith('linux') else ''}，
     用数据线连上 iPhone，把 SteinsGate.ipa 拖进去，填你的 Apple ID，点 Start。
  3. iPhone：设置 → 通用 → VPN 与设备管理 → 信任你的 Apple ID。
  4. 用{files}打开这台 iPhone → 文件 → {GAME.get("ios_display_name", "本 App")}，
     把 game-data 文件夹里的所有 .tar 文件拖进去（共约 5.8 GB）。
  5. 打开 App。第一次启动会先导入游戏数据、准备着色器（共约一分钟），之后直接进游戏。

  免费 Apple ID 的签名 7 天后过期：到时用 Sideloadly 重新装一次 SteinsGate.ipa 即可，存档和游戏数据都会保留。
  详细步骤见 README.zh.md（English: README.md）。''')


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    say(f'{B}STEINS;GATE iPhone 安装包制作工具{X}（{"记录模式" if RECORD else "kit " + open(os.path.join(KIT, "VERSION"), encoding="utf-8").read().strip()}）')
    sc = prerequisites()
    base, upd, keys = inputs()
    exefs, romfs = extract(base, upd, keys)
    out = rewrite(exefs)
    ios, tables = shaders(sc)
    ipa = assemble(out, ios, tables)
    data(romfs)
    if RECORD:
        json.dump(rec, open(EXPECT, 'w', encoding='utf-8'), indent=1, sort_keys=True); say(f'\n记录了 {sum(len(v) for v in rec.values() if isinstance(v, dict))} 项 -> {EXPECT}')
    next_steps(ipa)
    say(f'\n{G}PACK OK{X}')
