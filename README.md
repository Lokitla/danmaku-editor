# DanmakuEditor v3.0 — B站 XML 弹幕编辑器

一个离线、轻量的 B站 XML 弹幕编辑工具。支持时间偏移、精确/正则/颜色/时间范围删除、保留模式（反向删除），GUI 和 CLI 双模式。

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

CustomTkinter 是**可选**依赖：不装也能用 CLI，GUI 模式会给出中文提示。

### 使用打包版（免装 Python）

从 [Releases](https://github.com/Lokitla/danmaku-editor/releases) 下载最新的 `DanmakuEditor.exe`（v3.0 起每个版本都附打包版）：

* **双击**即可打开图形界面，不需要装 Python 或任何依赖；
* 命令行同样可用，把 `python danmaku_editor.py` 换成 `DanmakuEditor.exe` 即可，例如 `DanmakuEditor.exe input.xml --list`；
* 自己打包：`pwsh -File build_exe.ps1`（需要 `pip install pyinstaller`），产物在 `dist\DanmakuEditor.exe`。

各版本的改动说明写在对应 Release 里。

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

保存时保留原文件的换行风格（LF / CRLF）与 BOM 状态，不做无谓改写。

## 项目结构

```
danmaku_editor.py    # 主程序（CLI + GUI）
build_exe.ps1        # 打包脚本（PyInstaller 单文件 exe）
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

## 版本历史

各版本改了什么，看 [Releases](https://github.com/Lokitla/danmaku-editor/releases)。

## 技术栈

- Python 3.8+
- CustomTkinter 5.2+（可选：不装也能用 CLI）
- 标准库

## License

MIT
