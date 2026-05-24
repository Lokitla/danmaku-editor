#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B站 XML 弹幕编辑器 v2.0
功能: 时间偏移 + 删除/保留（精确/正则/时间范围/颜色/空白）
模式: CLI（带参数运行） / GUI（无参数运行）
"""

import re
import os
import sys
import json
import argparse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import ctypes
from dataclasses import dataclass
from typing import Optional


# ── 环境适配: Windows CMD stdout 编码 ──

def _fix_stdout():
    if sys.platform == 'win32':
        try:
            import io
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
        except Exception:
            pass

_fix_stdout()

# ── Windows DPI 感知：消除高分屏模糊 ──

def _enable_dpi_aware():
    """启用 Windows DPI 感知，防止界面被拉伸模糊"""
    if sys.platform != 'win32':
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

_enable_dpi_aware()

# ═════════════════════════════════════════════════════════════
# 第一部分: 核心引擎
# ═════════════════════════════════════════════════════════════

@dataclass
class Danmaku:
    time: float
    mode: int
    font_size: int
    color: int
    timestamp: int
    pool: int
    user_hash: str
    dm_id: int
    p_raw: str
    text: str
    raw: str

    def rebuild(self, new_time: float) -> str:
        parts = self.p_raw.split(',')
        parts[0] = f'{new_time:.3f}'
        return f'<d p="{",".join(parts)}">{self.text}</d>'


def parse_xml(filepath: str) -> tuple:
    """解析 B站 XML 弹幕文件 -> (header_str, list[Danmaku])"""
    with open(filepath, 'r', encoding='utf-8') as f:
        raw = f.read()

    m = re.search(r'(<i>.*</i>)', raw, re.DOTALL)
    if not m:
        raise ValueError('未找到 <i>...</i> 根标签')

    xml_block = m.group(1)
    danmaku: list[Danmaku] = []

    for match in re.finditer(r'<d\s+p="([^"]*)"\s*>([^<]*)</d>', xml_block):
        attrs = match.group(1).split(',')
        time_sec = float(attrs[0]) if len(attrs) > 0 else 0.0
        mode     = int(attrs[1]) if len(attrs) > 1 else 1
        fsize    = int(attrs[2]) if len(attrs) > 2 else 25
        color    = int(attrs[3]) if len(attrs) > 3 else 16777215
        # B站 p 属性: 时间,模式,字号,颜色,时间戳,弹幕池,用户Hash,弹幕ID
        ts       = int(attrs[4]) if len(attrs) > 4 else 0
        pool     = int(attrs[5]) if len(attrs) > 5 else 0
        uh       = attrs[6] if len(attrs) > 6 else ''
        dmid     = int(attrs[7]) if len(attrs) > 7 else 0

        danmaku.append(Danmaku(
            time=time_sec, mode=mode, font_size=fsize, color=color,
            timestamp=ts, pool=pool, user_hash=uh, dm_id=dmid,
            p_raw=match.group(1), text=match.group(2), raw=match.group(0),
        ))

    # 提取 header
    header_start = raw.index('<i>')
    prefix = raw[:header_start]
    body_start = xml_block.index('>') + 1
    body = xml_block[body_start:-len('</i>')]
    meta_lines = [
        line for line in body.splitlines()
        if line.strip() and not line.strip().startswith('<d ')
    ]
    header = prefix + '<i>' + ''.join(meta_lines)
    if not header.endswith('\n'):
        header += '\n'
    return header, danmaku


def write_xml(filepath: str, header: str, danmaku: list[Danmaku]):
    lines = [header.rstrip('\n')]
    lines.extend(f'  {d.rebuild(d.time)}' for d in danmaku)
    lines.append('</i>')
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


# ── 时间偏移 ──

def shift_time(danmaku: list[Danmaku], offset: float,
               time_range: Optional[tuple] = None) -> int:
    count = 0
    for d in danmaku:
        if time_range and not (time_range[0] <= d.time <= time_range[1]):
            continue
        d.time = max(0, d.time + offset)
        count += 1
    return count


# ── 删除/保留 ──

def delete_exact(danmaku: list[Danmaku], texts: set, invert: bool = False) -> int:
    """invert=False -> 删除匹配的；invert=True -> 保留匹配的（删除其他）"""
    before = len(danmaku)
    if invert:
        danmaku[:] = [d for d in danmaku if d.text in texts]
    else:
        danmaku[:] = [d for d in danmaku if d.text not in texts]
    return before - len(danmaku)


def delete_regex(danmaku: list[Danmaku], patterns: list,
                 invert: bool = False) -> list:
    """返回 [(pattern, 删除条数), ...]"""
    results = []
    for pat_str in patterns:
        pat = re.compile(pat_str)
        before = len(danmaku)
        if invert:
            danmaku[:] = [d for d in danmaku if pat.search(d.text)]
        else:
            danmaku[:] = [d for d in danmaku if not pat.search(d.text)]
        results.append((pat_str, before - len(danmaku)))
    return results


def delete_range(danmaku: list[Danmaku], rng: tuple,
                 invert: bool = False) -> int:
    start, end = rng
    before = len(danmaku)
    if invert:
        danmaku[:] = [d for d in danmaku if start <= d.time <= end]
    else:
        danmaku[:] = [d for d in danmaku if not (start <= d.time <= end)]
    return before - len(danmaku)


def delete_empty(danmaku: list[Danmaku]) -> int:
    before = len(danmaku)
    danmaku[:] = [d for d in danmaku if d.text.strip()]
    return before - len(danmaku)


def delete_by_color(danmaku: list[Danmaku],
                    colors: list, invert: bool = False) -> int:
    """colors 支持 #FFFFFF 或十进制整数"""
    color_set = set()
    for c in colors:
        if isinstance(c, str) and c.startswith('#'):
            color_set.add(int(c[1:], 16))
        else:
            color_set.add(int(c))
    before = len(danmaku)
    if invert:
        danmaku[:] = [d for d in danmaku if d.color in color_set]
    else:
        danmaku[:] = [d for d in danmaku if d.color not in color_set]
    return before - len(danmaku)


