# 回归对照：把旧版(v0)与新版(同目录的 danmaku_editor.py)放进同一个进程，
# 逐场景跑 CLI 并逐字段比对。
#
# 用法: python regress.py <旧版.py> <新版.py> <fixture.xml> <报告.json>
#
# 要点：
#   * 直接调 cli_main(argv)，绕开入口判断，不会弹出 GUI 窗口
#   * 同时捕获 stdout / stderr / SystemExit 退出码，并读回输出文件
#   * 每版用各自独立的输入/输出文件，路径在报告里归一化成 <IN> / <OUT>
import contextlib
import importlib.util
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# (场景名, CLI 参数)
CASES = [
    ('A_list',              ['--list']),
    ('B_shift_del',         ['-s', '2.5', '-d', 'awsl', '233', '--delete-empty']),
    ('C_regex_color_range', ['-r', r'\d+', '--delete-color', '#FF0000',
                             '--delete-range', '10', '20']),
    ('D_keep_regex',        ['--keep-regex', 'awsl|对的对的', '-s', '-1.5']),
    ('E_keep_exact',        ['--keep', '233', '对的对的']),
    ('F_bad_regex',         ['-r', '[unclosed']),
    ('G_shift_range',       ['--shift-range', '3', '6', '-s', '1.25']),
    ('H_noop',              []),
    ('I_keep_range',        ['--keep-range', '4', '9']),
    ('J_keep_color',        ['--keep-color', '#FF0000']),
    ('K_multi_regex',       ['-r', r'\d+', 'awsl']),
    ('L_keep_exact_shift',  ['--keep', '233', '-s', '1.0']),
    ('M_bad_color',         ['--delete-color', 'notacolor']),
    ('N_shift_only',        ['-s', '0.5']),
    ('O_delete_missing',    ['-d', '不存在的内容']),
    ('P_huge_shift',        ['-s', '100000']),
    ('Q_neg_shift_range',   ['--shift-range', '4', '8', '-s', '-3.5']),
]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        spec.loader.exec_module(mod)
    return mod


def norm(text):
    return text.replace('\r\n', '\n')


def run_cli(mod, argv):
    """跑一次 cli_main，返回 (退出码, stdout, stderr)。"""
    old_argv = sys.argv
    sys.argv = ['danmaku_editor.py'] + argv
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            # v4 的 cli_main 返回退出码，v0 的抛 SystemExit，两种都要接住
            code = mod.cli_main() or 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except Exception as e:  # 未捕获异常也当成一种可观测结果
        code = 'EXC'
        print(repr(e), file=out)
    finally:
        sys.argv = old_argv
    return code, norm(out.getvalue()), norm(err.getvalue())


def run_case(mod, name, argv, tmp, tag, fixture_bytes):
    inp = os.path.join(tmp, '%s_%s_in.xml' % (name, tag))
    outp = os.path.join(tmp, '%s_%s_out.xml' % (name, tag))
    with open(inp, 'wb') as f:
        f.write(fixture_bytes)
    if os.path.exists(outp):
        os.remove(outp)

    code, out, err = run_cli(mod, [inp, '-o', outp] + argv)
    data_out = open(outp, 'rb').read() if os.path.exists(outp) else b''

    def rel(s):
        return s.replace(inp, '<IN>').replace(outp, '<OUT>')

    return {
        'code': code,
        'out': rel(out),
        'err': rel(err),
        'xml': rel(data_out.decode('utf-8', 'replace')),
        'nl_kept': data_out == fixture_bytes,
    }


META_TAGS = ('chatserver', 'chatid', 'mission', 'maxlimit', 'state',
             'real_name', 'source')
META_TAG_RE = re.compile(r'<(/?)(?:%s)[^>]*>' % '|'.join(META_TAGS))


def xml_norm(text):
    """把两版有意不同的排版归一化后再比内容。

    v0 与 v4 的差异只有两处，都是 v0 的缺陷、本次有意修掉的：
      * v0 写文件时由 Python 把 \\n 翻成 os.linesep，v4 保持输入风格；
      * v0 会把 <i> 与紧随其后的元信息行重新挤成同一行，v4 逐字保留原文。
    因此头部（<i> + 元信息）只比标签序列，弹幕行逐字比，收尾换行不比。
    """
    text = text.replace('\r\n', '\n')
    lines = text.split('\n')
    idx = next((i for i, ln in enumerate(lines) if '<d ' in ln), len(lines))
    head = ''.join(m.group(0) for m in META_TAG_RE.finditer(''.join(lines[:idx])))
    return '\n'.join([head] + lines[idx:]).rstrip('\n')


def main():
    if len(sys.argv) != 5:
        print('用法: python regress.py <旧版.py> <新版.py> <fixture.xml> <报告.json>')
        return 2
    old_py, new_py, fixture, report_path = sys.argv[1:]

    fixture_bytes = open(fixture, 'rb').read()
    v0 = load(old_py, 'dmk_v0')
    v4 = load(new_py, 'dmk_v4')

    tmp = os.path.join(HERE, 'cmp_work')
    os.makedirs(tmp, exist_ok=True)

    report = {}
    for name, argv in CASES:
        report[name] = {tag: run_case(mod, name, argv, tmp, tag, fixture_bytes)
                        for tag, mod in (('v0', v0), ('v4', v4))}

    bad = 0
    nl_fixed = []
    for name, _argv in CASES:
        pair = report[name]
        for field in ('code', 'out', 'err'):
            if pair['v0'][field] != pair['v4'][field]:
                bad += 1
                print('DIFF %-20s %s' % (name, field))
                print('   v0: %r' % (pair['v0'][field],))
                print('   v4: %r' % (pair['v4'][field],))
        a = xml_norm(pair['v0']['xml'])
        b = xml_norm(pair['v4']['xml'])
        if a != b:
            bad += 1
            print('DIFF %-20s xml' % name)
            print('   v0: %r' % (a,))
            print('   v4: %r' % (b,))
        if pair['v4']['nl_kept'] and not pair['v0']['nl_kept']:
            nl_fixed.append(name)

    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    print('CASES=%d MISMATCHES=%d' % (len(CASES), bad))
    print('NL_PRESERVED_BY_V4_ONLY=%d %s' % (len(nl_fixed), nl_fixed))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
