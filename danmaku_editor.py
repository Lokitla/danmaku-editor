#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B站 XML 弹幕编辑器 v4.0
功能: 时间偏移 + 删除/保留（精确/正则/时间范围/颜色/空白）
模式: CLI（带参数运行） / GUI（无参数运行）
UI: CustomTkinter 暗色主题

v4.0 相对 v3.0 的重构要点
1. 单一事实源: CLI 与 GUI 原来各写一份删除/保留流程，顺序与判定还不一致；
   现在统一走 build_specs() + run_ops()。操作顺序在任何过滤发生之前一次性
   决定，因此每个操作都作用于完整集合。
2. 解析健壮化: p 属性字段缺失/非数字不再抛裸 int() / IndexError。
3. 正则与颜色预校验: 非法输入在改动数据集之前就报错，不会改到一半才失败。
4. 字体探测惰性化: 只开 GUI 时才建 Tk root，CLI 运行不再有 Tcl 报错噪音。
5. TagList 以内部列表为准并统一渲染，修掉选中序号错位与重复代码。
6. 入口判断修正: 原来 `python danmaku_editor.py xx.xml --list` 会被当成
   “打开文件”而弹出 GUI，现在只要出现任何选项就走 CLI；没有参数时仍然
   启动 GUI（双击运行时 argv 为空，不再一闪而过）。
7. 文件无损往返: 按原样保留 BOM、换行风格（LF/CRLF）与 `<i>` 元信息排版，
   一次“无操作”保存后文件逐字节不变，不再被改成 CRLF。
8. stdout 包装不再丢输出: 旧实现丢掉旧 wrapper 的引用，重定向到文件时
   析构会关掉同一个 buffer，内容被静默丢弃；现在先取 buffer 再包装。
9. GUI 不留黑框: 双击会用 py.exe 新开一个控制台窗口，开界面时把它隐藏；
   只有「本程序自己的控制台」才隐藏，在 cmd/PowerShell 里跑命令行不会把
   用户自己的终端一起藏掉。命令行模式一律不动控制台。
10. 压掉 Tcl 回调报错: customtkinter 在 CTk() 里挂了 200ms/1000ms 的延迟
   回调，窗口提前销毁时 Tk 会往 stderr 打 invalid command name ... 噪音，
   现在在自家窗口上把 Tcl 的 bgerror 钩子换成空实现（不经过 sys.stderr，
   也不影响功能，只是不再刷屏）。