def get_stats(danmaku: list[Danmaku]) -> dict:
    if not danmaku:
        return {'count': 0, 'time_min': 0, 'time_max': 0,
                'unique_texts': 0, 'modes': {}}
    from collections import Counter
    times = [d.time for d in danmaku]
    return {
        'count': len(danmaku),
        'time_min': min(times),
        'time_max': max(times),
        'unique_texts': len(set(d.text for d in danmaku)),
        'modes': dict(Counter(d.mode for d in danmaku).most_common()),
    }


# ═════════════════════════════════════════════════════════════
# 第二部分: 预设管理
# ═════════════════════════════════════════════════════════════

def _preset_dir() -> str:
    # 打包后使用 exe 所在目录，开发时使用脚本所在目录
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(base, 'presets')
    os.makedirs(d, exist_ok=True)
    return d


def list_presets() -> list:
    files = [f for f in os.listdir(_preset_dir()) if f.endswith('.json')]
    return sorted(os.path.splitext(f)[0] for f in files)


def load_preset(name: str) -> dict:
    with open(os.path.join(_preset_dir(), f'{name}.json'), 'r', encoding='utf-8') as f:
        return json.load(f)


def save_preset(name: str, data: dict):
    with open(os.path.join(_preset_dir(), f'{name}.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def delete_preset(name: str):
    path = os.path.join(_preset_dir(), f'{name}.json')
    if os.path.exists(path):
        os.remove(path)


# ═════════════════════════════════════════════════════════════
# 第三部分: CLI
# ═════════════════════════════════════════════════════════════

def run_cli(args):
    header, danmaku = parse_xml(args.input)

    if args.list:
        s = get_stats(danmaku)
        print(f'文件: {args.input}')
        print(f'弹幕总数: {s["count"]}')
        if s['count']:
            print(f'时间范围: {s["time_min"]:.3f}s ~ {s["time_max"]:.3f}s')
            print(f'唯一内容: {s["unique_texts"]}')
            print(f'弹幕模式分布: {s["modes"]}')
        return

    # 检查是否有任何操作
    ops = [args.shift, args.delete, args.regex, args.delete_range,
           args.delete_empty, args.delete_color,
           args.keep, args.keep_regex, args.keep_range, args.keep_color]
    if all(v is None for v in ops):
        print('错误: 至少需要指定一个操作。使用 -h 查看帮助。')
        sys.exit(1)

    total_deleted = 0

    # 保留模式（反向删除）
    if args.keep:
        n = delete_exact(danmaku, set(args.keep), invert=True)
        total_deleted += n
        print(f'[保留] 精确匹配 ({len(args.keep)} 个): 删除其他 {n} 条')

    if args.keep_regex:
        for pat, n in delete_regex(danmaku, args.keep_regex, invert=True):
            total_deleted += n
            print(f'[保留] 正则 "/{pat}/": 删除其他 {n} 条')

    if args.keep_range:
        t1, t2 = args.keep_range
        n = delete_range(danmaku, (t1, t2), invert=True)
        total_deleted += n
        print(f'[保留] 时间 {t1}s~{t2}s: 删除范围外 {n} 条')

    if args.keep_color:
        n = delete_by_color(danmaku, args.keep_color, invert=True)
        total_deleted += n
        print(f'[保留] 颜色: 删除其他 {n} 条')

    # 常规删除
    if args.delete_empty:
        n = delete_empty(danmaku)
        total_deleted += n
        print(f'[删除] 空白弹幕: {n} 条')

    if args.delete_color:
        n = delete_by_color(danmaku, args.delete_color)
        total_deleted += n
        print(f'[删除] 颜色: {n} 条')

    if args.delete_range:
        t1, t2 = args.delete_range
        n = delete_range(danmaku, (t1, t2))
        total_deleted += n
        print(f'[删除] 时间 {t1}s~{t2}s: {n} 条')

    if args.delete:
        n = delete_exact(danmaku, set(args.delete))
        total_deleted += n
        print(f'[删除] 精确匹配 ({len(args.delete)} 个): {n} 条')

    if args.regex:
        for pat, n in delete_regex(danmaku, args.regex):
            total_deleted += n
            print(f'[删除] 正则 "/{pat}/": {n} 条')

    # 偏移
    if args.shift is not None:
        tr = tuple(args.shift_range) if args.shift_range else None
        n = shift_time(danmaku, args.shift, time_range=tr)
        rng_info = f' [{tr[0]}s~{tr[1]}s]' if tr else ''
        print(f'[偏移] {args.shift:+.3f} 秒{rng_info}, 影响 {n} 条')

    output = args.output or args.input
    write_xml(output, header, danmaku)
    print(f'[完成] 输出: {output}')
    print(f'[完成] 剩余: {len(danmaku)} 条')
    if total_deleted:
        print(f'[完成] 总计删除: {total_deleted} 条')


def cli_main():
    parser = argparse.ArgumentParser(
        description='B站 XML 弹幕编辑器 v2',
        epilog='''使用示例:
  %(prog)s input.xml --list
  %(prog)s input.xml -o out.xml -s 2.5 -d "文本" "文本2"
  %(prog)s input.xml -r "\\\\d+" --delete-range 0 60
  %(prog)s input.xml --keep "对的对的" --delete-empty
  %(prog)s input.xml --delete-color "#FF0000"
  %(prog)s input.xml --keep-regex "awsl|可爱" -s -0.5''',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument('input', help='输入的 XML 文件路径')
    parser.add_argument('-o', '--output', help='输出路径（默认覆盖原文件）')

    g_shift = parser.add_argument_group('时间偏移')
    g_shift.add_argument('-s', '--shift', type=float, metavar='秒',
                         help='偏移量，正数延后、负数提前')
    g_shift.add_argument('--shift-range', type=float, nargs=2, metavar=('起始', '结束'),
                         help='只偏移指定时间范围')

    g_del = parser.add_argument_group('删除')
    g_del.add_argument('-d', '--delete', nargs='*', metavar='文本',
                       help='精确删除指定文本')
    g_del.add_argument('-r', '--regex', nargs='*', metavar='正则',
                       help='正则删除')
    g_del.add_argument('--delete-range', type=float, nargs=2, metavar=('起始', '结束'),
                       help='删除时间范围')
    g_del.add_argument('--delete-empty', action='store_true',
                       help='删除空白弹幕')
    g_del.add_argument('--delete-color', nargs='*', metavar='颜色',
                       help='删除指定颜色（如 #FF0000）')

    g_keep = parser.add_argument_group('保留模式（反向删除）')
    g_keep.add_argument('--keep', nargs='*', metavar='文本',
                        help='只保留精确匹配的文本')
    g_keep.add_argument('--keep-regex', nargs='*', metavar='正则',
                        help='只保留正则匹配的弹幕')
    g_keep.add_argument('--keep-range', type=float, nargs=2, metavar=('起始', '结束'),
                        help='只保留时间范围内的弹幕')
    g_keep.add_argument('--keep-color', nargs='*', metavar='颜色',
                        help='只保留指定颜色的弹幕')

    parser.add_argument('--list', action='store_true', help='显示统计信息（不修改文件）')

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()
    try:
        run_cli(args)
    except Exception as e:
        print(f'[错误] {e}', file=sys.stderr)
        sys.exit(1)



# ═════════════════════════════════════════════════════════════
# 第四部分: GUI
# ═════════════════════════════════════════════════════════════

_C = {
    'bg':          '#0d0d1a',
    'fg':          '#e8e8e8',
    'surface':     '#16162b',
    'surface2':    '#1e1e38',
    'accent':      '#7c4dff',
    'accent_dim':  '#5e35b1',
    'primary':     '#2962ff',
    'success':     '#00c853',
    'warn':        '#ffab00',
    'error':       '#ff1744',
    'entry_bg':    '#22223a',
    'entry_fg':    '#e8e8e8',
    'text_muted':  '#7a7a9a',
    'border':      '#2a2a45',
    'hover':       '#7c4dff',
}

import tkinter.font as _tkfont

_CANDIDATE_FONTS = [
    ('Microsoft YaHei UI', 12),
    ('Microsoft YaHei', 12),
    ('微软雅黑', 12),
    ('Microsoft JhengHei UI', 12),
    ('Microsoft JhengHei', 12),
    ('PingFang SC', 12),
    ('Noto Sans CJK SC', 12),
    ('Tahoma', 12),
    ('Segoe UI', 12),
    ('SimHei', 12),
]

def _detect_best_font():
    _r = None
    try:
        _r = tk.Tk()
        _r.withdraw()
        for name, sz in _CANDIDATE_FONTS:
            f = _tkfont.Font(family=name, size=sz)
            if f.actual()['family'] == name:
                _r.destroy()
                return name, sz
    except Exception:
        pass
    if _r:
        try: _r.destroy()
        except: pass
    return 'Microsoft YaHei', 12

FONT_FAMILY, FONT_SIZE = _detect_best_font()
_C['font'] = FONT_FAMILY
_C['font_size'] = FONT_SIZE
try:
    print(f'检测到字体: {FONT_FAMILY} {FONT_SIZE}pt')
except Exception:
    pass


def _rounded_rect(c, x1, y1, x2, y2, r=8, **kw):
    kw.setdefault('fill', _C['surface'])
    kw.setdefault('outline', _C['border'])
    kw.setdefault('width', 1)
    c.create_arc(x1, y1, x1+2*r, y1+2*r, start=90, extent=90, **kw)
    c.create_arc(x2-2*r, y1, x2, y1+2*r, start=0, extent=90, **kw)
    c.create_arc(x1, y2-2*r, x1+2*r, y2, start=180, extent=90, **kw)
    c.create_arc(x2-2*r, y2-2*r, x2, y2, start=270, extent=90, **kw)
    c.create_rectangle(x1+r, y1, x2-r, y1+r, **kw)
    c.create_rectangle(x1+r, y2-r, x2-r, y2, **kw)
    c.create_rectangle(x1, y1+r, x2, y2-r, **kw)


def _btn_canvas(master, text, command, width=48, height=24, bg=None, fg=None, r=5):
    bg = bg or _C['accent']
    fg = fg or 'white'
    c = tk.Canvas(master, bg=_C['bg'], highlightthickness=0, width=width, height=height)
    _rounded_rect(c, 0, 0, width-1, height-1, r=r, fill=bg, outline=bg)
    c.create_text(width//2, height//2, text=text, fill=fg, font=(_C['font'], 12))
    c.bind('<Button-1>', lambda e: command())
    return c


class ModeToggle(tk.Frame):
    MODE_DELETE = 0
    MODE_KEEP = 1

    def __init__(self, master, variable, label_del='删除模式', label_keep='保留模式', command=None):
        super().__init__(master, bg=_C['surface'])
        self.var = variable
        self._cmd = command
        self.label_del = label_del
        self.label_keep = label_keep

        self.inner = tk.Frame(self, bg=_C['surface'])
        self.inner.pack(fill=tk.X, padx=4, pady=4)

        self.indicator = tk.Canvas(self.inner, bg=_C['surface'],
                                    highlightthickness=0, width=22, height=22)
        self.indicator.pack(side=tk.LEFT)

        self.mode_label = tk.Label(self.inner, font=(_C['font'], 12, 'bold'),
                                    anchor=tk.W, padx=6)
        self.mode_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.inner.bind('<Button-1>', self._toggle)
        self.indicator.bind('<Button-1>', self._toggle)
        self.mode_label.bind('<Button-1>', self._toggle)
        self._refresh()

    def _toggle(self, event=None):
        self.var.set(not self.var.get())
        self._refresh()
        if self._cmd:
            self._cmd()

    def _refresh(self):
        self.indicator.delete('all')
        is_keep = self.var.get()
        if is_keep:
            _rounded_rect(self.indicator, 2, 2, 20, 20, r=4,
                           fill=_C['accent'], outline=_C['accent'])
            self.indicator.create_line(6, 11, 10, 15, 16, 7, fill='white', width=2)
            self.mode_label.config(text=f'\u25b6 {self.label_keep}', fg=_C['accent'])
            self.inner.config(bg='#1a1030')
            self.mode_label.config(bg='#1a1030')
            self.config(bg='#1a1030')
        else:
            _rounded_rect(self.indicator, 2, 2, 20, 20, r=4,
                           fill=_C['error'], outline=_C['error'])
            self.indicator.create_line(7, 7, 15, 15, fill='white', width=2)
            self.indicator.create_line(15, 7, 7, 15, fill='white', width=2)
            self.mode_label.config(text=f'\u25b6 {self.label_del}', fg=_C['error'])
            self.inner.config(bg=_C['surface'])
            self.mode_label.config(bg=_C['surface'])
            self.config(bg=_C['surface'])


class TitledCard(tk.Frame):
    def __init__(self, master, title, width=260, **kw):
        kw.setdefault('bg', _C['bg'])
        kw.setdefault('highlightthickness', 0)
        super().__init__(master, **kw)
        self.inner_bg = _C['surface']
        self.card_w = width
        self.title = title

        self.canvas = tk.Canvas(self, bg=_C['bg'], highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind('<Configure>', self._redraw)

        self.body = tk.Frame(self.canvas, bg=self.inner_bg, highlightthickness=0)
        self.canvas.create_window(2, 14, anchor='nw', window=self.body)

    def _redraw(self, event=None):
        w = self.winfo_width() or self.card_w
        h = self.winfo_height() or 80
        self.canvas.delete('bg')
        _rounded_rect(self.canvas, 0, 12, w-1, h-1, r=10,
                       fill=self.inner_bg, outline=_C['border'], tags='bg')
        self.canvas.create_line(16, 12, w-16, 12, fill=_C['accent'],
                                 width=3, capstyle='round', tags='bg')
        self.canvas.create_text(20, 12, text=self.title, fill=_C['accent'],
                                 font=(_C['font'], 12, 'bold'), anchor='nw', tags='bg')
        cw = max(10, w - 20)
        self.canvas.itemconfig(1, width=cw)
        self.body.config(width=cw)


class V2Entry(tk.Entry):
    def __init__(self, master, **kw):
        kw.setdefault('bg', _C['entry_bg'])
        kw.setdefault('fg', _C['entry_fg'])
        kw.setdefault('insertbackground', _C['fg'])
        kw.setdefault('relief', tk.FLAT)
        kw.setdefault('font', (_C['font'], 12))
        kw.setdefault('bd', 0)
        kw.setdefault('highlightthickness', 1)
        kw.setdefault('highlightcolor', _C['border'])
        kw.setdefault('highlightbackground', _C['border'])
        super().__init__(master, **kw)
        self.bind('<FocusIn>', lambda e: self.config(highlightcolor=_C['accent'], highlightbackground=_C['accent']))
        self.bind('<FocusOut>', lambda e: self.config(highlightcolor=_C['border'], highlightbackground=_C['border']))


class V2TagList(tk.Frame):
    def __init__(self, master, **kw):
        super().__init__(master, bg=_C['surface'], **kw)
        self.input_var = tk.StringVar()
        ef = tk.Frame(self, bg=_C['surface'])
        ef.pack(fill=tk.X)

        self.entry = V2Entry(ef, textvariable=self.input_var)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.entry.bind('<Return>', lambda e: self._add())

        add_c = _btn_canvas(ef, '+', self._add, width=24, height=22, bg=_C['success'])
        add_c.pack(side=tk.LEFT)
        rm_c = _btn_canvas(ef, '-', self._remove, width=24, height=22, bg=_C['error'])
        rm_c.pack(side=tk.LEFT, padx=(3, 0))

        bf = tk.Frame(self, bg=_C['surface'])
        bf.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.listbox = tk.Listbox(bf, bg=_C['entry_bg'], fg=_C['fg'],
                                   selectbackground=_C['accent'], selectforeground='white',
                                   relief=tk.FLAT, bd=0, font=(_C['font'], 12), height=4,
                                   highlightthickness=0)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = tk.Scrollbar(bf, orient=tk.VERTICAL, width=8, bg=_C['surface'], troughcolor=_C['entry_bg'])
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.config(yscrollcommand=sb.set)
        sb.config(command=self.listbox.yview)

    def _add(self):
        v = self.input_var.get().strip()
        if v:
            self.listbox.insert(tk.END, v)
            self.input_var.set('')

    def _remove(self):
        sel = self.listbox.curselection()
        if sel:
            self.listbox.delete(sel[0])

    def get_items(self):
        return list(self.listbox.get(0, tk.END))

    def set_items(self, items):
        self.listbox.delete(0, tk.END)
        for it in items:
            self.listbox.insert(tk.END, it)

class DanmakuEditorApp:
    def __init__(self, root, file_to_open=None):
        self.root = root
        root.title('弹幕编辑器')
        root.geometry('920x820')
        root.configure(bg=_C['bg'])
        root.minsize(760, 680)
        self.input_path = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.use_same_dir = tk.BooleanVar(value=True)
        self.output_suffix = tk.StringVar(value='_edited')
        self.shift_var = tk.StringVar()
        self.shift_start = tk.StringVar()
        self.shift_end = tk.StringVar()
        self.del_start = tk.StringVar()
        self.del_end = tk.StringVar()
        self.del_color = tk.StringVar()
        self.keep_mode = tk.BooleanVar(value=False)
        self.keep_range_var = tk.BooleanVar(value=False)
        self.keep_color_var = tk.BooleanVar(value=False)
        self.del_empty_var = tk.BooleanVar(value=False)
        self._build_ui()
        self._refresh_preset_list()
        if file_to_open:
            self._open_file(file_to_open)

    def _log(self, msg):
        from datetime import datetime
        try:
            self.log.config(state=tk.NORMAL)
            ts = datetime.now().strftime('%H:%M:%S')
            self.log.insert(tk.END, '  [{}] {}\n'.format(ts, msg))
            self.log.see(tk.END)
            self.log.config(state=tk.DISABLED)
        except Exception:
            pass

    def _open_file(self, path):
        if os.path.isfile(path):
            self.input_path.set(path)
            self.in_label.config(text=os.path.basename(path), fg=_C['fg'])
            self._log('文件已加载: ' + os.path.basename(path))

    def _build_ui(self):
        main = tk.Frame(self.root, bg=_C['bg'])
        main.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)
        left = tk.Frame(main, bg=_C['bg'])
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        self._build_file_panel(left)
        self._build_preset_panel(left)
        center = tk.Frame(main, bg=_C['bg'])
        center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._build_shift_panel(center)
        self._build_delete_panel(center)
        self._build_bottom_panel()

    def _build_file_panel(self, parent):
        c = TitledCard(parent, '文件', width=270)
        c.pack(fill=tk.X, pady=(0, 8))
        b = c.body
        tk.Label(b, text='选择 B站 XML 弹幕文件', bg=_C['surface'],
                 fg=_C['fg'], font=(_C['font'], 12, 'bold'), anchor=tk.W).pack(fill=tk.X, pady=(0, 4))
        ir = tk.Frame(b, bg=_C['surface'])
        ir.pack(fill=tk.X)
        _btn_canvas(ir, '...', self._browse_input, width=36, height=22).pack(side=tk.LEFT)
        self.in_label = tk.Label(ir, text='未选择', bg=_C['entry_bg'], fg=_C['text_muted'],
                                  anchor=tk.W, padx=8, pady=3)
        self.in_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
        _btn_canvas(ir, 'i', self._show_stats, width=24, height=22, bg=_C['primary']).pack(side=tk.LEFT, padx=(5, 0))
        tk.Frame(b, height=1, bg=_C['border']).pack(fill=tk.X, pady=6)
        tk.Label(b, text='输出设置', bg=_C['surface'], fg=_C['fg'],
                 font=(_C['font'], 12, 'bold'), anchor=tk.W).pack(fill=tk.X)
        sd_frame = tk.Frame(b, bg=_C['surface'])
        sd_frame.pack(fill=tk.X, pady=(3, 4))
        sd_cb = tk.Canvas(sd_frame, bg=_C['surface'], highlightthickness=0, width=22, height=22)
        sd_cb.pack(side=tk.LEFT, padx=(2, 6))
        def _r1():
            sd_cb.delete('all')
            chk = self.use_same_dir.get()
            bc = _C['accent'] if chk else _C['entry_bg']
            bo = _C['accent'] if chk else _C['border']
            _rounded_rect(sd_cb, 2, 2, 20, 20, r=4, fill=bc, outline=bo)
            if chk:
                sd_cb.create_line(6, 11, 10, 15, 16, 7, fill='white', width=2)
        _r1()
        def _t1(e):
            self.use_same_dir.set(not self.use_same_dir.get())
            self._toggle_output_dir()
            _r1()
        sd_cb.bind('<Button-1>', _t1)
        tk.Label(sd_frame, text='与原文件同目录', bg=_C['surface'],
                 fg=_C['fg'], font=(_C['font'], 12)).pack(side=tk.LEFT)
        or_ = tk.Frame(b, bg=_C['surface'])
        or_.pack(fill=tk.X)
        _btn_canvas(or_, '目录', self._browse_output, width=40, height=22).pack(side=tk.LEFT)
        self.out_label = tk.Label(or_, text='同目录', bg=_C['entry_bg'],
                                   fg=_C['text_muted'], anchor=tk.W, padx=8, pady=3)
        self.out_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
        sr_ = tk.Frame(b, bg=_C['surface'])
        sr_.pack(fill=tk.X, pady=(6, 0))
        tk.Label(sr_, text='后缀', bg=_C['surface'], fg=_C['fg'],
                 font=(_C['font'], 12)).pack(side=tk.LEFT)
        V2Entry(sr_, textvariable=self.output_suffix, width=14).pack(side=tk.LEFT, padx=(5, 0))

    def _build_preset_panel(self, parent):
        c = TitledCard(parent, '预设', width=270)
        c.pack(fill=tk.X)
        b = c.body
        nr = tk.Frame(b, bg=_C['surface'])
        nr.pack(fill=tk.X)
        self.preset_name = V2Entry(nr, width=16)
        self.preset_name.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _btn_canvas(nr, 'S', self._save_preset, width=24, height=22, bg=_C['success']).pack(side=tk.LEFT, padx=(3, 2))
        _btn_canvas(nr, 'L', self._load_preset, width=24, height=22, bg=_C['primary']).pack(side=tk.LEFT, padx=2)
        _btn_canvas(nr, 'X', self._del_preset, width=24, height=22, bg=_C['error']).pack(side=tk.LEFT, padx=(2, 0))
        self.preset_listbox = tk.Listbox(b, bg=_C['entry_bg'], fg=_C['fg'],
                                          selectbackground=_C['accent'], selectforeground='white',
                                          relief=tk.FLAT, bd=0, font=(_C['font'], 12), height=6,
                                          highlightthickness=0)
        self.preset_listbox.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.preset_listbox.bind('<<ListboxSelect>>', self._on_preset_click)

    def _build_shift_panel(self, parent):
        c = TitledCard(parent, '时间偏移', width=420)
        c.pack(fill=tk.X, pady=(0, 8))
        c.card_w = 420
        b = c.body
        tk.Label(b, text='正数 = 延后，负数 = 提前', bg=_C['surface'],
                 fg=_C['text_muted'], font=(_C['font'], 11)).pack(anchor=tk.W, padx=2, pady=(0, 6))
        gd = tk.Frame(b, bg=_C['surface'])
        gd.pack(fill=tk.X)
        tk.Label(gd, text='偏移量 (秒)', bg=_C['surface'], fg=_C['fg'],
                 font=(_C['font'], 12)).grid(row=0, column=0, sticky=tk.W, pady=4)
        V2Entry(gd, textvariable=self.shift_var, width=10).grid(row=0, column=1, sticky=tk.W, padx=6)
        tk.Label(gd, text='限定范围', bg=_C['surface'], fg=_C['fg'],
                 font=(_C['font'], 12)).grid(row=1, column=0, sticky=tk.W, pady=4)
        sr = tk.Frame(gd, bg=_C['surface'])
        sr.grid(row=1, column=1, sticky=tk.W, padx=6)
        V2Entry(sr, textvariable=self.shift_start, width=7).pack(side=tk.LEFT)
        tk.Label(sr, text=' ~ ', bg=_C['surface'], fg=_C['fg']).pack(side=tk.LEFT)
        V2Entry(sr, textvariable=self.shift_end, width=7).pack(side=tk.LEFT)
        tk.Label(sr, text='秒', bg=_C['surface'], fg=_C['text_muted'],
                 font=(_C['font'], 11)).pack(side=tk.LEFT, padx=(4, 0))
# ═════════════════════════════════════════════════════════════
# 第五部分: 入口
# ═════════════════════════════════════════════════════════════


    def _build_delete_panel(self, parent):
        c = TitledCard(parent, '删除设置', width=420)
        c.pack(fill=tk.BOTH, expand=True)
        c.card_w = 420
        b = c.body
        nb = ttk.Notebook(b)
        nb.pack(fill=tk.BOTH, expand=True)
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TNotebook', background=_C['surface'], borderwidth=0)
        style.configure('TNotebook.Tab', background=_C['entry_bg'], foreground=_C['fg'], padding=[6, 2], font=(_C['font'], 12))
        style.map('TNotebook.Tab', background=[('selected', _C['accent'])], foreground=[('selected', 'white')])
        t1 = tk.Frame(nb, bg=_C['surface'])
        nb.add(t1, text='精确匹配')
        ModeToggle(t1, self.keep_mode, label_del='删除模式：删除列表中输入的弹幕', label_keep='保留模式：只保留列表中输入的弹幕，删除其他').pack(fill=tk.X)
        tk.Frame(t1, height=1, bg=_C['border']).pack(fill=tk.X, padx=6, pady=2)
        tk.Label(t1, text='输入要精确匹配的弹幕文本，一行一条', bg=_C['surface'], fg=_C['text_muted'], font=(_C['font'], 11)).pack(anchor=tk.W, padx=8, pady=(6, 0))
        self.exact_list = V2TagList(t1)
        self.exact_list.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        t2 = tk.Frame(nb, bg=_C['surface'])
        nb.add(t2, text='正则匹配')
        ModeToggle(t2, self.keep_mode, label_del='删除模式：删除匹配正则的弹幕', label_keep='保留模式：只保留匹配正则的弹幕，删除其他').pack(fill=tk.X)
        tk.Frame(t2, height=1, bg=_C['border']).pack(fill=tk.X, padx=6, pady=2)
        hf = tk.Frame(t2, bg=_C['surface'])
        hf.pack(fill=tk.X, padx=8, pady=(6, 2))
        tk.Label(hf, text='常用正则示例：', bg=_C['surface'], fg=_C['accent'], font=(_C['font'], 11, 'bold')).pack(anchor=tk.W)
        for pat, desc in [('  \\d+              ', '匹配纯数字'), ('  awsl|可爱|来了   ', '多关键词'), ('  ^.{1,2}$          ', '短弹幕'), ('  [\\u4e00-\\u9fff]  ', '包含中文')]:
            hr = tk.Frame(t2, bg=_C['surface'])
            hr.pack(fill=tk.X, padx=8)
            tk.Label(hr, text=pat, bg=_C['surface'], fg=_C['fg'], font=('Consolas', 11), anchor=tk.W, width=22).pack(side=tk.LEFT)
            tk.Label(hr, text=desc, bg=_C['surface'], fg=_C['text_muted'], font=(_C['font'], 10), anchor=tk.W).pack(side=tk.LEFT, padx=(4, 0))
        tk.Label(t2, text='在下方输入正则表达式，一行一条', bg=_C['surface'], fg=_C['text_muted'], font=(_C['font'], 11)).pack(anchor=tk.W, padx=8, pady=(6, 0))
        self.regex_list = V2TagList(t2)
        self.regex_list.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        t3 = tk.Frame(nb, bg=_C['surface'])
        nb.add(t3, text='时间范围')
        ModeToggle(t3, self.keep_range_var, label_del='删除该时间段的弹幕', label_keep='只保留该时间段的弹幕').pack(fill=tk.X)
        tk.Frame(t3, height=1, bg=_C['border']).pack(fill=tk.X, padx=6, pady=2)
        tk.Label(t3, text='设置时间范围（秒）', bg=_C['surface'], fg=_C['text_muted'], font=(_C['font'], 11)).pack(anchor=tk.W, padx=8, pady=(8, 4))
        rr = tk.Frame(t3, bg=_C['surface'])
        rr.pack(padx=8, pady=(4, 12))
        V2Entry(rr, textvariable=self.del_start, width=9).pack(side=tk.LEFT)
        tk.Label(rr, text=' ~ ', bg=_C['surface'], fg=_C['fg']).pack(side=tk.LEFT)
        V2Entry(rr, textvariable=self.del_end, width=9).pack(side=tk.LEFT)
        tk.Label(rr, text=' 秒', bg=_C['surface'], fg=_C['text_muted']).pack(side=tk.LEFT, padx=(4, 0))
        t4 = tk.Frame(nb, bg=_C['surface'])
        nb.add(t4, text='颜色 / 空白')
        tk.Label(t4, text='按颜色删除弹幕', bg=_C['surface'], fg=_C['fg'], font=(_C['font'], 12, 'bold'), anchor=tk.W).pack(fill=tk.X, padx=8, pady=(8, 2))
        tk.Label(t4, text='颜色值，逗号分隔（#FF0000,#00FF00）', bg=_C['surface'], fg=_C['text_muted'], font=(_C['font'], 11)).pack(anchor=tk.W, padx=8)
        cr = tk.Frame(t4, bg=_C['surface'])
        cr.pack(padx=8, pady=6, fill=tk.X)
        V2Entry(cr, textvariable=self.del_color, width=20).pack(fill=tk.X)
        ModeToggle(t4, self.keep_color_var, label_del='删除这些颜色', label_keep='只保留这些颜色').pack(fill=tk.X, padx=4)
        tk.Frame(t4, height=1, bg=_C['border']).pack(fill=tk.X, padx=8, pady=8)
        tk.Label(t4, text='其他操作', bg=_C['surface'], fg=_C['fg'], font=(_C['font'], 12, 'bold'), anchor=tk.W).pack(fill=tk.X, padx=8)
        ef = tk.Frame(t4, bg=_C['surface'])
        ef.pack(fill=tk.X, padx=8, pady=(4, 8))
        ec = tk.Canvas(ef, bg=_C['surface'], highlightthickness=0, width=22, height=22)
        ec.pack(side=tk.LEFT)
        def _re():
            ec.delete('all')
            chk = self.del_empty_var.get()
            bc = _C['accent'] if chk else _C['entry_bg']
            bo = _C['accent'] if chk else _C['border']
            _rounded_rect(ec, 2, 2, 20, 20, r=4, fill=bc, outline=bo)
            if chk:
                ec.create_line(6, 11, 10, 15, 16, 7, fill='white', width=2)
        _re()
        def _te(e):
            self.del_empty_var.set(not self.del_empty_var.get())
            _re()
        ec.bind('<Button-1>', _te)
        tk.Label(ef, text='删除空白/纯空格弹幕', bg=_C['surface'], fg=_C['fg'], font=(_C['font'], 12)).pack(side=tk.LEFT, padx=(6, 0))

    def _build_bottom_panel(self):
        bottom = tk.Frame(self.root, bg=_C['bg'])
        bottom.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 14))
        ab = tk.Frame(bottom, bg='#1a1a30')
        ab.pack(fill=tk.X, pady=(0, 6))
        run_btn = tk.Button(ab, text='▶ 执行', bg=_C['success'], fg='white', relief=tk.FLAT, bd=0, font=(_C['font'], 11, 'bold'), padx=28, pady=7, cursor='hand2', activebackground='#00a86b', activeforeground='white', command=self._execute)
        run_btn.pack(side=tk.LEFT, padx=10, pady=6)
        clear_btn = tk.Button(ab, text='清除', bg='#555', fg=_C['fg'], relief=tk.FLAT, bd=0, font=(_C['font'], 10), padx=14, cursor='hand2', activebackground='#777', activeforeground='white', command=self._clear_log)
        clear_btn.pack(side=tk.LEFT, padx=4, pady=6)
        lf = tk.Frame(bottom, bg=_C['entry_bg'])
        lf.pack(fill=tk.BOTH, expand=True)
        self.log = tk.Text(lf, bg=_C['entry_bg'], fg=_C['fg'], insertbackground=_C['fg'], relief=tk.FLAT, bd=0, padx=10, pady=6, font=('Consolas', 10), state=tk.DISABLED, wrap=tk.WORD)
        self.log.pack(fill=tk.BOTH, expand=True)
        sb = tk.Scrollbar(self.log, orient=tk.VERTICAL, width=8)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log.config(yscrollcommand=sb.set)
        sb.config(command=self.log.yview)
        self._log('就绪。')

    def _browse_input(self):
        p = filedialog.askopenfilename(title='选择 B站 XML 弹幕文件', filetypes=[('XML', '*.xml'), ('*', '*.*')])
        if p:
            self._open_file(p)

    def _browse_output(self):
        p = filedialog.askdirectory(title='选择输出目录')
        if p:
            self.output_dir.set(p)
            self.out_label.config(text=p, fg=_C['fg'])

    def _toggle_output_dir(self):
        if self.use_same_dir.get():
            self.out_label.config(text='同目录', fg=_C['text_muted'])
        else:
            self.out_label.config(text=self.output_dir.get() or '未选择', fg=_C['fg'] if self.output_dir.get() else _C['text_muted'])

    def _show_stats(self):
        path = self.input_path.get()
        if not path:
            return
        try:
            _, danmaku = parse_xml(path)
            s = get_stats(danmaku)
            md = {1: '滚动', 4: '底部', 5: '顶部', 6: '逆向'}
            ms = ', '.join(f'{md.get(k,k)}:{v}' for k,v in s['modes'].items())
            messagebox.showinfo('统计', '弹幕总数: {}\n时间范围: {:.3f}s~{}s\n唯一内容: {}\n弹幕模式: {}'.format(s['count'], s['time_min'], s['time_max'], s['unique_texts'], ms))
        except Exception:
            pass

    def _gather_settings(self):
        return {
            'shift': self._fn(self.shift_var.get()), 'shift_start': self._fn(self.shift_start.get()), 'shift_end': self._fn(self.shift_end.get()),
            'delete_exact': self.exact_list.get_items(), 'delete_regex': self.regex_list.get_items(),
            'delete_range_start': self._fn(self.del_start.get()), 'delete_range_end': self._fn(self.del_end.get()),
            'delete_color': self.del_color.get().strip(), 'delete_empty': self.del_empty_var.get(),
            'keep_mode': self.keep_mode.get(), 'keep_range': self.keep_range_var.get(), 'keep_color': self.keep_color_var.get(),
            'use_same_dir': self.use_same_dir.get(), 'output_dir': self.output_dir.get(), 'output_suffix': self.output_suffix.get(),
        }

    def _apply_settings(self, d):
        def sv(k):
            v = d.get(k)
            return str(v) if v is not None and v != '' else ''
        self.shift_var.set(sv('shift'))
        self.shift_start.set(sv('shift_start'))
        self.shift_end.set(sv('shift_end'))
        self.exact_list.set_items(d.get('delete_exact', []))
        self.regex_list.set_items(d.get('delete_regex', []))
        self.del_start.set(sv('delete_range_start'))
        self.del_end.set(sv('delete_range_end'))
        self.del_color.set(d.get('delete_color', ''))
        self.del_empty_var.set(d.get('delete_empty', False))
        self.keep_mode.set(d.get('keep_mode', False))
        self.keep_range_var.set(d.get('keep_range', False))
        self.keep_color_var.set(d.get('keep_color', False))
        self.use_same_dir.set(d.get('use_same_dir', True))
        self.output_dir.set(d.get('output_dir', ''))
        self.output_suffix.set(d.get('output_suffix', '_edited'))
        self._toggle_output_dir()

    @staticmethod
    def _fn(v):
        try: return float(v.strip()) if v.strip() else None
        except ValueError: return None

    def _on_preset_click(self, event):
        sel = self.preset_listbox.curselection()
        if sel:
            self.preset_name.delete(0, tk.END)
            self.preset_name.insert(0, self.preset_listbox.get(sel[0]))

    def _refresh_preset_list(self):
        self.preset_listbox.delete(0, tk.END)
        for n in list_presets():
            self.preset_listbox.insert(tk.END, n)

    def _save_preset(self):
        name = self.preset_name.get().strip()
        if not name:
            messagebox.showwarning('提示', '请输入预设名称')
            return
        save_preset(name, self._gather_settings())
        self._log('预设已保存: ' + name)
        self._refresh_preset_list()

    def _load_preset(self):
        name = self.preset_name.get().strip()
        if not name:
            messagebox.showwarning('提示', '请选择预设名称')
            return
        try:
            self._apply_settings(load_preset(name))
            self._log('预设已加载: ' + name)
        except FileNotFoundError:
            messagebox.showerror('错误', '预设 "{0}" 不存在'.format(name))
        except Exception as e:
            messagebox.showerror('错误', str(e))

    def _del_preset(self):
        name = self.preset_name.get().strip()
        if not name:
            return
        if messagebox.askyesno('确认', '删除预设 "{0}"？'.format(name)):
            delete_preset(name)
            self._log('预设已删除: ' + name)
            self._refresh_preset_list()

    def _clear_log(self):
        self.log.config(state=tk.NORMAL)
        self.log.delete(1.0, tk.END)
        self.log.config(state=tk.DISABLED)

    def _execute(self):
        inp = self.input_path.get()
        if not inp or not os.path.isfile(inp):
            messagebox.showwarning('提示', '请先选择 XML 文件')
            return
        try:
            header, danmaku = parse_xml(inp)
        except Exception as e:
            messagebox.showerror('错误', '解析失败: ' + str(e))
            return
        self._log('已读取: {0} ({1} 条弹幕)'.format(os.path.basename(inp), len(danmaku)))
        total_deleted = 0
        keep = self.keep_mode.get()
        exact_items = self.exact_list.get_items()
        regex_items = self.regex_list.get_items()
        if keep and exact_items:
            n = delete_exact(danmaku, set(exact_items), invert=True)
            total_deleted += n
            self._log('保留[精确]: 删除其他 ' + str(n) + ' 条')
        if keep and regex_items:
            for p, n in delete_regex(danmaku, regex_items, invert=True):
                total_deleted += n
                self._log('保留[正则] /' + p + '/: 删除其他 ' + str(n) + ' 条')
        ds, de = self._fn(self.del_start.get()), self._fn(self.del_end.get())
        if self.keep_range_var.get() and ds is not None and de is not None:
            n = delete_range(danmaku, (ds, de), invert=True)
            total_deleted += n
            self._log('保留[范围] ' + str(ds) + 's~' + str(de) + 's: 删除范围外 ' + str(n) + ' 条')
        color_str = self.del_color.get().strip()
        if self.keep_color_var.get() and color_str:
            cl = [c.strip() for c in color_str.split(',') if c.strip()]
            n = delete_by_color(danmaku, cl, invert=True)
            total_deleted += n
            self._log('保留[颜色]: 删除其他 ' + str(n) + ' 条')
        if not keep:
            if self.del_empty_var.get():
                n = delete_empty(danmaku)
                total_deleted += n
                self._log('删除: 空白 ' + str(n) + ' 条')
            if color_str and not self.keep_color_var.get():
                cl = [c.strip() for c in color_str.split(',') if c.strip()]
                n = delete_by_color(danmaku, cl)
                total_deleted += n
                self._log('删除: 颜色 ' + str(n) + ' 条')
            if ds is not None and de is not None and not self.keep_range_var.get():
                n = delete_range(danmaku, (ds, de))
                total_deleted += n
                self._log('删除: 时间 ' + str(ds) + 's~' + str(de) + 's ' + str(n) + ' 条')
            if exact_items:
                n = delete_exact(danmaku, set(exact_items))
                total_deleted += n
                self._log('删除: 精确 ' + str(n) + ' 条')
            if regex_items:
                for p, n in delete_regex(danmaku, regex_items):
                    total_deleted += n
                    self._log('删除: 正则 /' + p + '/ ' + str(n) + ' 条')
        sv = self._fn(self.shift_var.get())
        if sv is not None:
            ss, se = self._fn(self.shift_start.get()), self._fn(self.shift_end.get())
            tr = (ss, se) if (ss is not None and se is not None) else None
            n = shift_time(danmaku, sv, time_range=tr)
            ri = ' [' + str(ss) + 's~' + str(se) + 's]' if tr else ''
            self._log('偏移: {0:+.3f} 秒{1} ({2} 条)'.format(sv, ri, n))
        out_dir = os.path.dirname(inp) if self.use_same_dir.get() else (self.output_dir.get().strip() or os.path.dirname(inp))
        base, ext = os.path.splitext(os.path.basename(inp))
        suf = self.output_suffix.get().strip()
        out_path = os.path.join(out_dir, base + suf + ext)
        write_xml(out_path, header, danmaku)
        self._log('完成: 输出到 ' + out_path)
        self._log('完成: 剩余 ' + str(len(danmaku)) + ' 条')
        if total_deleted:
            self._log('完成: 删除 ' + str(total_deleted) + ' 条')
def gui_main(file_to_open=None):
    """启动 GUI（DPI 感知在 _enable_dpi_aware 中全局处理）"""
    root = tk.Tk()
    DanmakuEditorApp(root, file_to_open)
    root.mainloop()


if __name__ == '__main__':
    if len(sys.argv) >= 2 and not sys.argv[1].startswith('-'):
        gui_main(sys.argv[1])
    elif len(sys.argv) >= 2 and sys.argv[1].startswith('-'):
        cli_main()
    else:
        gui_main()
