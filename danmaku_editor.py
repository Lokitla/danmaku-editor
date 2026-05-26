#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B站 XML 弹幕编辑器 v3.0
功能: 时间偏移 + 删除/保留（精确/正则/时间范围/颜色/空白）
模式: CLI（带参数运行） / GUI（无参数运行）
UI: CustomTkinter 暗色主题
"""

import re
import os
import sys
import json
import argparse
from collections import Counter
from dataclasses import dataclass
from typing import Optional

import customtkinter as ctk
from tkinter import filedialog, messagebox
import tkinter.font as tkfont


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

# ── CustomTkinter 全局设置 ──
ctk.set_appearance_mode('dark')
ctk.set_default_color_theme('blue')

# ── 字体检测 ──
_FONT_CANDIDATES = [
    'Microsoft YaHei UI', 'Microsoft YaHei', '微软雅黑',
    'PingFang SC', 'Noto Sans CJK SC', 'Segoe UI', 'Tahoma',
]


def _detect_font():
    try:
        _r = ctk.CTk()
        _r.withdraw()
        for name in _FONT_CANDIDATES:
            try:
                f = tkfont.Font(family=name, size=12)
                if f.actual()['family'] == name:
                    _r.destroy()
                    return name
            except Exception:
                continue
        _r.destroy()
    except Exception:
        pass
    return 'Microsoft YaHei UI'


FONT = _detect_font()
try:
    print(f'[弹幕编辑器] 字体: {FONT}')
except Exception:
    pass


def ft(size=12, weight='normal'):
    """生成字体元组"""
    return (FONT, size, weight) if weight != 'normal' else (FONT, size)


# ═════════════════════════════════════════════════════════════
# 核心引擎
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
        ts       = int(attrs[4]) if len(attrs) > 4 else 0
        pool     = int(attrs[5]) if len(attrs) > 5 else 0
        uh       = attrs[6] if len(attrs) > 6 else ''
        dmid     = int(attrs[7]) if len(attrs) > 7 else 0

        danmaku.append(Danmaku(
            time=time_sec, mode=mode, font_size=fsize, color=color,
            timestamp=ts, pool=pool, user_hash=uh, dm_id=dmid,
            p_raw=match.group(1), text=match.group(2), raw=match.group(0),
        ))

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
    before = len(danmaku)
    if invert:
        danmaku[:] = [d for d in danmaku if d.text in texts]
    else:
        danmaku[:] = [d for d in danmaku if d.text not in texts]
    return before - len(danmaku)


def delete_regex(danmaku: list[Danmaku], patterns: list,
                 invert: bool = False) -> list:
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
    times = [d.time for d in danmaku]
    return {
        'count': len(danmaku),
        'time_min': min(times),
        'time_max': max(times),
        'unique_texts': len(set(d.text for d in danmaku)),
        'modes': dict(Counter(d.mode for d in danmaku).most_common()),
    }


# ═════════════════════════════════════════════════════════════
# 预设管理
# ═════════════════════════════════════════════════════════════

def _preset_dir() -> str:
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
# CLI
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

    ops = [args.shift, args.delete, args.regex, args.delete_range,
           args.delete_empty, args.delete_color,
           args.keep, args.keep_regex, args.keep_range, args.keep_color]
    if all(v is None for v in ops):
        print('错误: 至少需要指定一个操作。使用 -h 查看帮助。')
        sys.exit(1)

    total_deleted = 0

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
        description='B站 XML 弹幕编辑器 v3',
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
# GUI — CustomTkinter
# ═════════════════════════════════════════════════════════════

# 颜色常量
C_ACCENT    = '#7c4dff'
C_ACCENT2   = '#5e35b1'
C_SUCCESS   = '#00c853'
C_WARN      = '#ffab00'
C_ERROR     = '#ff1744'
C_PRIMARY   = '#2962ff'


class TagList(ctk.CTkFrame):
    """可增删的标签列表（替代原 V2TagList + tk.Listbox）"""

    def __init__(self, master, **kw):
        super().__init__(master, fg_color='transparent', **kw)
        self._selected = None

        # 输入行
        ef = ctk.CTkFrame(self, fg_color='transparent')
        ef.pack(fill='x')
        self.entry = ctk.CTkEntry(ef, placeholder_text='输入内容…')
        self.entry.pack(side='left', fill='x', expand=True, padx=(0, 4))
        self.entry.bind('<Return>', lambda e: self._add())
        ctk.CTkButton(ef, text='+', width=28, fg_color=C_SUCCESS,
                       command=self._add).pack(side='left', padx=2)
        ctk.CTkButton(ef, text='-', width=28, fg_color=C_ERROR,
                       command=self._remove).pack(side='left')

        # 列表显示
        self._box = ctk.CTkTextbox(self, height=90, activate_scrollbars=True)
        self._box.pack(fill='both', expand=True, pady=(4, 0))
        self._box.configure(state='disabled')
        self._box.bind('<Button-1>', self._on_click)

    def _on_click(self, event):
        idx = self._box.index(f'@{event.x},{event.y}').split('.')[0]
        try:
            self._select(int(idx) - 1)
        except (ValueError, IndexError):
            pass

    def _select(self, idx):
        self._selected = idx
        items = self.get_items()
        self._box.configure(state='normal')
        self._box.delete('1.0', 'end')
        for i, item in enumerate(items):
            prefix = '>> ' if i == idx else '   '
            self._box.insert('end', f'{prefix}{item}\n')
        self._box.configure(state='disabled')

    def _add(self):
        v = self.entry.get().strip()
        if v:
            self._box.configure(state='normal')
            items = self.get_items()
            items.append(v)
            self._box.delete('1.0', 'end')
            for item in items:
                self._box.insert('end', f'   {item}\n')
            self._box.configure(state='disabled')
            self.entry.delete(0, 'end')
            self._selected = None

    def _remove(self):
        if self._selected is not None:
            items = self.get_items()
            items.pop(self._selected)
            self._box.configure(state='normal')
            self._box.delete('1.0', 'end')
            for item in items:
                self._box.insert('end', f'   {item}\n')
            self._box.configure(state='disabled')
            self._selected = None

    def get_items(self):
        lines = self._box.get('1.0', 'end').strip().split('\n')
        return [l.strip().lstrip('> ') for l in lines if l.strip() and not l.strip().startswith('>> ')]

    def set_items(self, items):
        self._box.configure(state='normal')
        self._box.delete('1.0', 'end')
        for item in items:
            self._box.insert('end', f'   {item}\n')
        self._box.configure(state='disabled')
        self._selected = None


class DanmakuEditorApp:
    def __init__(self, root, file_to_open=None):
        self.root = root
        root.title('弹幕编辑器')
        root.geometry('920x820')
        root.minsize(760, 680)

        self.input_path = ctk.StringVar()
        self.output_dir = ctk.StringVar()
        self.use_same_dir = ctk.BooleanVar(value=True)
        self.output_suffix = ctk.StringVar(value='_edited')
        self.shift_var = ctk.StringVar()
        self.shift_start = ctk.StringVar()
        self.shift_end = ctk.StringVar()
        self.del_start = ctk.StringVar()
        self.del_end = ctk.StringVar()
        self.del_color = ctk.StringVar()
        self.keep_mode = ctk.BooleanVar(value=False)
        self.keep_range_var = ctk.BooleanVar(value=False)
        self.keep_color_var = ctk.BooleanVar(value=False)
        self.del_empty_var = ctk.BooleanVar(value=False)

        self._build_ui()
        self._refresh_preset_list()
        if file_to_open:
            self._open_file(file_to_open)

    # ── 日志 ──

    def _log(self, msg):
        from datetime import datetime
        try:
            self.log.configure(state='normal')
            ts = datetime.now().strftime('%H:%M:%S')
            self.log.insert('end', f'  [{ts}] {msg}\n')
            self.log.see('end')
            self.log.configure(state='disabled')
        except Exception:
            pass

    def _open_file(self, path):
        if os.path.isfile(path):
            self.input_path.set(path)
            self.in_label.configure(text=os.path.basename(path))
            self._log('文件已加载: ' + os.path.basename(path))

    # ── UI 构建 ──

    def _build_ui(self):
        main = ctk.CTkFrame(self.root)
        main.pack(fill='both', expand=True, padx=14, pady=14)

        left = ctk.CTkFrame(main, fg_color='transparent')
        left.pack(side='left', fill='y', padx=(0, 10))
        self._build_file_panel(left)
        self._build_preset_panel(left)

        center = ctk.CTkFrame(main, fg_color='transparent')
        center.pack(side='left', fill='both', expand=True)
        self._build_shift_panel(center)
        self._build_delete_panel(center)

        self._build_bottom_panel()

    def _build_file_panel(self, parent):
        card = ctk.CTkFrame(parent, corner_radius=10, border_width=1, border_color='#2a2a45')
        card.pack(fill='x', pady=(0, 8))

        # 标题
        ctk.CTkLabel(card, text='文件', font=ft(14, 'bold'),
                      text_color=C_ACCENT).pack(anchor='w', padx=12, pady=(10, 2))

        ctk.CTkLabel(card, text='选择 B站 XML 弹幕文件',
                      font=ft(12, 'bold')).pack(anchor='w', padx=12, pady=(4, 2))

        ir = ctk.CTkFrame(card, fg_color='transparent')
        ir.pack(fill='x', padx=12, pady=(0, 6))
        ctk.CTkButton(ir, text='...', width=36, command=self._browse_input
                       ).pack(side='left')
        self.in_label = ctk.CTkLabel(ir, text='未选择', text_color='#7a7a9a',
                                      anchor='w')
        self.in_label.pack(side='left', fill='x', expand=True, padx=5)
        ctk.CTkButton(ir, text='i', width=28, fg_color=C_PRIMARY,
                       command=self._show_stats).pack(side='left', padx=(5, 0))

        # 分隔线
        ctk.CTkFrame(card, height=1, fg_color='#2a2a45').pack(fill='x', padx=12, pady=4)

        ctk.CTkLabel(card, text='输出设置', font=ft(12, 'bold')
                      ).pack(anchor='w', padx=12)

        sd = ctk.CTkFrame(card, fg_color='transparent')
        sd.pack(fill='x', padx=12, pady=3)
        ctk.CTkSwitch(sd, text='与原文件同目录', variable=self.use_same_dir,
                       command=self._toggle_output_dir, switch_width=36,
                       onvalue=True, offvalue=False).pack(anchor='w')

        or_ = ctk.CTkFrame(card, fg_color='transparent')
        or_.pack(fill='x', padx=12, pady=(0, 4))
        ctk.CTkButton(or_, text='目录', width=44,
                       command=self._browse_output).pack(side='left')
        self.out_label = ctk.CTkLabel(or_, text='同目录', text_color='#7a7a9a',
                                       anchor='w')
        self.out_label.pack(side='left', fill='x', expand=True, padx=5)

        sr = ctk.CTkFrame(card, fg_color='transparent')
        sr.pack(fill='x', padx=12, pady=(0, 10))
        ctk.CTkLabel(sr, text='后缀').pack(side='left')
        ctk.CTkEntry(sr, textvariable=self.output_suffix, width=100
                      ).pack(side='left', padx=5)

    def _build_preset_panel(self, parent):
        card = ctk.CTkFrame(parent, corner_radius=10, border_width=1, border_color='#2a2a45')
        card.pack(fill='x')

        ctk.CTkLabel(card, text='预设', font=ft(14, 'bold'),
                      text_color=C_ACCENT).pack(anchor='w', padx=12, pady=(10, 2))

        nr = ctk.CTkFrame(card, fg_color='transparent')
        nr.pack(fill='x', padx=12)
        self.preset_name = ctk.CTkEntry(nr, placeholder_text='预设名称')
        self.preset_name.pack(side='left', fill='x', expand=True, padx=(0, 4))
        ctk.CTkButton(nr, text='S', width=28, fg_color=C_SUCCESS,
                       command=self._save_preset).pack(side='left', padx=1)
        ctk.CTkButton(nr, text='L', width=28, fg_color=C_PRIMARY,
                       command=self._load_preset).pack(side='left', padx=1)
        ctk.CTkButton(nr, text='X', width=28, fg_color=C_ERROR,
                       command=self._del_preset).pack(side='left')

        self.preset_listbox = ctk.CTkTextbox(card, height=120, activate_scrollbars=True)
        self.preset_listbox.pack(fill='both', expand=True, padx=12, pady=(4, 10))
        self.preset_listbox.configure(state='disabled')

    def _build_shift_panel(self, parent):
        card = ctk.CTkFrame(parent, corner_radius=10, border_width=1, border_color='#2a2a45')
        card.pack(fill='x', pady=(0, 8))

        ctk.CTkLabel(card, text='时间偏移', font=ft(14, 'bold'),
                      text_color=C_ACCENT).pack(anchor='w', padx=12, pady=(10, 2))

        ctk.CTkLabel(card, text='正数 = 延后，负数 = 提前',
                      text_color='#7a7a9a', font=ft(11)).pack(anchor='w', padx=12, pady=(0, 6))

        gd = ctk.CTkFrame(card, fg_color='transparent')
        gd.pack(fill='x', padx=12, pady=(0, 10))

        ctk.CTkLabel(gd, text='偏移量 (秒)').grid(row=0, column=0, sticky='w', pady=4)
        ctk.CTkEntry(gd, textvariable=self.shift_var, width=100
                      ).grid(row=0, column=1, sticky='w', padx=6)

        ctk.CTkLabel(gd, text='限定范围').grid(row=1, column=0, sticky='w', pady=4)
        sr = ctk.CTkFrame(gd, fg_color='transparent')
        sr.grid(row=1, column=1, sticky='w', padx=6)
        ctk.CTkEntry(sr, textvariable=self.shift_start, width=70).pack(side='left')
        ctk.CTkLabel(sr, text=' ~ ').pack(side='left')
        ctk.CTkEntry(sr, textvariable=self.shift_end, width=70).pack(side='left')
        ctk.CTkLabel(sr, text='秒', text_color='#7a7a9a').pack(side='left', padx=4)

    def _build_delete_panel(self, parent):
        card = ctk.CTkFrame(parent, corner_radius=10, border_width=1, border_color='#2a2a45')
        card.pack(fill='both', expand=True)

        ctk.CTkLabel(card, text='删除设置', font=ft(14, 'bold'),
                      text_color=C_ACCENT).pack(anchor='w', padx=12, pady=(10, 2))

        nb = ctk.CTkTabview(card)
        nb.pack(fill='both', expand=True, padx=8, pady=(4, 8))

        # ─ Tab 1: 精确匹配 ─
        t1 = nb.add('精确匹配')
        ctk.CTkSwitch(t1, text='保留模式（只保留列表中的弹幕，删除其他）',
                       variable=self.keep_mode, switch_width=36).pack(fill='x', padx=6, pady=4)
        ctk.CTkFrame(t1, height=1, fg_color='#2a2a45').pack(fill='x', padx=6, pady=2)
        ctk.CTkLabel(t1, text='输入要精确匹配的弹幕文本，一行一条',
                      text_color='#7a7a9a', font=ft(11)).pack(anchor='w', padx=8, pady=(4, 0))
        self.exact_list = TagList(t1)
        self.exact_list.pack(fill='both', expand=True, padx=6, pady=4)

        # ─ Tab 2: 正则匹配 ─
        t2 = nb.add('正则匹配')
        ctk.CTkSwitch(t2, text='保留模式（只保留匹配正则的弹幕，删除其他）',
                       variable=self.keep_mode, switch_width=36).pack(fill='x', padx=6, pady=4)
        ctk.CTkFrame(t2, height=1, fg_color='#2a2a45').pack(fill='x', padx=6, pady=2)

        hf = ctk.CTkFrame(t2, fg_color='transparent')
        hf.pack(fill='x', padx=8, pady=(6, 2))
        ctk.CTkLabel(hf, text='常用正则示例：', text_color=C_ACCENT,
                      font=ft(11, 'bold')).pack(anchor='w')
        for pat, desc in [('  \\d+              ', '匹配纯数字'),
                           ('  awsl|可爱|来了   ', '多关键词'),
                           ('  ^.{1,2}$          ', '短弹幕'),
                           ('  [\\u4e00-\\u9fff]  ', '包含中文')]:
            hr = ctk.CTkFrame(t2, fg_color='transparent')
            hr.pack(fill='x', padx=8)
            ctk.CTkLabel(hr, text=pat, font=('Consolas', 11), anchor='w',
                          width=180).pack(side='left')
            ctk.CTkLabel(hr, text=desc, text_color='#7a7a9a',
                          font=ft(10)).pack(side='left', padx=4)

        ctk.CTkLabel(t2, text='在下方输入正则表达式，一行一条',
                      text_color='#7a7a9a', font=ft(11)).pack(anchor='w', padx=8, pady=(6, 0))
        self.regex_list = TagList(t2)
        self.regex_list.pack(fill='both', expand=True, padx=6, pady=4)

        # ─ Tab 3: 时间范围 ─
        t3 = nb.add('时间范围')
        ctk.CTkSwitch(t3, text='保留模式（只保留该时间段的弹幕）',
                       variable=self.keep_range_var, switch_width=36).pack(fill='x', padx=6, pady=4)
        ctk.CTkFrame(t3, height=1, fg_color='#2a2a45').pack(fill='x', padx=6, pady=2)
        ctk.CTkLabel(t3, text='设置时间范围（秒）', text_color='#7a7a9a',
                      font=ft(11)).pack(anchor='w', padx=8, pady=(8, 4))
        rr = ctk.CTkFrame(t3, fg_color='transparent')
        rr.pack(padx=8, pady=(4, 12))
        ctk.CTkEntry(rr, textvariable=self.del_start, width=80).pack(side='left')
        ctk.CTkLabel(rr, text=' ~ ').pack(side='left')
        ctk.CTkEntry(rr, textvariable=self.del_end, width=80).pack(side='left')
        ctk.CTkLabel(rr, text=' 秒', text_color='#7a7a9a').pack(side='left', padx=4)

        # ─ Tab 4: 颜色 / 空白 ─
        t4 = nb.add('颜色 / 空白')
        ctk.CTkLabel(t4, text='按颜色删除弹幕', font=ft(12, 'bold')
                      ).pack(anchor='w', padx=8, pady=(8, 2))
        ctk.CTkLabel(t4, text='颜色值，逗号分隔（#FF0000,#00FF00）',
                      text_color='#7a7a9a', font=ft(11)).pack(anchor='w', padx=8)
        cr = ctk.CTkFrame(t4, fg_color='transparent')
        cr.pack(padx=8, pady=6, fill='x')
        ctk.CTkEntry(cr, textvariable=self.del_color).pack(fill='x')

        ctk.CTkSwitch(t4, text='保留模式（只保留这些颜色）',
                       variable=self.keep_color_var, switch_width=36).pack(fill='x', padx=6, pady=4)

        ctk.CTkFrame(t4, height=1, fg_color='#2a2a45').pack(fill='x', padx=8, pady=8)

        ctk.CTkLabel(t4, text='其他操作', font=ft(12, 'bold')).pack(anchor='w', padx=8)
        ef = ctk.CTkFrame(t4, fg_color='transparent')
        ef.pack(fill='x', padx=8, pady=(4, 8))
        ctk.CTkSwitch(ef, text='删除空白/纯空格弹幕', variable=self.del_empty_var,
                       switch_width=36).pack(anchor='w')

    def _build_bottom_panel(self):
        bottom = ctk.CTkFrame(self.root)
        bottom.pack(fill='both', expand=True, padx=14, pady=(0, 14))

        ab = ctk.CTkFrame(bottom)
        ab.pack(fill='x', pady=(0, 6))

        self.run_btn = ctk.CTkButton(ab, text='▶ 执行', fg_color=C_SUCCESS,
                                      hover_color='#00a86b', font=ft(13, 'bold'),
                                      text_color='white', command=self._execute)
        self.run_btn.pack(side='left', padx=10, pady=6)

        ctk.CTkButton(ab, text='清除', fg_color='#555555', hover_color='#777777',
                       command=self._clear_log).pack(side='left', padx=4, pady=6)

        self.log = ctk.CTkTextbox(bottom, font=('Consolas', 11), activate_scrollbars=True)
        self.log.pack(fill='both', expand=True)
        self.log.configure(state='disabled')
        self._log('就绪。')

    # ── 事件处理 ──

    def _browse_input(self):
        p = filedialog.askopenfilename(title='选择 B站 XML 弹幕文件',
                                        filetypes=[('XML', '*.xml'), ('All', '*.*')])
        if p:
            self._open_file(p)

    def _browse_output(self):
        p = filedialog.askdirectory(title='选择输出目录')
        if p:
            self.output_dir.set(p)
            self.out_label.configure(text=p)

    def _toggle_output_dir(self):
        if self.use_same_dir.get():
            self.out_label.configure(text='同目录', text_color='#7a7a9a')
        else:
            v = self.output_dir.get()
            self.out_label.configure(text=v or '未选择',
                                      text_color='white' if v else '#7a7a9a')

    def _show_stats(self):
        path = self.input_path.get()
        if not path:
            return
        try:
            _, danmaku = parse_xml(path)
            s = get_stats(danmaku)
            md = {1: '滚动', 4: '底部', 5: '顶部', 6: '逆向'}
            ms = ', '.join(f'{md.get(k, k)}:{v}' for k, v in s['modes'].items())
            messagebox.showinfo('统计',
                f'弹幕总数: {s["count"]}\n'
                f'时间范围: {s["time_min"]:.3f}s~{s["time_max"]:.3f}s\n'
                f'唯一内容: {s["unique_texts"]}\n'
                f'弹幕模式: {ms}')
        except Exception:
            pass

    # ── 预设 ──

    def _on_preset_click(self, event=None):
        pass

    def _refresh_preset_list(self):
        self.preset_listbox.configure(state='normal')
        self.preset_listbox.delete('1.0', 'end')
        for n in list_presets():
            self.preset_listbox.insert('end', n + '\n')
        self.preset_listbox.configure(state='disabled')

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
            messagebox.showerror('错误', f'预设 "{name}" 不存在')
        except Exception as e:
            messagebox.showerror('错误', str(e))

    def _del_preset(self):
        name = self.preset_name.get().strip()
        if not name:
            return
        if messagebox.askyesno('确认', f'删除预设 "{name}"？'):
            delete_preset(name)
            self._log('预设已删除: ' + name)
            self._refresh_preset_list()

    # ── 设置读写 ──

    def _gather_settings(self):
        return {
            'shift': self._fn(self.shift_var.get()),
            'shift_start': self._fn(self.shift_start.get()),
            'shift_end': self._fn(self.shift_end.get()),
            'delete_exact': self.exact_list.get_items(),
            'delete_regex': self.regex_list.get_items(),
            'delete_range_start': self._fn(self.del_start.get()),
            'delete_range_end': self._fn(self.del_end.get()),
            'delete_color': self.del_color.get().strip(),
            'delete_empty': self.del_empty_var.get(),
            'keep_mode': self.keep_mode.get(),
            'keep_range': self.keep_range_var.get(),
            'keep_color': self.keep_color_var.get(),
            'use_same_dir': self.use_same_dir.get(),
            'output_dir': self.output_dir.get(),
            'output_suffix': self.output_suffix.get(),
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
        try:
            return float(v.strip()) if v.strip() else None
        except (ValueError, AttributeError):
            return None

    # ── 执行 ──

    def _clear_log(self):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

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

        self._log(f'已读取: {os.path.basename(inp)} ({len(danmaku)} 条弹幕)')
        total_deleted = 0
        keep = self.keep_mode.get()

        exact_items = self.exact_list.get_items()
        regex_items = self.regex_list.get_items()

        # 保留模式
        if keep and exact_items:
            n = delete_exact(danmaku, set(exact_items), invert=True)
            total_deleted += n
            self._log(f'保留[精确]: 删除其他 {n} 条')

        if keep and regex_items:
            for p, n in delete_regex(danmaku, regex_items, invert=True):
                total_deleted += n
                self._log(f'保留[正则] /{p}/: 删除其他 {n} 条')

        ds, de = self._fn(self.del_start.get()), self._fn(self.del_end.get())
        if self.keep_range_var.get() and ds is not None and de is not None:
            n = delete_range(danmaku, (ds, de), invert=True)
            total_deleted += n
            self._log(f'保留[范围] {ds}s~{de}s: 删除范围外 {n} 条')

        color_str = self.del_color.get().strip()
        if self.keep_color_var.get() and color_str:
            cl = [c.strip() for c in color_str.split(',') if c.strip()]
            n = delete_by_color(danmaku, cl, invert=True)
            total_deleted += n
            self._log(f'保留[颜色]: 删除其他 {n} 条')

        # 删除模式
        if not keep:
            if self.del_empty_var.get():
                n = delete_empty(danmaku)
                total_deleted += n
                self._log(f'删除: 空白 {n} 条')
            if color_str and not self.keep_color_var.get():
                cl = [c.strip() for c in color_str.split(',') if c.strip()]
                n = delete_by_color(danmaku, cl)
                total_deleted += n
                self._log(f'删除: 颜色 {n} 条')
            if ds is not None and de is not None and not self.keep_range_var.get():
                n = delete_range(danmaku, (ds, de))
                total_deleted += n
                self._log(f'删除: 时间 {ds}s~{de}s {n} 条')
            if exact_items:
                n = delete_exact(danmaku, set(exact_items))
                total_deleted += n
                self._log(f'删除: 精确 {n} 条')
            if regex_items:
                for p, n in delete_regex(danmaku, regex_items):
                    total_deleted += n
                    self._log(f'删除: 正则 /{p}/ {n} 条')

        # 时间偏移
        sv = self._fn(self.shift_var.get())
        if sv is not None:
            ss, se = self._fn(self.shift_start.get()), self._fn(self.shift_end.get())
            tr = (ss, se) if (ss is not None and se is not None) else None
            n = shift_time(danmaku, sv, time_range=tr)
            ri = f' [{ss}s~{se}s]' if tr else ''
            self._log(f'偏移: {sv:+.3f} 秒{ri} ({n} 条)')

        # 输出
        out_dir = os.path.dirname(inp) if self.use_same_dir.get() else (
            self.output_dir.get().strip() or os.path.dirname(inp))
        base, ext = os.path.splitext(os.path.basename(inp))
        suf = self.output_suffix.get().strip()
        out_path = os.path.join(out_dir, base + suf + ext)
        write_xml(out_path, header, danmaku)
        self._log(f'完成: 输出到 {out_path}')
        self._log(f'完成: 剩余 {len(danmaku)} 条')
        if total_deleted:
            self._log(f'完成: 删除 {total_deleted} 条')


def gui_main(file_to_open=None):
    root = ctk.CTk()
    DanmakuEditorApp(root, file_to_open)
    root.mainloop()


# ═════════════════════════════════════════════════════════════
# 入口
# ═════════════════════════════════════════════════════════════

if __name__ == '__main__':
    if len(sys.argv) >= 2 and not sys.argv[1].startswith('-'):
        # 有非 flag 参数 → GUI 并打开文件
        gui_main(sys.argv[1])
    elif len(sys.argv) >= 2:
        # 有 flag 参数 → CLI
        cli_main()
    else:
        gui_main()