CLI 输出文案被脚本依赖，属于对外契约，改动前先跑
regression/regress.py 对照（见 regression/README.md）。
"""

import re
import os
import sys
import json
import codecs
import argparse
from collections import Counter
from dataclasses import dataclass
from typing import Callable, NamedTuple, Optional

try:
    import customtkinter as ctk
    from tkinter import filedialog, messagebox
    import tkinter.font as tkfont
    _HAS_CTK = True
except ImportError:  # 只影响 GUI；CLI 不需要 tkinter
    filedialog = messagebox = tkfont = None
    _HAS_CTK = False

    class _NoGuiWidget:
        """customtkinter 缺席时的占位基类: 只保证类定义能完成，实例化即报错。"""

        def __init__(self, *a, **kw):
            raise RuntimeError('未安装 customtkinter，无法使用图形界面；'
                               '命令行模式不受影响（pip install customtkinter）')

    class _NoCtk:
        CTk = CTkFrame = _NoGuiWidget

    ctk = _NoCtk()


# ── 环境适配: Windows 控制台（GUI 模式不留黑框）──

_CONSOLE_HWND = None  # 首次使用时的控制台窗口句柄；进程内唯一，缓存没问题


def _console_hwnd():
    """当前进程挂着哪个控制台窗口；没有则 None。"""
    global _CONSOLE_HWND
    if _CONSOLE_HWND is None and sys.platform == 'win32':
        try:
            import ctypes
            _CONSOLE_HWND = ctypes.windll.kernel32.GetConsoleWindow()
        except Exception:
            _CONSOLE_HWND = 0
    return _CONSOLE_HWND or None


def _console_owned_by_this_process() -> bool:
    """控制台是不是「本程序自己的」，判断依据是同一控制台上还挂着谁。

    * 双击 .py：控制台由 py.exe / python.exe 自己建，同台进程只有我们自己
      和启动器 → 可以隐藏。
    * 从 cmd / PowerShell / Windows Terminal 里运行：控制台上还挂着那个
      终端宿主（cmd.exe / powershell.exe / WindowsTerminal.exe / explorer.exe），
      隐藏它会把用户自己的终端一起藏掉 → 一律不动。

    注意 py.exe 那条路径：GetConsoleProcessList 对启动器可能报 pid=0
    （拿不到真实 pid），这种要当成「不认识的启动器」放行，否则双击场景永远
    隐藏不掉。
    """
    if sys.platform != 'win32' or _console_hwnd() is None:
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        self_pid = kernel32.GetCurrentProcessId()

        n = kernel32.GetConsoleProcessList(None, 0)
        buf = (ctypes.c_ulong * max(n, 1))()
        n = kernel32.GetConsoleProcessList(buf, max(n, 1))
        if n <= 0:
            return True                     # 数不出来就按独占处理

        launcher = ('py.exe', 'pyw.exe', 'python.exe', 'pythonw.exe')
        for pid in [int(p) for p in buf[:n]]:
            if pid == self_pid or pid == 0:
                continue                    # 我们自己 / 拿不到 pid 的启动器
            h = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            try:
                size = ctypes.c_ulong(260)
                name = ctypes.create_unicode_buffer(size.value)
                if not kernel32.QueryFullProcessImageNameW(h, 0, name, ctypes.byref(size)):
                    return False
                exe = name.value.rsplit('\\', 1)[-1].lower()
            finally:
                kernel32.CloseHandle(h)
            if exe not in launcher:
                return False                # 有别的程序共用一个控制台 → 可能是用户的终端
        return True
    except Exception:
        return False


def _show_console_window(cmd: int) -> bool:
    hwnd = _console_hwnd()
    if hwnd is None:
        return False
    try:
        import ctypes
        ctypes.windll.user32.ShowWindow(hwnd, cmd)
        return True
    except Exception:
        return False


def hide_console():
    """开 GUI 前把附带的黑框藏掉（只藏本进程独占的那个）。"""
    if _console_owned_by_this_process():
        _show_console_window(0)  # SW_HIDE


def show_console():
    """需要往控制台报错时把黑框找回来；找不到窗口就当无事发生。"""
    _show_console_window(5)  # SW_SHOW


def silence_tcl_bgerror(root):
    """把 Tcl 后台回调的报错吞掉。

    customtkinter 在 CTk() 里挂了几个延迟回调（200ms 设标题栏图标、1000ms 设
    缩放上下限），窗口若在它们触发前就被销毁，Tk 会往 stderr 打
    `invalid command name "..._windows_set_titlebar_icon"` 之类的报错。
    这类错误不经过 Python 的 sys.stderr，也不影响功能，但会污染控制台，
    所以在自家窗口上把 Tcl 标准的 bgerror 钩子换掉。
    """
    try:
        root.tk.eval('proc bgerror {msg} {}')
    except Exception:
        pass


# ── 环境适配: Windows CMD stdout 编码 ──

def _fix_stdout():
    """把 stdout/stderr 切到 UTF-8，避免中文在 Windows 控制台变乱码。

    先取得底层 buffer 再包装: 旧实现直接包 sys.stdout.buffer 又丢掉旧引用，
    重定向到文件时旧 wrapper 析构会关掉同一个 buffer，内容被静默丢弃。
    """
    if sys.platform != 'win32':
        return
    import io
    for name in ('stdout', 'stderr'):
        stream = getattr(sys, name, None)
        buf = getattr(stream, 'buffer', None)
        if buf is None:
            continue
        try:
            setattr(sys, name, io.TextIOWrapper(buf, encoding='utf-8',
                                                errors='replace'))
        except Exception:
            pass

_fix_stdout()

# ── CustomTkinter 全局设置 ──
if _HAS_CTK:
    ctk.set_appearance_mode('dark')
    ctk.set_default_color_theme('blue')

# ── 字体检测（惰性）──

_FONT_CANDIDATES = [
    'Microsoft YaHei UI', 'Microsoft YaHei', '微软雅黑',
    'PingFang SC', 'Noto Sans CJK SC', 'Segoe UI', 'Tahoma',
]
FONT_FALLBACK = 'Microsoft YaHei UI'
FONT: Optional[str] = None


def detect_font() -> str:
    """探测可用的中文界面字体，结果缓存。

    只在开 GUI 时才建 Tk root（旧实现 import 期就建一次再销毁，
    CLI 运行也会闪出窗口并在 stderr 留下 Tcl 报错噪音）。
    """
    global FONT
    if FONT is not None:
        return FONT
    root = None
    try:
        if _HAS_CTK:
            root = ctk.CTk()
            silence_tcl_bgerror(root)   # 临时窗口会被立刻销毁，压掉 Tcl 回调噪音
            root.withdraw()
            for name in _FONT_CANDIDATES:
                try:
                    if tkfont.Font(family=name, size=12).actual()['family'] == name:
                        FONT = name
                        break
                except Exception:
                    continue
    except Exception:
        pass
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
    if FONT is None:
        FONT = FONT_FALLBACK
    return FONT


def ft(size=12, weight='normal'):
    """生成字体元组"""
    family = detect_font()
    return (family, size, weight) if weight != 'normal' else (family, size)


# ═════════════════════════════════════════════════════════════
# 核心引擎
# ═════════════════════════════════════════════════════════════

DEFAULT_COLOR = 16777215
DEFAULT_FONT_SIZE = 25

# 弹幕模式 → 中文说明
MODE_NAMES = {
    1: '滚动', 2: '滚动', 3: '滚动', 4: '底部', 5: '顶部',
    6: '逆向', 7: '高级', 8: '代码', 9: 'BAS',
}


class DanmakuError(ValueError):
    """弹幕文件解析/写入相关错误"""


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

    def rebuild(self, new_time: Optional[float] = None) -> str:
        """渲染回 `<d p="...">文本</d>` 单行。

        new_time 为 None 时原样保留 p 里的时间字段（无损往返的前提）。
        """
        if new_time is None:
            p = self.p_raw
        else:
            parts = self.p_raw.split(',')
            parts[0] = f'{new_time:.3f}'
            p = ','.join(parts)
        return f'<d p="{p}">{self.text}</d>'


def _to_int(value, default: int) -> int:
    """宽松整数解析: 字段缺失或非数字时退回默认值，不抛异常。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _to_float(value, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_p(p_raw: str) -> dict:
    """解析 p 属性串。

    B站弹幕有 8 个字段，但导出工具或改版版本可能多/少字段；
    字段缺失或非数字一律退回默认值，避免裸 int() 让整份文件解析失败。
    """
    parts = p_raw.split(',')
    get = lambda i: parts[i] if i < len(parts) else ''  # noqa: E731
    return {
        'time': _to_float(get(0), 0.0),
        'mode': _to_int(get(1), 1),
        'font_size': _to_int(get(2), DEFAULT_FONT_SIZE),
        'color': _to_int(get(3), DEFAULT_COLOR),
        'timestamp': _to_int(get(4), 0),
        'pool': _to_int(get(5), 0),
        'user_hash': get(6),
        'dm_id': _to_int(get(7), 0),
    }


DANMAKU_RE = re.compile(r'<d\s+p="([^"]*)"\s*>([^<]*)</d>')


def _norm_nl(text: str, nl: str) -> str:
    """把行尾统一成 nl；先去掉所有 \\r 再补，保证不会混出 \\r\\r\\n。"""
    return nl.join(text.splitlines())


def _norm_nl_keep(text: str, nl: str) -> str:
    """同 _norm_nl，但保留末尾那个换行（splitlines 会把它吃掉）。"""
    tail = nl if text.endswith(('\n', '\r')) else ''
    return _norm_nl(text, nl) + tail


class XmlSource(NamedTuple):
    """写出弹幕文件时需要还原的原文碎片。

    prefix 是第一条弹幕之前的全部原文（截至它的缩进），suffix 是 `</i>` 起的
    全部原文；首尾的收尾空白由 _trim 去掉，中段由调用方拼回去。
    bom 记录原文件是否带 UTF-8 BOM，prefix_nl 记录 prefix 原本是否以换行结尾，
    两者都在写出时照原样还原。
    """
    prefix: str
    nl: str
    suffix: str
    bom: bool = False
    prefix_nl: bool = True


def _detect_nl(raw: str) -> str:
    """按最先出现的换行判断文件风格，默认 '\\n'。"""
    crlf, lf = raw.find('\r\n'), raw.find('\n')
    return '\r\n' if crlf != -1 and (lf == -1 or crlf <= lf) else '\n'


def _trim(text: str, nl: str, keep_nl: bool = False) -> str:
    """规范化文件首尾片段的收尾。

    prefix 不能以换行结尾（中段由调用方补），suffix 要保留文件自己的收尾换行；
    两者都先去掉 \\r，交给 _norm_nl 按文件风格统一补。
    """
    text = text.rstrip('\r')
    return text if keep_nl else text.rstrip('\n')


def _split_body(raw: str, m) -> tuple:
    """把原文切成 (第一条弹幕之前, `</i>` 起之后)。

    只有 `<i>` 没有 `<d ...>` 的文件整段正文都算 prefix，这样无弹幕的文件也能
    原样写回。找不到 `<i>` 时按空正文处理。
    """
    matches = list(DANMAKU_RE.finditer(raw))
    if matches:
        return raw[:matches[0].start()], raw[raw.rindex('</i>'):]
    if m:
        close = raw.rindex('</i>')
        return raw[:close], raw[close:]
    return '', raw


def parse_xml(filepath: str) -> tuple:
    """解析弹幕 XML，返回 (XmlSource, [Danmaku, ...])。

    旧版用文本模式读写：Python 会把写出的 '\\n' 一律翻成 os.linesep（LF 文件
    保存一次就变 CRLF），通用换行还会把弹幕正文里的 '\\r' 吃掉；它按行挑元信息
    再拼回去的做法又丢掉了 `<i>` 行的换行，把元信息全挤到一行。这里改成二进制
    读写，只重新生成弹幕行，其余原文逐字保留。
    """
    if not os.path.isfile(filepath):
        raise DanmakuError(f'文件不存在: {filepath}')
    try:
        with open(filepath, 'rb') as f:
            data = f.read()
    except OSError as e:
        raise DanmakuError(f'无法读取文件: {e}') from e
    bom = data.startswith(codecs.BOM_UTF8)
    try:
        raw = data.decode('utf-8-sig')  # utf-8-sig 顺带吃掉 BOM
    except UnicodeDecodeError as e:
        raise DanmakuError(f'文件不是 UTF-8 编码的 XML: {e}') from e

    m = re.search(r'<i>.*</i>', raw, re.DOTALL)
    if not m:
        raise DanmakuError('未找到 <i>...</i> 根标签，可能不是 B站弹幕 XML')

    danmaku: list = []
    for match in DANMAKU_RE.finditer(raw):
        p_raw, text = match.group(1), match.group(2)
        danmaku.append(Danmaku(
            p_raw=p_raw, text=text, raw=match.group(0), **parse_p(p_raw),
        ))

    prefix, suffix = _split_body(raw, m)
    if danmaku:
        # prefix 末尾是第一条弹幕的缩进（例如 '\n  '），由写出时统一补两格
        prefix = prefix.rstrip(' \t')
    nl = _detect_nl(raw)
    return XmlSource(_trim(prefix, nl), nl, _trim(suffix, nl, keep_nl=True),
                     bom, prefix.endswith(('\n', '\r'))), danmaku


def write_xml(filepath: str, source, danmaku: list):
    """写出弹幕 XML；输出目录不存在时自动创建。

    source 是 parse_xml 返回的 XmlSource；也兼容直接传 header 字符串（此时按
    '\\n' 拼一份 `<i>` 正文）。
    """
    if isinstance(source, XmlSource):
        prefix, nl, suffix, bom, prefix_nl = source
    elif isinstance(source, tuple):
        prefix, nl, suffix, bom, prefix_nl = str(source[0]), str(source[1]), '</i>\n', False, True
    else:
        prefix, nl, suffix, bom, prefix_nl = str(source), '\n', '</i>\n', False, True

    parent = os.path.dirname(os.path.abspath(filepath))
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as e:
        raise DanmakuError(f'无法创建输出目录 {parent}: {e}') from e

    head = _norm_nl(prefix, nl)
    tail = _norm_nl_keep(suffix, nl)
    lines = [d.rebuild(d.time) for d in danmaku]
    if lines:
        body = head + nl + nl.join('  ' + line for line in lines) + nl + tail
    elif prefix_nl:
        # prefix 原本自带换行才补：`<i></i>` 这种一行到底的文件要原样写回
        body = head + nl + tail
    else:
        body = head + tail
    try:
        # newline='' 关掉 Python 的换行翻译：换行符完全按原文件风格写
        encoding = 'utf-8-sig' if bom else 'utf-8'
        with open(filepath, 'w', encoding=encoding, newline='') as f:
            f.write(body)
    except OSError as e:
        raise DanmakuError(f'无法写入文件: {e}') from e


# ── 时间偏移 ──

def shift_time(danmaku: list, offset: float,
               time_range: Optional[tuple] = None) -> int:
    """按 offset 平移时间（不会小于 0）；返回受影响条数。"""
    count = 0
    for d in danmaku:
        if time_range and not (time_range[0] <= d.time <= time_range[1]):
            continue
        d.time = max(0.0, d.time + offset)
        count += 1
    return count


# ── 删除 / 保留 ──
#
# 统一约定: 原地过滤 danmaku，返回受影响条数（即被移除的条数）。
#   invert=False → 删除命中项
#   invert=True  → 保留命中项、删除其余（“保留模式”）
# 因此谓词 keep_hit(d) 的语义是「是否保留」，注意别把两个方向写反。

def _apply_filter(danmaku: list, keep_hit: Callable) -> int:
    """按谓词原地过滤，返回被移除的条数；keep_hit(d) 为真表示保留。"""
    before = len(danmaku)
    danmaku[:] = [d for d in danmaku if keep_hit(d)]
    return before - len(danmaku)


def delete_exact(danmaku: list, texts: set, invert: bool = False) -> int:
    if invert:
        return _apply_filter(danmaku, lambda d: d.text in texts)
    return _apply_filter(danmaku, lambda d: d.text not in texts)


def delete_range(danmaku: list, rng: tuple, invert: bool = False) -> int:
    start, end = rng
    if invert:
        return _apply_filter(danmaku, lambda d: start <= d.time <= end)
    return _apply_filter(danmaku, lambda d: not (start <= d.time <= end))


def delete_empty(danmaku: list, invert: bool = False) -> int:
    if invert:
        return _apply_filter(danmaku, lambda d: not d.text.strip())
    # 删除空白 = 保留非空白，方向别反
    return _apply_filter(danmaku, lambda d: bool(d.text.strip()))


def parse_colors(colors) -> set:
    """把 '#FF0000' / '16711680' / 16711680 统一成 int 集合。"""
    result = set()
    for c in colors:
        token = str(c).strip()
        if not token:
            continue
        try:
            result.add(int(token[1:], 16) if token.startswith('#') else int(token))
        except ValueError as e:
            # 沿用 v3 的原生错误文本（invalid literal for int() ...）不另作包装
            raise DanmakuError(str(e)) from e
    return result


def delete_by_color(danmaku: list, colors, invert: bool = False) -> int:
    color_set = parse_colors(colors)
    if invert:
        return _apply_filter(danmaku, lambda d: d.color in color_set)
    return _apply_filter(danmaku, lambda d: d.color not in color_set)


def prepare_regex(patterns) -> list:
    """预编译正则；任一非法立即抛错，保证不会改到一半才失败。"""
    prepared = []
    for pat_str in patterns:
        try:
            prepared.append((pat_str, re.compile(pat_str)))
        except re.error as e:
            # 措辞与 v3 保持一致（原生 re 错误文本），便于老用户/脚本对不上时排查
            raise DanmakuError(str(e)) from e
    return prepared


def delete_regex(danmaku: list, patterns, invert: bool = False) -> list:
    """逐个正则依次过滤；返回 [(pattern_str, 删除条数), ...]。"""
    results = []
    for pat_str, pat in prepare_regex(patterns):
        if invert:
            n = _apply_filter(danmaku, lambda d, p=pat: bool(p.search(d.text)))
        else:
            n = _apply_filter(danmaku, lambda d, p=pat: not p.search(d.text))
        results.append((pat_str, n))
    return results


def get_stats(danmaku: list) -> dict:
    if not danmaku:
        return {'count': 0, 'time_min': 0.0, 'time_max': 0.0,
                'unique_texts': 0, 'modes': {}}
    times = [d.time for d in danmaku]
    return {
        'count': len(danmaku),
        'time_min': min(times),
        'time_max': max(times),
        'unique_texts': len(set(d.text for d in danmaku)),
        'modes': dict(Counter(d.mode for d in danmaku).most_common()),
    }


def format_modes(modes: dict) -> str:
    """弹幕模式分布 → '滚动:6, 底部:2' 形式。"""
    return ', '.join(f'{MODE_NAMES.get(k, k)}:{v}' for k, v in modes.items())


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
# 操作管线 —— CLI 与 GUI 的唯一事实源
# ═════════════════════════════════════════════════════════════

@dataclass
class OpResult:
    """单个操作的执行结果。

    line/message 是同一个操作的两种说法: line 逐字沿用 v3 CLI 的输出格式
    （回归基线要求），message 沿用 v3 界面的日志格式。count 是受影响条数，
    对保留类操作而言就是「删除其他」的条数。
    """
    line: str
    message: str
    count: int


def _count_line(text: str, n: int) -> str:
    """v3 的计数文案有几种收尾: '删除其他 N 条'、'…, 影响 N 条'、': N 条'。"""
    if text.endswith(('删除其他', '删除范围外', '影响')):
        return f'{text} {n} 条'
    return f'{text}: {n} 条'


@dataclass
class OpSpec:
    """一个待执行操作: 两种展示文案 + 执行函数。"""
    text: str          # v3 CLI 的前缀，如 '[删除] 空白弹幕'
    kind: str          # v3 界面的前缀，如 '删除: 空白'
    run: Callable[[list], int]
    suffix: str = ''   # 计数文案前的补充，如 ', 影响'
    raw: bool = False  # 前缀已含 '(N 个)'，不再给它补括号
    is_shift: bool = False  # 偏移不是删除，不计入「总计删除」

    def execute(self, danmaku: list) -> OpResult:
        """带上条数：CLI 行尾按 v3 补 '(N 个)'，界面行不补。"""
        n = self.run(danmaku)
        text, kind = f'{self.text}{self.suffix}', f'{self.kind}{self.suffix}'
        if self.raw:
            # v3 把 '(N 个)' 放在条数之前，两种收尾:
            #   '[删除] 精确匹配 (2 个): 2 条'
            #   '[保留] 精确匹配 (2 个): 删除其他 7 条'
            if not text.endswith(('删除其他', '删除范围外')):
                text = text.rstrip(':')
        return OpResult(_count_line(text, n), _count_line(kind, n), n)


def _spec(text: str, kind: str, run: Callable, suffix: str = '',
          raw: bool = False, is_shift: bool = False) -> OpSpec:
    return OpSpec(text, kind, run, suffix, raw, is_shift)


def _regex_spec(pattern: str, invert: bool) -> OpSpec:
    """构造一个正则操作；正则的合法性在构造期就已校验。"""
    pat = prepare_regex([pattern])[0][1]
    # v3 的措辞: CLI「[保留] 正则 "/p/": 删除其他 N 条」, 界面「保留[正则] /p/: 删除其他 N 条」
    text = (f'[保留] 正则 "/{pattern}/": 删除其他' if invert
            else f'[删除] 正则 "/{pattern}/"')
    kind = (f'保留[正则] /{pattern}/: 删除其他' if invert
            else f'删除: 正则 /{pattern}/')

    def run(dm, p=pat, inv=invert):
        if inv:
            return _apply_filter(dm, lambda d: bool(p.search(d.text)))
        return _apply_filter(dm, lambda d: not p.search(d.text))

    return _spec(text, kind, run)


def build_specs(*, shift=None, shift_range=None,
                delete_texts=None, delete_regex_patterns=None,
                delete_range_pair=None, delete_blanks=False, delete_colors=None,
                keep_texts=None, keep_regex_patterns=None,
                keep_range_pair=None, keep_colors=None) -> list:
    """把 CLI/界面上收集到的原始选项整理成有序操作列表。

    列表顺序**就是执行顺序**，在动手之前一次性定好，因此每个操作都作用于还
    没被前一个操作删减过的集合；CLI 与 GUI 也共享完全相同的语义。

    顺序沿用 v3 的实际执行顺序（保留操作 → 各删除操作 → 时间偏移），
    文案也逐字沿用 v3，使 CLI 输出与旧版完全一致。
    """
    specs: list = []

    # ── 保留模式（反向删除）: count 即「删除其他」的条数 ──
    if keep_texts:
        n = len(keep_texts)
        specs.append(_spec(
            f'[保留] 精确匹配 ({n} 个): 删除其他',
            '保留[精确]: 删除其他',
            lambda dm, t=set(keep_texts): delete_exact(dm, t, invert=True),
            raw=True))

    for pattern in keep_regex_patterns or []:
        specs.append(_regex_spec(pattern, invert=True))

    if keep_range_pair is not None:
        lo, hi = keep_range_pair
        specs.append(_spec(
            f'[保留] 时间 {lo}s~{hi}s: 删除范围外',
            f'保留[范围] {lo}s~{hi}s: 删除范围外',
            lambda dm, r=(lo, hi): delete_range(dm, r, invert=True)))

    if keep_colors:
        color_set = parse_colors(keep_colors)
        specs.append(_spec(
            '[保留] 颜色: 删除其他', '保留[颜色]: 删除其他',
            lambda dm, c=color_set: delete_by_color(dm, c, invert=True)))

    # ── 删除 ──
    if delete_blanks:
        # 形参特意不叫 delete_empty: 那样会在本函数里遮蔽同名全局函数，
        # 闭包中调用它就变成调用一个 bool，报 'bool' object is not callable
        specs.append(_spec('[删除] 空白弹幕', '删除: 空白',
                           lambda dm: delete_empty(dm)))

    if delete_colors:
        # 颜色在构造期校验一次，非法值不会等到执行到一半才炸
        color_set = parse_colors(delete_colors)
        specs.append(_spec('[删除] 颜色', '删除: 颜色',
                           lambda dm, c=color_set: delete_by_color(dm, c)))

    if delete_range_pair is not None:
        lo, hi = delete_range_pair
        specs.append(_spec(
            f'[删除] 时间 {lo}s~{hi}s', f'删除: 时间 {lo}s~{hi}s',
            lambda dm, r=(lo, hi): delete_range(dm, r)))

    if delete_texts:
        specs.append(_spec(
            f'[删除] 精确匹配 ({len(delete_texts)} 个)', '删除: 精确',
            lambda dm, t=set(delete_texts): delete_exact(dm, t), raw=True))

    for pattern in delete_regex_patterns or []:
        specs.append(_regex_spec(pattern, invert=False))

    # ── 时间偏移（v3 里也是最后执行）──
    if shift is not None:
        rng = tuple(shift_range) if shift_range else None
        rng_info = f' [{rng[0]}s~{rng[1]}s]' if rng else ''
        specs.append(_spec(
            f'[偏移] {shift:+.3f} 秒{rng_info}',
            f'偏移: {shift:+.3f} 秒{rng_info}',
            lambda dm, r=rng: shift_time(dm, shift, time_range=r),
            suffix=', 影响', is_shift=True))

    return specs


def run_ops(danmaku: list, specs: list, on_result: Optional[Callable] = None) -> list:
    """依次执行操作，返回 [OpResult, ...]。

    有 on_result 回调时逐个上报（GUI 用来实时记日志）。
    """
    results = []
    for spec in specs:
        try:
            result = spec.execute(danmaku)
        except DanmakuError:
            raise
        except Exception as e:  # 给失败的操作带上上下文
            raise DanmakuError(f'{spec.kind} 执行失败: {e}') from e
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results


# ═════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════

def _range_pair(raw) -> Optional[tuple]:
    """把 [start, end] 归一成 (start, end)，两端都给了才算数。"""
    if not raw:
        return None
    lo, hi = raw[0], raw[1]
    return (lo, hi) if lo is not None and hi is not None else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='B站 XML 弹幕编辑器 v4',
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

    parser.add_argument('--list', action='store_true',
                        help='显示统计信息（不修改文件）')
    return parser


def specs_from_args(args) -> list:
    """CLI 选项 → 操作列表（顺序即执行顺序）。"""
    return build_specs(
        shift=args.shift,
        shift_range=_range_pair(args.shift_range),
        delete_texts=args.delete,
        delete_regex_patterns=args.regex,
        delete_range_pair=_range_pair(args.delete_range),
        delete_blanks=args.delete_empty,
        delete_colors=args.delete_color,
        keep_texts=args.keep,
        keep_regex_patterns=args.keep_regex,
        keep_range_pair=_range_pair(args.keep_range),
        keep_colors=args.keep_color,
    )


def print_stats(filepath: str, danmaku: list):
    s = get_stats(danmaku)
    print(f'文件: {filepath}')
    print(f'弹幕总数: {s["count"]}')
    if s['count']:
        print(f'时间范围: {s["time_min"]:.3f}s ~ {s["time_max"]:.3f}s')
        print(f'唯一内容: {s["unique_texts"]}')
        print(f'弹幕模式分布: {s["modes"]}')


def run_cli(args) -> int:
    """执行一次 CLI 调用；返回进程退出码。"""
    if args.list:
        _, danmaku = parse_xml(args.input)
        print_stats(args.input, danmaku)
        return 0

    # 无操作也照跑: 等价于「原样重写一份」，v3 就是这么做的（无损往返靠它验证）
    specs = specs_from_args(args)

    source, danmaku = parse_xml(args.input)

    # 正则/颜色在 build_specs 阶段已校验，非法输入不会动到数据
    results = run_ops(danmaku, specs)
    # 只统计删除/保留类操作: v3 的 total 没有累加偏移，所以单独排除它
    total = sum(r.count for spec, r in zip(specs, results) if not spec.is_shift)

    for r in results:
        print(r.line)

    output = args.output or args.input
    write_xml(output, source, danmaku)
    print(f'[完成] 输出: {output}')
    print(f'[完成] 剩余: {len(danmaku)} 条')
    if total:
        print(f'[完成] 总计删除: {total} 条')
    return 0


def cli_main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run_cli(args)
    except DanmakuError as e:
        print(f'[错误] {e}', file=sys.stderr)
        return 1
    except Exception as e:
        print(f'[错误] {e}', file=sys.stderr)
        return 1


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
C_MUTED     = '#7a7a9a'
C_BORDER    = '#2a2a45'
C_MONO      = 'Consolas'


class TagList(ctk.CTkFrame):
    """可增删的标签列表。

    v4: 以 self._items 为唯一数据源，所有变更后走一次 _render()。
    旧实现每次操作都重新解析文本框内容（get_items 里 strip/lstrip 再比较），
    选中序号在渲染与解析之间会错位，还重复了四份增删渲染代码。
    """

    def __init__(self, master, **kw):
        super().__init__(master, fg_color='transparent', **kw)
        self._items: list = []
        self._selected: Optional[int] = None

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
        self._render()

    # ── 渲染 ──

    def _render(self):
        self._box.configure(state='normal')
        self._box.delete('1.0', 'end')
        for i, item in enumerate(self._items):
            prefix = '>> ' if i == self._selected else '   '
            self._box.insert('end', f'{prefix}{item}\n')
        self._box.configure(state='disabled')

    # ── 事件 ──

    def _on_click(self, event):
        idx = self._box.index(f'@{event.x},{event.y}').split('.')[0]
        try:
            self._select(int(idx) - 1)
        except (ValueError, IndexError):
            pass

    def _select(self, idx):
        self._selected = idx if 0 <= idx < len(self._items) else None
        self._render()

    def _add(self):
        v = self.entry.get().strip()
        if not v:
            return
        self._items.append(v)
        self._selected = None
        self.entry.delete(0, 'end')
        self._render()

    def _remove(self):
        if self._selected is None:
            return
        del self._items[self._selected]
        self._selected = None
        self._render()

    # ── 数据访问 ──

    def get_items(self) -> list:
        return list(self._items)

    def set_items(self, items):
        self._items = [str(i) for i in (items or [])]
        self._selected = None
        self._render()


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

    def _card(self, parent, **pack_kw):
        """统一卡片样式，省掉各处重复的边框配色参数。"""
        card = ctk.CTkFrame(parent, corner_radius=10, border_width=1,
                            border_color=C_BORDER)
        card.pack(**pack_kw)
        return card

    def _card_title(self, card, text):
        ctk.CTkLabel(card, text=text, font=ft(14, 'bold'),
                     text_color=C_ACCENT).pack(anchor='w', padx=12, pady=(10, 2))

    def _build_file_panel(self, parent):
        card = self._card(parent, fill='x', pady=(0, 8))
        self._card_title(card, '文件')

        ctk.CTkLabel(card, text='选择 B站 XML 弹幕文件',
                     font=ft(12, 'bold')).pack(anchor='w', padx=12, pady=(4, 2))

        ir = ctk.CTkFrame(card, fg_color='transparent')
        ir.pack(fill='x', padx=12, pady=(0, 6))
        ctk.CTkButton(ir, text='...', width=36, command=self._browse_input
                      ).pack(side='left')
        self.in_label = ctk.CTkLabel(ir, text='未选择', text_color=C_MUTED,
                                     anchor='w')
        self.in_label.pack(side='left', fill='x', expand=True, padx=5)
        ctk.CTkButton(ir, text='i', width=28, fg_color=C_PRIMARY,
                      command=self._show_stats).pack(side='left', padx=(5, 0))

        ctk.CTkFrame(card, height=1, fg_color=C_BORDER).pack(fill='x', padx=12, pady=4)

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
        self.out_label = ctk.CTkLabel(or_, text='同目录', text_color=C_MUTED,
                                      anchor='w')
        self.out_label.pack(side='left', fill='x', expand=True, padx=5)

        sr = ctk.CTkFrame(card, fg_color='transparent')
        sr.pack(fill='x', padx=12, pady=(0, 10))
        ctk.CTkLabel(sr, text='后缀').pack(side='left')
        ctk.CTkEntry(sr, textvariable=self.output_suffix, width=100
                     ).pack(side='left', padx=5)

    def _build_preset_panel(self, parent):
        card = self._card(parent, fill='x')
        self._card_title(card, '预设')

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
        self.preset_listbox.bind('<Button-1>', self._on_preset_click)

    def _build_shift_panel(self, parent):
        card = self._card(parent, fill='x', pady=(0, 8))
        self._card_title(card, '时间偏移')

        ctk.CTkLabel(card, text='正数 = 延后，负数 = 提前',
                     text_color=C_MUTED, font=ft(11)).pack(anchor='w', padx=12, pady=(0, 6))

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
        ctk.CTkLabel(sr, text='秒', text_color=C_MUTED).pack(side='left', padx=4)

    def _build_delete_panel(self, parent):
        card = self._card(parent, fill='both', expand=True)
        self._card_title(card, '删除设置')

        nb = ctk.CTkTabview(card)
        nb.pack(fill='both', expand=True, padx=8, pady=(4, 8))

        # ─ Tab 1: 精确匹配 ─
        t1 = nb.add('精确匹配')
        ctk.CTkSwitch(t1, text='保留模式（只保留列表中的弹幕，删除其他）',
                      variable=self.keep_mode, switch_width=36).pack(fill='x', padx=6, pady=4)
        ctk.CTkFrame(t1, height=1, fg_color=C_BORDER).pack(fill='x', padx=6, pady=2)
        ctk.CTkLabel(t1, text='输入要精确匹配的弹幕文本，一行一条',
                     text_color=C_MUTED, font=ft(11)).pack(anchor='w', padx=8, pady=(4, 0))
        self.exact_list = TagList(t1)
        self.exact_list.pack(fill='both', expand=True, padx=6, pady=4)

        # ─ Tab 2: 正则匹配 ─
        t2 = nb.add('正则匹配')
        ctk.CTkSwitch(t2, text='保留模式（只保留匹配正则的弹幕，删除其他）',
                      variable=self.keep_mode, switch_width=36).pack(fill='x', padx=6, pady=4)
        ctk.CTkFrame(t2, height=1, fg_color=C_BORDER).pack(fill='x', padx=6, pady=2)

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
            ctk.CTkLabel(hr, text=pat, font=(C_MONO, 11), anchor='w',
                         width=180).pack(side='left')
            ctk.CTkLabel(hr, text=desc, text_color=C_MUTED,
                         font=ft(10)).pack(side='left', padx=4)

        ctk.CTkLabel(t2, text='在下方输入正则表达式，一行一条',
                     text_color=C_MUTED, font=ft(11)).pack(anchor='w', padx=8, pady=(6, 0))
        self.regex_list = TagList(t2)
        self.regex_list.pack(fill='both', expand=True, padx=6, pady=4)

        # ─ Tab 3: 时间范围 ─
        t3 = nb.add('时间范围')
        ctk.CTkSwitch(t3, text='保留模式（只保留该时间段的弹幕）',
                      variable=self.keep_range_var, switch_width=36).pack(fill='x', padx=6, pady=4)
        ctk.CTkFrame(t3, height=1, fg_color=C_BORDER).pack(fill='x', padx=6, pady=2)
        ctk.CTkLabel(t3, text='设置时间范围（秒）', text_color=C_MUTED,
                     font=ft(11)).pack(anchor='w', padx=8, pady=(8, 4))
        rr = ctk.CTkFrame(t3, fg_color='transparent')
        rr.pack(padx=8, pady=(4, 12))
        ctk.CTkEntry(rr, textvariable=self.del_start, width=80).pack(side='left')
        ctk.CTkLabel(rr, text=' ~ ').pack(side='left')
        ctk.CTkEntry(rr, textvariable=self.del_end, width=80).pack(side='left')
        ctk.CTkLabel(rr, text=' 秒', text_color=C_MUTED).pack(side='left', padx=4)

        # ─ Tab 4: 颜色 / 空白 ─
        t4 = nb.add('颜色 / 空白')
        ctk.CTkLabel(t4, text='按颜色删除弹幕', font=ft(12, 'bold')
                     ).pack(anchor='w', padx=8, pady=(8, 2))
        ctk.CTkLabel(t4, text='颜色值，逗号分隔（#FF0000,#00FF00）',
                     text_color=C_MUTED, font=ft(11)).pack(anchor='w', padx=8)
        cr = ctk.CTkFrame(t4, fg_color='transparent')
        cr.pack(padx=8, pady=6, fill='x')
        ctk.CTkEntry(cr, textvariable=self.del_color).pack(fill='x')

        ctk.CTkSwitch(t4, text='保留模式（只保留这些颜色）',
                      variable=self.keep_color_var, switch_width=36).pack(fill='x', padx=6, pady=4)

        ctk.CTkFrame(t4, height=1, fg_color=C_BORDER).pack(fill='x', padx=8, pady=8)

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

        self.log = ctk.CTkTextbox(bottom, font=(C_MONO, 11), activate_scrollbars=True)
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
            self.out_label.configure(text='同目录', text_color=C_MUTED)
        else:
            v = self.output_dir.get()
            self.out_label.configure(text=v or '未选择',
                                     text_color='white' if v else C_MUTED)

    def _show_stats(self):
        path = self.input_path.get()
        if not path:
            return
        try:
            _, danmaku = parse_xml(path)
            s = get_stats(danmaku)
            messagebox.showinfo('统计',
                                f'弹幕总数: {s["count"]}\n'
                                f'时间范围: {s["time_min"]:.3f}s~{s["time_max"]:.3f}s\n'
                                f'唯一内容: {s["unique_texts"]}\n'
                                f'弹幕模式: {format_modes(s["modes"])}')
        except DanmakuError as e:
            messagebox.showerror('错误', str(e))

    # ── 预设 ──

    def _on_preset_click(self, event=None):
        """点击预设列表时把该行名称填进输入框。"""
        try:
            idx = int(self.preset_listbox.index(f'@{event.x},{event.y}').split('.')[0]) - 1
        except (ValueError, AttributeError):
            return
        names = list_presets()
        if 0 <= idx < len(names):
            self.preset_name.delete(0, 'end')
            self.preset_name.insert(0, names[idx])

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

    def _color_list(self) -> list:
        return [c.strip() for c in self.del_color.get().split(',') if c.strip()]

    def _range_pair_or_none(self):
        lo, hi = self._fn(self.del_start.get()), self._fn(self.del_end.get())
        return (lo, hi) if lo is not None and hi is not None else None

    def _gui_specs(self) -> list:
        """界面选项 → 与 CLI 完全同一套操作语义。"""
        keep = self.keep_mode.get()
        exact = self.exact_list.get_items()
        regexes = self.regex_list.get_items()
        colors = self._color_list()
        rng = self._range_pair_or_none()

        shift = self._fn(self.shift_var.get())
        shift_range = None
        if shift is not None:
            ss, se = self._fn(self.shift_start.get()), self._fn(self.shift_end.get())
            if ss is not None and se is not None:
                shift_range = (ss, se)

        return build_specs(
            shift=shift,
            shift_range=shift_range,
            # 保留模式
            keep_texts=exact if keep else None,
            keep_regex_patterns=regexes if keep else None,
            keep_range_pair=rng if self.keep_range_var.get() else None,
            keep_colors=colors if (self.keep_color_var.get() and colors) else None,
            # 删除只在不处于「保留模式」时生效
            delete_blanks=self.del_empty_var.get() and not keep,
            delete_colors=colors if (colors and not keep and not self.keep_color_var.get()) else None,
            delete_range_pair=None if (keep or self.keep_range_var.get()) else rng,
            delete_texts=exact if not keep else None,
            delete_regex_patterns=regexes if not keep else None,
        )

    def _output_path(self, inp: str) -> str:
        out_dir = os.path.dirname(inp) if self.use_same_dir.get() else (
            self.output_dir.get().strip() or os.path.dirname(inp))
        base, ext = os.path.splitext(os.path.basename(inp))
        return os.path.join(out_dir, base + self.output_suffix.get().strip() + ext)

    def _execute(self):
        inp = self.input_path.get()
        if not inp or not os.path.isfile(inp):
            messagebox.showwarning('提示', '请先选择 XML 文件')
            return

        specs = self._gui_specs()
        try:
            source, danmaku = parse_xml(inp)
        except DanmakuError as e:
            messagebox.showerror('错误', f'解析失败: {e}')
            return

        self._log(f'已读取: {os.path.basename(inp)} ({len(danmaku)} 条弹幕)')

        try:
            results = run_ops(danmaku, specs)
        except DanmakuError as e:
            messagebox.showerror('错误', str(e))
            self._log(f'错误: {e}')
            return

        for r in results:
            self._log(r.message)
        # 与 CLI 一致: 偏移不是删除，不计入总数
        total = sum(r.count for spec, r in zip(specs, results) if not spec.is_shift)

        out_path = self._output_path(inp)
        try:
            write_xml(out_path, source, danmaku)
        except DanmakuError as e:
            messagebox.showerror('错误', str(e))
            self._log(f'错误: {e}')
            return

        self._log(f'完成: 输出到 {out_path}')
        self._log(f'完成: 剩余 {len(danmaku)} 条')
        if total:
            self._log(f'完成: 删除 {total} 条')


def gui_main(file_to_open=None) -> int:
    hide_console()          # 双击 .py 时附带的黑框在开界面后就藏掉
    if not _HAS_CTK:
        show_console()      # 报错要看得见
        print('错误: 未安装 customtkinter，无法启动图形界面（pip install customtkinter）',
              file=sys.stderr)
        return 1
    root = ctk.CTk()
    silence_tcl_bgerror(root)
    DanmakuEditorApp(root, file_to_open)
    root.mainloop()
    return 0


# ═════════════════════════════════════════════════════════════
# 入口
# ═════════════════════════════════════════════════════════════

def main(argv=None) -> int:
    """分流 CLI / GUI。

    规则（与 v3 的“无参数=开界面”一致，双击运行时 argv 为空）:
    * 没有参数          → GUI（双击或直接 `python danmaku_editor.py`）
    * 参数里出现选项    → CLI（v3 的判断是 `sys.argv[1].startswith('-')`，
                          于是 `python danmaku_editor.py xx.xml --list`
                          会被当成“带文件启动 GUI”，这里修掉）
    * 只有 1 个裸文件名 → GUI 并打开该文件（拖到 .py 上 / 命令行直接给路径）
    * 多个裸参数        → CLI 报错，不静默开窗
    """
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv:
        return gui_main()
    if any(a.startswith('-') for a in argv):
        return cli_main(argv)
    if len(argv) == 1:
        return gui_main(argv[0])
    return cli_main(argv)


if __name__ == '__main__':
    sys.exit(main())
