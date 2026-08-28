# florr-auto-afk自动启动(免手点run按钮) — design

## Problem

[afk_watch.py](../../../afk_watch.py)的`ensure_florr_auto_afk_running()`打开`segment.exe`之后只能打印一句"请在它的界面里点run按钮"——用户必须手动点一次GUI,AFK检测才真的开始。这跟这个项目"双击exe就能跑"的打包目标(见[2026-08-26-chrome-bootstrap-design.md](2026-08-26-chrome-bootstrap-design.md))脱节,而且忘了点是**静默失效**:寻路照跑,AFK弹窗没人处理,账号被踢。

顺带发现`_download_and_extract()`现在是坏的(从来没在Windows上真跑过):官方v1.1.1 release zip **没有顶层目录**(它的workflow用`Compress-Archive -Path ./dist/segment/*`,条目直接是`segment.exe`/`models\afk-det.pt`/`gui\bg.png`),而代码是`zf.extractall(_INSTALL_ROOT)`——4500个文件会铺在`main.py`旁边,`_EXE_PATH`(`_INSTALL_ROOT/florr-auto-afk-v1.1.1-auto/segment.exe`)永远不存在 → 下完260MB必然启动失败。

## Goal

`main.py`启动后AFK处理全自动:没装就问要不要下 → 装了就静默开起来(窗口自己最小化,不挡游戏)→ 确认它真的进入了检测状态。全程零点击。

## 调研结论:为什么必须改florr-auto-afk本身

florr-auto-afk(`Shiny-Ladybug/florr-auto-afk`,GPL-3.0,源码在`~/florr-auto-afk`)**没有任何"启动即开始检测"的入口**:

- `Settings.md`的配置全表里没有autoStart之类的键。`runs.runningCountDown`是"跑X分钟后自动**关闭**",方向相反
- 没有命令行参数解析(全repo只有`infer_*.py`那几个离线推理脚本用argparse,`segment.py`没有)
- v1.3.2那个`extensions` WebSocket server只是浏览器扩展的遥测桥(florrHealth/florrPosition等消息),没有"开始检测"这类命令;v1.1.1连这个server都没有
- run按钮的实际动作就是`toggle_segment_process()`(v1.1.1无参数版,`segment.py:205`)

所以外部只剩"模拟点击GUI"一条路,而它启动时会`ctypes.windll.shcore.SetProcessDpiAwareness(1)` + `root.tk.call('tk','scaling',ScaleFactor/75)`——按钮坐标随系统DPI缩放漂移。这个项目在`switch_server()`上已经栽过一次像素点击(见`switch-server-cdp-not-clicks`那条memory:点游戏内下拉框反复没成,最后换CDP)。不重复踩。

GPL-3.0允许fork后修改,fork本身就满足"公开改动源码"的义务,不需要上游提交权限。

## Non-goals

- **不跟上游更新** —— fork钉在`v1.1.1`(用户实机已经在跑的版本),不做rebase/同步。补丁只有3行,哪天真要升级再贴一次
- **不升到v1.3.2** —— 那版多了FastAPI扩展server、`rejoin`、模型自动更新,识别行为要重新验证,不在这次范围
- **不做GUI自动化**(模拟点击/键盘Tab导航)—— 见上面调研结论
- **不把YOLO/torch搬进本仓库** —— 沿用[2026-08-11-afk-check-coexistence-design.md](2026-08-11-afk-check-coexistence-design.md)定好的边界
- **不管"用户自己另一份florr-auto-afk在跑"** —— `_is_florr_auto_afk_running()`探到就整段跳过(已实现),不去猜那份的config/log在哪
- **不做版本更新检测** —— `_DOWNLOAD_URL`照旧是写死的常量

## 第一部分:fork florr-auto-afk(本仓库外,一次性)

fork `Shiny-Ladybug/florr-auto-afk`,从`v1.1.1` tag开分支、推到fork的`main`(它的workflow push触发只认`branches: [main]`)。

`segment.py`,`root.mainloop()`那行之前(v1.1.1在418行附近):

```python
if get_config()["runs"].get("autoStart", False):
    toggle_segment_process()
    root.iconify()
```

- `.get(..., False)`:老的`config.json`里没这个键也不炸
- `toggle_segment_process()`在v1.1.1里不带参数(v1.3.2才要`capture_windows`);`capture_windows`默认是`[]`(`gui_utils.py:22`)→ 走全屏检测,正好是我们要的(游戏全屏跑)
- `root.iconify()`最小化到任务栏。不用`withdraw()`——那个连任务栏图标都没有,用户想手动停就只能去任务管理器杀进程

`config.json`里加`"runs": {"autoStart": false}`,上游默认行为不变。

Actions(windows-latest + PyInstaller,v1.1.1那版workflow)自己出`florr-auto-afk-v1.1.1-auto.zip`并建release。zip名字里的`-auto`是v1.1.1 workflow里写死的(`upload_exe_with_name: 'florr-auto-afk-v${version}-auto'`),跟本功能无关,别误读成"自动启动版"。

v1.1.1那版workflow已经确认可用:`on: push: branches: [main]`、`permissions: contents: write`、`constants.py`里`VERSION_TYPE = "Release"`(后来版本才加的"Dev版就退出"检查在这版还没有),release tag和zip名都从`VERSION_INFO`推出来。

建议把`constants.py`的`VERSION_INFO`改成`1.1.1-autostart`——zip名字跟着变成`florr-auto-afk-v1.1.1-autostart-auto.zip`,一眼能跟官方原件区分开,不会哪天搞混下错。

