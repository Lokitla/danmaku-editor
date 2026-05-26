# DanmakuEditor v2.0 — B站 XML 弹幕编辑器

> 由 AI（Claude）辅助开发重构。

一个离线、轻量的 B站 XML 弹幕编辑工具。支持时间偏移、精确/正则/颜色/时间范围删除、保留模式（反向删除），GUI 和 CLI 双模式。

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
```

## 技术栈

- Python 3.8+
- CustomTkinter 5.2+（现代 UI 组件）
- 标准库

## License

MIT
