#!/usr/bin/env python3
# NVN's GL conventions for MSL vertex shaders: window origin lower-left (NVN_WINDOW_ORIGIN_MODE_LOWER_LEFT) and clip
# depth near = -w (NVN_DEPTH_MODE_NEAR_IS_MINUS_W). Both are NVN's defaults, kept by a game that never calls
# nvnDeviceSetWindowOriginMode / nvnDeviceSetDepthMode (this port's game: neither has a call site).
# add() gives a spirv-cross vertex entry an epilogue before each "return out;", guarded by two optional function
# constants the replay sets from the device's modes (nvn_mtl.m entry_fn):
#   dm_gl_y (202): negate clip y. Metal puts NDC y = -1 at the bottom of the viewport, which is the last memory row;
#     GL puts it at window y = 0, the first memory row. With y negated, every render target holds the same rows as on
#     the console, so viewports, scissors, gl_FragCoord, copies and readbacks need no change; the winding flips
#     (the replay swaps the front face) and a lower-left window is presented with its last row at the top.
#   dm_gl_z (203): z = (z + w) / 2, from GL's [-w, w] clip range to Metal's [0, w] (the same transform Ryujinx emits
#     for DepthMode without depth clip control); the window depth then comes out as GL's near/far mapping.
# Unspecialised (an upper-left / near = 0 game, or a function fetched without values) both are undefined and the
# epilogue folds away.
# Usage: msl_clip.py <in.metal> <out.metal>   (prints "CLIP <status>"; status ok = epilogue added)
#        msl_clip.py --selftest               (built-in shapes; last line CLIP SELFTEST OK)
import re, sys

Y_INDEX, Z_INDEX = 202, 203   # next to msl_alpha.py's 200 / 201; above dmsh_fetch.py's 0-127
PRELUDE = f'''
// NVN GL conventions (gfx/tools/msl_clip.py): set by the replay from the device's window origin / depth mode
constant bool dm_gl_y [[function_constant({Y_INDEX})]];
constant bool dm_gl_z [[function_constant({Z_INDEX})]];
constant bool dm_gl_y_on = is_function_constant_defined(dm_gl_y) && dm_gl_y;
constant bool dm_gl_z_on = is_function_constant_defined(dm_gl_z) && dm_gl_z;
'''


def _body(src, start):
    i = src.index('{', start); depth = 0
    for k in range(i, len(src)):
        if src[k] == '{': depth += 1
        elif src[k] == '}':
            depth -= 1
            if not depth: return i, k
    raise ValueError('unbalanced braces in the vertex entry')


def add(msl):
    """-> (new msl, status). status: ok | not_vertex | no_position | no_return"""
    m = re.search(r'\bvertex\s+(\w+)\s+(\w+)\s*\(', msl)
    if not m: return msl, 'not_vertex'
    st = re.search(r'struct\s+%s\s*\{(.*?)\};' % re.escape(m.group(1)), msl, re.S)
    pos = st and re.search(r'float4\s+(\w+)\s*\[\[\s*position\b', st.group(1))
    if not pos: return msl, 'no_position'
    p = 'out.' + pos.group(1)
    o, c = _body(msl, m.end())
    epi = ('{ if (dm_gl_y_on) %s.y = -%s.y; if (dm_gl_z_on) %s.z = (%s.z + %s.w) * 0.5; return out; }' % (p, p, p, p, p))
    nbody, n = re.subn(r'\breturn\s+out\s*;', lambda _: epi, msl[o:c])
    if not n: return msl, 'no_return'
    msl = msl[:o] + nbody + msl[c:]
    u = re.search(r'using namespace metal;\n', msl)
    at = u.end() if u else 0
    return msl[:at] + PRELUDE + msl[at:], 'ok'


SAMPLE = '''#include <metal_stdlib>
using namespace metal;

struct main0_out
{
    float4 out_attr0 [[user(locn0)]];
    float4 gl_Position [[position, invariant]];
};

struct main0_in
{
    float4 in_attr0 [[attribute(0)]];
};

vertex main0_out main0(main0_in in [[stage_in]])
{
    main0_out out = {};
    out.gl_Position = in.in_attr0;
    if (in.in_attr0.w == 0.0)
    {
        return out;
    }
    out.out_attr0 = in.in_attr0;
    return out;
}
'''


def selftest():
    fails = []
    def want(name, cond):
        print(('PASS ' if cond else 'FAIL ') + name)
        if not cond: fails.append(name)
    new, s = add(SAMPLE)
    want('epilogue added to a vertex entry', s == 'ok')
    want('both returns get it', new.count('if (dm_gl_y_on) out.gl_Position.y = -out.gl_Position.y;') == 2)
    want('z remap is (z + w) / 2', new.count('out.gl_Position.z = (out.gl_Position.z + out.gl_Position.w) * 0.5;') == 2)
    want('prelude after using namespace metal', new.index('using namespace metal;') < new.index('function_constant(202)') < new.index('struct main0_out'))
    want('a fragment entry is left alone', add(SAMPLE.replace('vertex main0_out', 'fragment main0_out'))[1] == 'not_vertex')
    want('a vertex entry without [[position]] is refused', add(SAMPLE.replace('[[position, invariant]]', ''))[1] == 'no_position')
    # Negative control: an epilogue on the wrong member must fail the check above
    bad = new.replace('out.gl_Position.y = -out.gl_Position.y', 'out.out_attr0.y = -out.out_attr0.y')
    want('NEG a negate on the wrong member is detected', bad.count('if (dm_gl_y_on) out.gl_Position.y = -out.gl_Position.y;') != 2)
    print('CLIP SELFTEST OK' if not fails else 'CLIP SELFTEST FAILED')
    return not fails


if __name__ == '__main__':
    if sys.argv[1:] == ['--selftest']: sys.exit(0 if selftest() else 1)
    src = open(sys.argv[1]).read(); new, status = add(src)
    open(sys.argv[2], 'w').write(new); print('CLIP ' + status)
