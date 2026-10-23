#!/usr/bin/env python3
# steins-gate-re-port: NVN alpha test (nvnColorStateSetAlphaTest + nvnCommandBufferSetAlphaRef) for MSL fragment shaders.
# Metal has no fixed-function alpha test. add() gives a spirv-cross fragment entry a test on color(0).a, run before each
# "return out;", guarded by two function constants. The replay specialises the function only while the bound color
# state's alpha func is not ALWAYS (nvn_mtl.m at_fn); unspecialised, dm_at_func is undefined, dm_at_on is false and the
# test folds away. Metal only builds pipelines from a function with constants if it was fetched with constantValues, so
# the replay fetches every entry that has constants through an empty specialisation (nvn_mtl.m entry_fn).
# This game: both nvnColorStateSetAlphaTest sites (main+0x384bbc, +0x384e54) pass GREATER, and both
# nvnCommandBufferSetAlphaRef sites (main+0x388830, +0x3893a8) pass 0.0: "drop fragments whose alpha is not > 0".
# Usage: msl_alpha.py <in.metal> <out.metal>   (prints "ALPHA TEST <status>"; status ok = epilogue added)
#        msl_alpha.py --selftest               (runs the rewrite on built-in shapes; last line ALPHA SELFTEST OK)
import re, sys

AT_FUNC_INDEX, AT_REF_INDEX = 200, 201   # above every index dmsh_fetch.py uses (FX 0-31, FB 32-39, OM 64-127)
PRELUDE = f'''
// NVN alpha test (gfx/tools/msl_alpha.py): NVNalphaFunc values; specialised by the replay only when not ALWAYS
constant int dm_at_func [[function_constant({AT_FUNC_INDEX})]];
constant float dm_at_ref [[function_constant({AT_REF_INDEX})]];
constant bool dm_at_on = is_function_constant_defined(dm_at_func) && is_function_constant_defined(dm_at_ref);   // both optional
static inline bool dm_at_pass(float a)
{{
    switch (dm_at_func)
    {{
        case 1: return false;
        case 2: return a < dm_at_ref;
        case 3: return a == dm_at_ref;
        case 4: return a <= dm_at_ref;
        case 5: return a > dm_at_ref;
        case 6: return a != dm_at_ref;
        case 7: return a >= dm_at_ref;
        default: return true;
    }}
}}
'''


def _body(src, start):
    """(open, close) indices of the brace block that begins at or after start."""
    i = src.index('{', start); depth = 0
    for k in range(i, len(src)):
        if src[k] == '{': depth += 1
        elif src[k] == '}':
            depth -= 1
            if not depth: return i, k
    raise ValueError('unbalanced braces in the fragment entry')


def add(msl):
    """-> (new msl, status). status: ok | not_fragment | no_color0 | int_color0 | early_z | no_return."""
    m = re.search(r'(\[\[[^\]]*\]\]\s*)?fragment\s+(\w+)\s+(\w+)\s*\(', msl)
    if not m: return msl, 'not_fragment'
    if m.group(1) and 'early_fragment_tests' in m.group(1): return msl, 'early_z'   # depth is written before a discard could act
    outty = m.group(2)
    st = re.search(r'struct\s+%s\s*\{(.*?)\};' % re.escape(outty), msl, re.S)
    c0 = st and re.search(r'(\w+)\s+(\w+)\s*\[\[\s*color\(0\)', st.group(1))
    if not c0: return msl, 'no_color0'
    if not re.fullmatch(r'(float|half)4', c0.group(1)): return msl, 'int_color0'
    o, c = _body(msl, m.end())
    body = msl[o:c]
    test = '{ if (dm_at_on && !dm_at_pass(float(out.%s.w))) discard_fragment(); return out; }' % c0.group(2)
    nbody, n = re.subn(r'\breturn\s+out\s*;', lambda _: test, body)
    if not n: return msl, 'no_return'
    msl = msl[:o] + nbody + msl[c:]
    u = re.search(r'using namespace metal;\n', msl)
    at = u.end() if u else 0
    return msl[:at] + PRELUDE + msl[at:], 'ok'


SAMPLE = '''#include <metal_stdlib>
#include <simd/simd.h>

using namespace metal;

struct main0_out
{
    float4 out_var_SV_Target0 [[color(0)]];
    float4 out_var_SV_Target1 [[color(1)]];
};

struct main0_in
{
    float4 in_var_COLOR [[user(locn0)]];
};

fragment main0_out main0(main0_in in [[stage_in]])
{
    main0_out out = {};
    if (in.in_var_COLOR.x < 0.0)
    {
        out.out_var_SV_Target0 = float4(0.0);
        return out;
    }
    out.out_var_SV_Target0 = in.in_var_COLOR;
    out.out_var_SV_Target1 = in.in_var_COLOR;
    return out;
}
'''


def selftest():
    fails = []
    def want(name, cond):
        print(('PASS ' if cond else 'FAIL ') + name)
        if not cond: fails.append(name)
    new, s = add(SAMPLE)
    want('epilogue added to a two-output fragment', s == 'ok')
    want('both returns are tested', new.count('dm_at_pass(float(out.out_var_SV_Target0.w))') == 2)
    want('prelude follows using namespace metal', new.index('using namespace metal;') < new.index('dm_at_func [[function_constant(200)]]') < new.index('struct main0_out'))
    want('idempotent shape: no test inside a struct', 'discard_fragment' not in new[:new.index('fragment main0_out')])
    want('a vertex shader is left alone', add(SAMPLE.replace('fragment main0_out', 'vertex main0_out'))[1] == 'not_fragment')
    want('integer color(0) is refused', add(SAMPLE.replace('float4 out_var_SV_Target0', 'uint4 out_var_SV_Target0'))[1] == 'int_color0')
    want('early fragment tests are refused', add(SAMPLE.replace('fragment main0_out', '[[early_fragment_tests]] fragment main0_out'))[1] == 'early_z')
    want('no color(0) is refused', add(SAMPLE.replace('[[color(0)]]', '[[color(2)]]'))[1] == 'no_color0')
    # Negative control: a rewrite that tests the wrong output must be caught by the check above
    bad = new.replace('out.out_var_SV_Target0.w', 'out.out_var_SV_Target1.w')
    want('NEG a test on the wrong output is detected', bad.count('dm_at_pass(float(out.out_var_SV_Target0.w))') != 2)
    print('ALPHA SELFTEST OK' if not fails else 'ALPHA SELFTEST FAILED')
    return not fails


if __name__ == '__main__':
    if sys.argv[1:] == ['--selftest']: sys.exit(0 if selftest() else 1)
    src = open(sys.argv[1]).read(); new, status = add(src)
    open(sys.argv[2], 'w').write(new); print('ALPHA TEST ' + status)
