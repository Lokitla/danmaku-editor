# DanmakuEditor v3.0 — B站 XML 弹幕编辑器

> 使用 mimo-v2-pro 模型辅助开发重构。感谢 mimo 百万亿 token 赠送计划提供的 token 支持。

一个离线、轻量的 B站 XML 弹幕编辑工具。支持时间偏移、精确/正则/颜色/时间范围删除、保留模式（反向删除），GUI 和 CLI 双模式。

## v3.0 更新（内部清理重构，功能与 CLI 输出保持不变）

本次只做单文件内部清理，不拆包、不加依赖，`python danmaku_editor.py` 照旧直接跑。

**修掉的缺陷**

| # | 缺陷 | 影响 |
|---|------|------|
| 1 | `p` 属性裸 `int()` | 字段异常时整份文件解析失败，报 `invalid literal for int()` |
| 2 | 入口判断用 `sys.argv[1].startswith('-')` | `xxx.xml --list` 被误判为「带文件启动 GUI」，弹出窗口 |
| 3 | `_fix_stdout()` 直接替换 `sys.stdout` | 旧 wrapper 析构关闭同一 buffer，重定向到文件时输出被静默丢弃 |
| 4 | import 期就建/销 Tk root 探测字体 | 纯 CLI 运行也会短暂创建窗口，stderr 留下 Tcl 噪音 |
| 5 | 非法正则/颜色直接抛原生异常 | 用户看到 `unterminated character set at position 0`，无中文提示 |
| 6 | CLI 与 GUI 各写一份删除/保留/偏移流程 | 两边顺序与判定不一致 |
| 7 | TagList 每次操作都从文本框重新 parse | 选中序号与渲染错位，四份增删渲染代码重复 |
| 8 | 死代码 `_on_preset_click`、只为判空的 `ops` 列表 | 冗余 |

**行为改进**

* **文件无损往返**：二进制读写 + 保留原文件换行风格（LF/CRLF/BOM 都原样还原）。旧版一次「无操作」保存就会把 LF 翻成 CRLF，并重排 `<i>` 内的元信息行；新版不再改动输入字节。
* **入口路由**：无参数 / 单个文件 → GUI；出现任何选项（`-h`、`--list`…）→ CLI；多个裸参数交给 CLI 报错，不再静默弹窗。
* **GUI 不留黑框**：Windows 下双击启动 GUI 时会隐藏控制台窗口；若同台还有其他进程（比如从 cmd / PowerShell 里跑命令行），则不隐藏。
* **不再刷 Tcl 报错**：customtkinter 的延迟回调在窗口销毁后触发会打 `invalid command name "..._windows_set_titlebar_icon"`，现通过替换 Tcl 后台错误钩子压掉，不影响 Python 侧异常上报。
* **customtkinter 可选**：没装 `customtkinter` 时 CLI 照常工作，GUI 模式给出中文提示而不是 traceback。

**对外契约**：CLI 输出文案是契约，改动前后请用 `regression/` 里的 17 场景对照脚本验证（见下）。

## v2.0 更新

- **UI 全面重写**：迁移至 CustomTkinter，内置暗色主题，圆角组件
- **字体优化**：自动检测系统最佳中文字体（Microsoft YaHei UI）
- **Bug 修复**：修复 CLI 入口逻辑、移除脆弱的 canvas item 操作
- **代码精简**：删除约 450 行冗余代码（手动 DPI、canvas 圆角、自定义输入框）

## 功能

| 功能 | CLI | GUI |
|------|:---:|:---:|
| 弹幕统计 | ✓ | ✓ |
| 时间偏移（正/负，可选范围） | ✓ | ✓ |
| 精确文本删除 | ✓ | ✓ |
| 正则表达式删除 | ✓ | ✓ |
| 时间范围删除 | ✓ | ✓ |
| 颜色删除（按 #FFFFFF 格式） | ✓ | ✓ |
| 空白弹幕删除 | ✓ | ✓ |
| 保留模式（反向删除，只保留匹配项） | ✓ | ✓ |
| 预设保存/加载 | - | ✓ |

## 快速开始

### 直接运行（需 Python 3.8+）

```bash
pip install customtkinter
python danmaku_editor.py                           # 启动 GUI
python danmaku_editor.py input.xml                 # GUI 并加载文件
python danmaku_editor.py input.xml --list          # CLI 统计
```

### 使用打包版

从 [Releases](https://github.com/Lokitla/danmaku-editor/releases) 下载 `DanmakuEditor.exe`，双击运行。

## CLI 用法

```bash
# 查看统计信息
python danmaku_editor.py input.xml --list

# 精确删除 + 时间偏移
python danmaku_editor.py input.xml -o output.xml -d "文本1" "文本2" -s 2.5

# 正则删除
python danmaku_editor.py input.xml -r "\\d+" "awsl|可爱"

# 保留模式（只保留指定内容，删除其他）
python danmaku_editor.py input.xml --keep "对的对的" --delete-empty

# 时间范围删除 + 偏移
python danmaku_editor.py input.xml --delete-range 0 60 -s -0.5

# 颜色删除 + 空白删除
python danmaku_editor.py input.xml --delete-color "#FF0000" --delete-empty
```

### 完整参数

```
positional arguments:
  input                  输入的 XML 文件路径
  -o, --output          输出路径（默认覆盖原文件）
  -s, --shift           时间偏移量（秒）
  --shift-range         只偏移指定时间范围
  -d, --delete          精确删除指定文本
  -r, --regex           正则删除
  --delete-range        删除时间范围
  --delete-empty        删除空白弹幕
  --delete-color        删除指定颜色（如 #FF0000）
  --keep                保留模式
  --keep-regex          保留模式
  --keep-range          保留模式
  --keep-color          保留模式
  --list                显示统计信息
```

## GUI 界面

CustomTkinter 暗色主题，左侧文件与预设管理，中间操作设置，底部日志与执行按钮。

- **删除模式 / 保留模式**：Tab 顶部有切换开关
- **预设**：可保存当前所有设置，一键加载
- **字体**：自动检测系统最佳中文字体

## 文件格式

兼容 **Bilibili 标准 XML 弹幕格式**：

```xml
<i>
  <chatserver>chat.bilibili.com</chatserver>
  <chatid>38363205439</chatid>
  <d p="时间,模式,字号,颜色,时间戳,弹幕池,用户Hash,弹幕ID">弹幕内容</d>
</i>
```

## 项目结构

```
danmaku_editor.py    # 主程序（CLI + GUI）
presets/             # 预设文件（自动创建）
regression/          # 重构期的行为对照脚本，不参与运行，删掉不影响主程序
```

## 回归对照

`regression/` 里放着一套「新版 CLI 与重构前等价」的对照脚本。它把新旧两版放进同一个进程，注入 `sys.argv` 直接调 `cli_main()`（**不会弹 GUI 窗口**），逐场景比对退出码 / stdout / stderr / 输出文件：

```powershell
cd regression
python regress.py v0_original.py ..\danmaku_editor.py fixture_baseline.xml compare_report.json
```

期望输出：

```
CASES=17 MISMATCHES=0
NL_PRESERVED_BY_V4_ONLY=2 ['H_noop', 'O_delete_missing']
```

`v0_original.py` 就是本仓库 `master` 在重构前那一版（commit `3a9dbc5` 的 `danmaku_editor.py`），逐字节同源。

## 技术栈

- Python 3.8+
- CustomTkinter 5.2+（可选：不装也能用 CLI）
- 标准库

## License

MIT