本仓库`_DOWNLOAD_URL`改指这个新release的zip。`_INSTALL_DIR_NAME`从此纯粹是**我们自己选的目录名**(解压目标),不再需要跟zip内部结构对上——官方zip本来就没有顶层目录。顺带把它改成`florr-auto-afk-v1.1.1-autostart`:已经装了旧版官方包的用户,`_EXE_PATH`是存在的,不换名字就永远不会重新下载,而旧版那个exe没有`autoStart`,每次启动都白等到超时。


## 第二部分:本仓库`afk_watch.py`

### 1. 修解压路径

`zf.extractall(_INSTALL_ROOT)` → `zf.extractall(_INSTALL_DIR)`(官方zip没有顶层目录,见Problem)。解压完检查`_EXE_PATH`是否存在,不存在就返回False并打印清楚原因(zip结构跟预期不符),不要拖到`Popen`才炸。

### 2. `_write_afk_config()`(新增)

读`_INSTALL_DIR/config.json`,**只覆盖我们依赖的键**,其余原样保留(用户自己调的`mouseSpeed`之类不能被冲掉),写回:

| 键 | 值 | 为什么 |
|---|---|---|
| `runs.autoStart` | `true` | 本次新增的开关,免手点run |
| `runs.autoTakeOverWhenIdle` | `false` | 我们一直在动鼠标,它的idle门永远不会触发(见2026-08-11那份design) |
| `runs.moveAfterAFK` | `false` | 它解完题的WASD乱走会跟我们的移动/防卡死打架 |
| `advanced.verbose` | `true` | 保证事件真的写进`latest.log` |
| `advanced.skipUpdate` | `true` | 免得启动时联网查模型更新,拖时间/失败 |

文件不存在或JSON坏了:重新写一份只含上面这些键的最小config(它的`get_config()`对其余键有默认值兜底)。写失败只打印警告,继续往下走——跟这块一贯的"AFK是可选增强,不阻塞主程序"一致。

### 3. `_wait_for_segment_started()`(新增):确认它真的进入检测

`latest.log`是用`'a'`打开的(`segment_utils.py:67`/`:101`,v1.1.1和v1.3.2都一样),跨次运行**不清空** → 不能整文件搜marker,上次运行留下的记录会被误判成本次成功。

- `Popen`**之前**先记下`os.path.getsize(LATEST_LOG_PATH)`(文件不存在算0)
- 启动后每秒读一次这个offset之后的新增字节,找`Segment process started`这个子串(`log_ret(..., "INFO", shared_logger)`,`save`默认True会落盘;v1.1.1里`segment.py:202`和`:234`两处,后者带句号,用子串匹配两个都能命中)
- 命中 → 打印`✅ AFK弹窗自动处理已开启`
- 90秒超时 → 打印`⚠️ 没能确认florr-auto-afk已开始检测, 请在它的界面里点"run"按钮`,不抛异常、不阻塞主程序
- 中途文件比记录的还小(被删/被换)→ offset退回0重读,跟`_read_new_lines()`同一个套路

这段用自己的局部offset,**不碰**模块级的`_last_offset`/`_initialized` —— 主循环第一次`poll_afk_pause()`照旧跳到文件末尾,现有行为不变。

### 4. 删掉无条件的"请点run按钮"提示

只有第3步验证超时才提示手点。

### `ensure_florr_auto_afk_running()`最终形态

```
非Windows                → 直接return
已经在跑                 → 打印一句, return           (已实现)
没装 + 用户拒绝下载      → 打印一句, return           (已实现)
没装 + 下载/解压失败     → 打印原因, return           (已实现)
装了(或刚下完)          → 写config → Popen → 等marker → 成功/超时各打印一句
```

## 测试

沿用[test_afk_watch.py](../../../test_afk_watch.py)现有套路:全部mock掉`subprocess`/网络,Windows专属分支用`monkeypatch.setattr(afk_watch.sys, "platform", "win32")`,文件系统用`tmp_path`。

- `_download_and_extract()`:用**无顶层目录**的假zip(跟官方一致),解压后`_EXE_PATH`真的存在;zip结构不对时返回False
- `_write_afk_config()`:已有config的其它键(`mouseSpeed`等)保留、我们那5个键被覆盖;文件缺失/JSON损坏时能重建;写失败不抛
- `_wait_for_segment_started()`:新增行里有marker → True;**只有旧行**有marker → 超时False(防跨次运行误判);带句号那个变体也能命中;超时不抛
- `ensure_florr_auto_afk_running()`:装了 → 写config → Popen → 等marker,顺序对;验证超时时打印手点提示但不抛

## 风险

- **fork那3行只能在Windows上真验证** —— mac开发机跑不了`segment.exe`(见`windows-is-real-deployment`那条memory)。实机要确认两件事:窗口自己最小化了、`latest.log`里出现`Segment process started`
- **90秒超时是估的** —— 它README的FAQ说初始化要10秒以上(PyInstaller解包+torch+两个YOLO模型加载)。实机看真实耗时再调这个常量
- **v1.1.1的marker文本只从源码tag确认过** —— 实机跑一次核对
- **v1.1.1的workflow用的是`sayyid5416/pyinstaller@v1`这个第三方action + 2025年的`py311-requirements.txt`** —— 时隔一年多,依赖解析/action本身有可能已经跑不通。Actions红了就得先修构建(通常是给torch之类钉版本),这是第一部分的主要不确定性,不影响第二部分开工

## 不需要改的地方

`main.py`不用动:`ensure_florr_auto_afk_running()`的签名和调用位置(`main.py:529`)都不变,行为变化全在函数内部。`poll_afk_pause()`和它的三个调用点也不动。

