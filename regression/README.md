# danmaku-editor 回归对照

这个目录不是测试框架，只是重构时用来证明「新版 CLI 与旧版行为一致」的一套对照脚本。
它不参与运行，删掉也不影响 `danmaku_editor.py`。

## 跑一次对照

```powershell
cd regression
python regress.py v0_original.py ..\danmaku_editor.py fixture_baseline.xml compare_report.json
```

期望输出：

```
CASES=17 MISMATCHES=0
NL_PRESERVED_BY_V4_ONLY=2 ['H_noop', 'O_delete_missing']
```

## 文件

| 文件 | 说明 |
| --- | --- |
| `regress.py` | 对照脚本。把两版放进同一个进程，注入 `sys.argv` 直接调 `cli_main()`，因此**不会弹 GUI 窗口**；逐场景捕获 stdout / stderr / 退出码 / 输出文件。 |
| `fixture_baseline.xml` | 1013 字节的 LF 测试样本：9 条弹幕，含空白弹幕、颜色弹幕、`<i>` 内 8 行元信息，文件末尾无多余内容。 |
| `v0_original.py` | 重构前的行为基线，即仓库 `master` 上 commit `3a9dbc5` 的 `danmaku_editor.py`（1011 行），只用于对照，不要拿去用。 |
| `compare_report.json` | 最近一次对照的完整结果（每场景两版的四字段）。 |

## 比对口径

* `code` / `out` / `err` 必须逐字节一致。
* `xml` 做两处**有意保留**的归一化后再比：
  * 旧版写文件时由 Python 把 `\n` 翻成 `os.linesep`，新版保持输入风格；
  * 旧版会把 `<i>` 与紧随其后的元信息行挤成同一行，新版逐字保留原文。
  这两条正是本次修掉的缺陷，`NL_PRESERVED_BY_V4_ONLY` 就是在数它们。

## 覆盖的 17 个场景

`--list`；偏移+精确删除+删空白；删除正则/颜色/时间范围；保留正则（带负偏移）；
保留精确匹配；非法正则；偏移+时间范围；无操作；保留时间范围；保留颜色；
多正则删除；保留+偏移；非法颜色；纯偏移；删除不存在的文本；超大偏移；负偏移+范围。

## 历史

* `v0_original.py` 就是本仓库重构前 `master` 上那一版 `danmaku_editor.py`（commit `3a9dbc5`），逐字节同源，
  所以这套对照是可复现的：`git show 3a9dbc5:danmaku_editor.py` 即可重新得到它。
* 重构过程中还有一个中间快照 `v3_before_v4.py`，在整理临时目录时被误删，没留下完整源码；
  它的两份基线（`v0_original.py` 与远端 master）在本套场景下输出一致。
