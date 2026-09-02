# GUI 阶段2 — 周计划调度 + 账号管理 — design

## Problem

阶段1（`docs/superpowers/specs/2026-08-31-gui-phase1-design.md`，已合并 `main`）给了 CustomTkinter 控制面板：单页控制台，一套扁平 `config.json`，点「开始」起一个 `main.py --worker` 子进程。侧栏 `账号` / `时间表` 是灰置占位。

阶段2 要把占位页做实：

- **账号管理** —— 每个 florr.io 账号 = 一个独立 Chrome profile 目录（`--user-data-dir`）。GUI 里建 / 改名 / 删除 profile，用户自己取别名。
- **周计划调度** —— 界面中间从「单套控制台」换成「时块列表」。每个时块勾若干星期几 + 起止时间 + 用哪个 profile + 打哪张图 + 目标点 / 刷怪区 + 若干开关。到点自动切 profile（关 Chrome 重开）、切地图、起停 worker，全程零人工。

同时修阶段1 遗留的两个引导体验问题（用户实测反馈）：

1. 专用 Chrome 起来后不自动打开 florr.io，要手动敲网址。
2. 引导期间主窗口被钉 `-topmost`、弹的是阻塞式 `messagebox` 循环，用户没法最小化窗口去干别的。

## Goal

- `config.json` 升到 v2：`profiles` + `schedule`（时块数组）+ `active`（worker 只读这一段）+ `afk_enabled`。v1 扁平配置自动迁移成「一个全周全天时块 + 名为『默认』的 profile」。
- 侧栏 `控制台` → `时间表`（时块列表）；`账号` 页变实（profile 列表：新建 / 登录 / 改名 / 删除）。
- `▶ 开始调度 / ■ 停止调度`：GUI 内 `after()` 循环按周计划驱动 worker，切换时块时按需关 / 开 Chrome（换 profile）、重写 `config["active"]`、重启 worker。空档期停 worker。
- 调度驱动的运行 **完全无人工**：Chrome 带 `--start-fullscreen` + `https://florr.io` 起，worker 循环自己点「开始」按钮、处理死亡结算画面；换服务器走 CDP。
- 登录引导重做成**非模态、不置顶、可最小化**：Chrome 自动开 florr.io，GUI 里一块引导区提示登录，后台轮询到 florr 标签页就点亮「完成」。**只有用户主动「新建账号 / 重新登录」时才走这个流程**。
- 索敌 AI 默认开（已从 YOLO 截图换成 canvas 解码，不再需要 `models/*.pt`），文案里去掉「YOLO」，标注「仅沙漠」。

## Non-goals

- **worker 内部逻辑** —— `run_worker` 只多读一层 `cfg["active"]`（缺失回退顶层扁平键），寻路 / 刷怪 / 索敌 / 换服全不动。
- **多开 Chrome** —— 任一时刻只有一个 Chrome 实例（当前时块 profile 的）。换 profile = 关掉重开，十几秒停顿可接受。
- **手动「立刻跑某时块」入口** —— 只有调度这一种跑法。
- **索敌 AI 细分参数**（`AVOID_TRIGGER_PX` 等）—— 仍是一个总开关。
- **跨时区 / 夏令时 / 秒级精度** —— `after()` 每 30s tick，本地时间，`HH:MM` 精度。
- **时块级别的 AFK 控制** —— AFK 开关全局、独立，跟阶段1 一样。
- **B站脚本 / `models/*.pt` 孤儿文件 / 纯注释里的 YOLO 字样** —— plan 里单列一个收尾 task，不是本 spec 主线。

## `config.json` schema v2

```jsonc
{
  "version": 2,
  "afk_enabled": false,

  "profiles": [
    { "alias": "默认", "dir": "chrome-profiles/默认" }
  ],

  "schedule": [
    {
      "id": "blk-1",                    // GUI 生成的稳定 id，不随改名 / 排序变
      "enabled": true,                  // 行内开关，临时禁用
      "days": [0, 2, 4],                // 0=周一 … 6=周日，勾多天
      "start": "09:00",
      "end":   "12:00",                 // start >= end ⇒ 跨午夜到次日
      "profile": "默认",                // 引用 profiles[].alias
      "map": "desert",
      "location": [22, 32],
      "farming_area": [[9, 8], [51, 56]],
      "farming_duration": 300,
      "consecutive_short_round_limit": 2,
      "enemy_ai_enabled": true,
      "auto_switch_server": true
    }
  ],

  // GUI 在起 worker 前刷成「当前生效时块」的刷怪参数；worker 只读这一段。
  "active": {
    "map": "desert", "location": [22, 32], "farming_area": [[9, 8], [51, 56]],
    "farming_duration": 300, "consecutive_short_round_limit": 2,
    "enemy_ai_enabled": true, "auto_switch_server": true
  }
}
```

### 迁移（v1 扁平 → v2）

`load_config()` 读到文件里没有 `"version"` 或 `version < 2`：

1. `profiles = [{"alias": "默认", "dir": "chrome-profiles/默认"}]`。把阶段1 的旧目录 `<exe>/chrome-profile/` 原地 `os.rename` 成 `<exe>/chrome-profiles/默认/`（父目录先 `makedirs`）。改名失败（目录被占用 / 不存在）→ 只写映射，用户下次用「默认」时触发登录引导。
2. 旧的 7 个扁平键（`map` / `location` / `farming_area` / `farming_duration` / `consecutive_short_round_limit` / `enemy_ai_enabled` / `auto_switch_server`）→ 一个时块：
   `{"id": "blk-1", "enabled": true, "days": [0,1,2,3,4,5,6], "start": "00:00", "end": "00:00", "profile": "默认", ...那 7 个值}`。
   `start == end == "00:00"` 约定为「全天」。
3. `afk_enabled` 原样保留（缺则 `False`）。
4. `active` = 该时块的 7 个刷怪参数。
5. `version = 2`，`save_config()` 写回。**不备份 `.bak`** —— 这是主动升级，写回前 `_coerce` 过一遍；跟 `app_config` 现有「只读不写用户坏文件」的谨慎一致。

### `_coerce` v2

- 顶层不是 dict → 全 `DEFAULTS_V2`（= 迁移产物那种单「默认」profile + 一个全周全天时块，但 `enabled=false`，避免全新装就空跑）+ 警告。
- `profiles`：过滤掉 `alias` 非非空字符串 / `dir` 非字符串的项；空了补 `{"alias": "默认", "dir": "chrome-profiles/默认"}`。别名去重（后出现的重名丢弃 + 警告）。
- `schedule`：逐时块校验，**坏块整块丢弃 + `print` 警告**（一个坏时块没有「合理默认」）。单块校验：
  - `id` 非空字符串（缺 → 生成 `blk-<n>`）
  - `enabled` bool（缺 → `true`）
  - `days` 是 `list`，元素是 `0..6` 的 int，去重排序，非空
  - `start` / `end` 匹配 `^([01]\d|2[0-3]):[0-5]\d$`
  - `profile` 是字符串且命中某个 `profiles[].alias` —— 不命中则该块 `enabled=false` + 警告（不丢，用户改完 profile 还能启用）
  - `map` ∈ `_VALID_MAPS`
  - `location` 是 2 个 int
  - `farming_area` 是 `[[int,int],[int,int]]`
  - `farming_duration` int > 0
  - `consecutive_short_round_limit` int >= 1
  - `enemy_ai_enabled` / `auto_switch_server` bool
- `active`：按阶段1 那套 7 键 `_coerce`（复用现有逻辑）；缺 `active` → 用第一个 `enabled` 时块的参数，没有就用 `DEFAULTS`。
- 保存时若两个 `enabled` 时块重叠（`blocks_overlap`）→ `print` 警告但**不拦**（GUI 层已在编辑器里拦；这里是防御性日志，不改数据）。

### 新增纯函数（GUI 与测试共用，不 import 任何 GUI 库）

- `_valid_time(s) -> bool` —— `HH:MM` 格式。
- `expand_block_days(block) -> list[(weekday, start_min, end_min)]` —— 把一个时块摊平成「(星期, 起分钟, 止分钟)」区间列表；跨午夜（`start >= end` 且非 `00:00/00:00`）拆成当天 `[start, 1440)` + 次日 `[0, end)`（次日星期 = `(d+1) % 7`）；`00:00/00:00` → `[0, 1440)`。
- `blocks_overlap(a, b) -> bool` —— 两个时块任一 `(weekday, 区间)` 对在同一星期且分钟区间相交（半开区间，`end` 相接不算重叠）即冲突。
- `active_block(schedule, weekday, hhmm) -> block | None` —— 遍历 `enabled` 时块，返回第一个「摊平后有某区间在 `weekday` 且 `hhmm` 落在 `[start, end)`」的块。

## 进程模型 + 调度器状态机

进程不变：GUI 进程（Tk 主循环）+ 按需一个 `main.py --worker` 子进程 + 一个 Chrome 实例。worker 一次跑一份 `config["active"]`。

**调度器** —— GUI 内 `self.after(30_000, self._sched_tick)` 循环，仅在「▶ 开始调度」后运行。持有 `self._running_block_id`（当前正在跑的时块 id，`None` = 空档）和 `self._chrome_profile`（当前 Chrome 用的 profile 别名）。

每次 tick：

1. `blk = app_config.active_block(schedule, now_weekday, now_hhmm)`。
2. 对比 `self._running_block_id`：
   - **`blk` 是同一个 id** → 什么都不做。
   - **`blk` 是另一个时块**：
     1. 停 worker：关 `proc.stdin`（EOF → worker `reset_keyboard()` 退），3s 未退 `proc.kill()`。
     2. `blk["profile"] != self._chrome_profile` → 换 Chrome：
        - 查 profile 目录存在 + 已登录（目录存在 且 起 Chrome 后 `wait_for_florr_tab(30)` 拿到标签页）。
        - `cdp_bridge.launch_chrome_for_profile(profile_dir, open_url="https://florr.io", fullscreen=True)`。
        - `wait_for_florr_tab(30)` 超时 → **跳过该时块**：日志 `⚠️ profile『X』未登录 / florr.io 没起来，跳过时块 blk-N`，`self._running_block_id = None`，Chrome 留着，等下次 tick。
        - 成功 → `self._chrome_profile = blk["profile"]`。
     3. `blk["profile"] == self._chrome_profile`（只换图 / 参数）→ 不碰 Chrome。
     4. `cfg["active"] = {那 7 个刷怪参数}`；`app_config.save_config(cfg)`。
     5. 起 worker（`subprocess.Popen(worker_command(), ...)`，跟阶段1 一样的管道 / 编码 / reader 线程）。
     6. `self._running_block_id = blk["id"]`。日志 `▶ 进入时块 blk-N（默认 / desert）09:00–12:00`。
   - **`blk is None` 且之前在跑**（进入空档）→ 停 worker，`self._running_block_id = None`，Chrome 留着。日志 `⏸ 空档，worker 已停`。
3. 更新状态行：`当前：blk-1（默认 / desert）09:00–12:00 · 下一个：blk-3 今天 18:00`（下一个 = 摊平所有 `enabled` 区间里，从此刻起最近的一个起点；算不出显 `—`）。

**worker 自己退出**（崩溃 / EOF）：阶段1 的 `_on_worker_exit` 回调照旧把按钮 / 状态复位；调度运行中的话额外把 `self._running_block_id = None`，下次 tick 会重新评估（等于自动重启当前时块）。

**▶ 开始调度 / ■ 停止调度**：

- 开始：`schedule` 非空校验（空 → 日志提示「先加时块」，按钮不变）。锁编辑：时块列表转只读（✎ / 🗑 / ＋ 灰置），账号页整页只读。`self._sched_running = True`，立刻跑一次 `_sched_tick`（此刻在某时块内就直接起）。按钮 → `■ 停止调度`。
- 停止：停 worker（同上），取消 `after` 句柄，`self._sched_running = False`，解锁编辑。Chrome 留着。按钮 → `▶ 开始调度`。

**AFK 开关**：全局、独立。阶段1 的 `_on_afk_toggle` / `_ensure_afk` / 启动时补 ensure 原样保留，跟调度启停互不影响。

**直接 `python main.py --worker`**（调试）：`run_worker` 读 `cfg.get("active")`，`None` 就回退顶层扁平键（迁移前的老文件 / 手写文件），再没有就 `app_config.DEFAULTS`。

## 主界面布局

```
┌────────┬─────────────────────────────────────────────┐
│ 时间表 │  时块列表（CTkScrollableFrame，折叠行）        │
│ 账号   │   ☑ 一三五  09:00–12:00  默认 · desert   ✎ 🗑 │
│        │   ☐ 六日     14:00–20:00  小号2 · ocean  ✎ 🗑 │
│(spacer)│  [ ＋ 新增时块 ]                              │
│        │  ── 引导区（仅新建 / 重新登录时显示，非模态）──│
│        │  日志 CTkTextbox（只读，等宽）                 │
│  AFK   │  状态：当前 blk-1（默认 / desert）· 下一个 …   │
│ [开关] │  [ ▶ 开始调度 ]                               │
└────────┴─────────────────────────────────────────────┘
```

- 侧栏：`时间表` / `账号` 两页 + 底部 AFK 开关（阶段1 原样，Mac 上仍灰置 + 「仅 Windows」注）。
- 时块折叠行：`☑启用`（`CTkCheckBox`，即时写 `enabled` + `save_config`）· 星期简写（`一三五`）· `HH:MM–HH:MM` · `profile别名 · map` · `✎` · `🗑`（有引用无所谓，时块可随便删；确认框）。
- 调度运行中：整行只读，`☑` / `✎` / `🗑` / `＋` 全 `state=disabled`。

### 时块编辑器 `gui_schedule.TimeBlockEditor(CTkToplevel)`

点 `✎` 或 `＋` 弹出。**非置顶、可最小化**（不 `grab_set()`，不 `-topmost`，`transient(parent)` 仍设，方便窗口管理）。

字段：

- 星期：7 个 `CTkCheckBox`（一 二 三 四 五 六 日）。
- 起 / 止：两个 `CTkEntry`，占位 `09:00`，失焦校验 `_valid_time`。
- 账号：`CTkOptionMenu`，值 = `profiles[].alias` + 末项 `＋ 新建…`。选 `＋ 新建…` → 弹小输入框要别名 → 校验（非空 / 不重名 / `_safe_dirname`）→ `config["profiles"]` 加项 + `os.makedirs(chrome-profiles/别名)` → 立即触发登录引导（见下），引导「完成」后下拉选中新别名。
- 地图：`CTkOptionMenu`（`desert` / `ocean` / `anthell`）。切图 → `MapPicker.load_map` + 清空该编辑器里的目标点 / 区域（坐标每图独立）。
- 地图选择器：复用 `gui_map_picker.MapPicker`（点 = 目标点画十字，拖 = 刷怪矩形，滚轮 = 1–4× 缩放）。
- 索敌 AI：`CTkSwitch`，默认开，右侧灰字「仅沙漠」。
- 自动换服务器：`CTkSwitch`，默认开。关 → 「短局阈值」`CTkEntry` `state=disabled`。
- ▸ 高级选项（`CTkFrame`，默认折叠，点标题展开）：
  - `刷怪时长（秒）` + `?`（`_Tooltip`：「一轮在刷怪区停留多少秒。也是『刷满』判定线 —— 一条命活过这个秒数才算这轮刷满。」）
  - `连续短局阈值` + `?`（`_Tooltip`：「连续这么多轮没撑到刷怪时长（被秒 / 到不了区），且『自动换服务器』开着，就自动跳服。」）
- `[取消] [保存]`。

保存校验（纯函数 `gui_schedule.validate_block(block, others) -> None | str`，返回错误字符串或 `None`）：

- 星期勾选 ≥ 1
- `start` / `end` 都合法 `HH:MM`
- 目标点和刷怪区至少给一个（都没有 → 「在地图上点个目标点或框个刷怪区」）；只给一个时按阶段1 `resolve_point_and_area` 那套补全（点→小方块 / 区域→中心点）
- `farming_duration` / `consecutive_short_round_limit` 正整数
- 跟 `others`（除自己外所有时块，不管 `enabled`）逐一 `blocks_overlap` —— 有冲突 → 「跟时块 blk-3 时间重叠」

校验失败 → 编辑器里红字，不关窗、不写。

### `_Tooltip` 助手（放 `gui_schedule.py` 或新 `gui_widgets.py`）

绑 `<Enter>` / `<Leave>`：`<Enter>` 后 `after(400)` 弹一个 `Toplevel(overrideredirect=True)` 放一个 `Label`（浅底深字），跟到鼠标附近；`<Leave>` / 点击 → `destroy`。纯 tkinter，不依赖第三方。

## 账号页 `gui_accounts.AccountsPage(CTkFrame)`

```
默认    chrome-profiles/默认    [登录] [改名] [删除]
小号2   chrome-profiles/小号2   [登录] [改名] [删除]
[ ＋ 新建账号 ]
```

- **＋ 新建账号**：输别名 → 校验（非空 / 不重名 / `_safe_dirname` —— 只留 `\w` `-` 汉字，别的替换成 `_`；空结果拒）→ `config["profiles"]` 加 `{"alias", "dir": f"chrome-profiles/{alias}"}` + `os.makedirs(dir, exist_ok=True)` + `save_config` → 进登录引导。
- **登录 / 重新登录**：对该 profile 跑登录引导（关 Chrome → `launch_chrome_for_profile(dir, open_url="https://florr.io", fullscreen=False)` → 引导区轮询 `wait_for_florr_tab(1)` → 点亮「完成」）。用于换绑账号 / 首次没登成。
- **改名**：`CTkInputDialog` 要新别名 → 校验 → `os.rename(旧dir, 新dir)` + 改 `profiles[i]["alias"]` / `["dir"]` + 遍历 `schedule` 把 `block["profile"] == 旧别名` 的改成新别名 + `save_config`。`os.rename` 失败（目录被 Chrome 占用）→ 提示「先关掉该账号的 Chrome 窗口再改」。
- **删除**：`used = [b["id"] for b in schedule if b["profile"] == alias]` —— 非空 → 拦：「时块 blk-2、blk-5 还在用『X』，先改掉那些时块的账号」。空 → 确认框 → 删 `profiles` 项 + `save_config`。**目录留在磁盘**（用户数据，不动），日志说一句路径。
- 调度运行中：整页控件 `state=disabled`。
- Mac：建目录 / 改名 / 删除都跨平台，正常可用；登录引导里 Chrome 用 `open -a "Google Chrome" --args`。

## Chrome flow 改动

### `cdp_bridge.py`

- 新增 `launch_chrome_for_profile(profile_dir, *, open_url="https://florr.io", fullscreen=False)`：
  `_quit_all_chrome()` → 拼 `--remote-debugging-port=9222` `--remote-allow-origins=*` `--user-data-dir=<profile_dir>` `--no-first-run` `--no-default-browser-check` `--no-startup-window`（否则 `open_url` 会开成第二个窗口）+（`fullscreen` 时）`--start-fullscreen` + `open_url` 作为位置参数 → 平台分派（Windows 找 `chrome.exe`；macOS `open -a "Google Chrome" --args`）。
- `_CHROME_PROFILE_DIR` 保留为「默认」profile 的目录常量（`chrome-profiles/默认`，迁移目标）。`quit_and_launch_chrome()` 变成 `launch_chrome_for_profile(_CHROME_PROFILE_DIR, fullscreen=False)` 的 wrapper（`launch_dedicated_chrome()` 命令行版一并指过去，不再各写一套）。
- `is_dedicated_chrome_ready()` / `wait_for_florr_tab()` / `is_cdp_port_reachable()` / `find_florr_tab()` 不变。

### `gui_chrome_flow.py`（重写）

- 删 `ensure_chrome_ready` 的阻塞 `while True` + `messagebox` + `ChromeSetupCancelled`。
- `LoginGuide`（GUI 引导区控制器，挂在主窗口一块常隐的 `CTkFrame` 上）：
  - `start(profile_dir, *, on_done, on_cancel)`：`cdp_bridge.launch_chrome_for_profile(profile_dir, open_url="https://florr.io", fullscreen=False)` → 显示引导区（三行：`① 已在 Chrome 打开 florr.io` / `② 在那个窗口登录你的账号` / `③ 登录完成后点右边` + `[完成（检测中…）]` 按钮，初始 `disabled` + `[取消]`）。
  - `after(2000)` 轮询 `cdp_bridge.wait_for_florr_tab(1)`：非 `None` → 按钮变 `[完成]` `state=normal`（用户也可提前手动点，点了就走 `on_done`）。
  - 「完成」→ 收起引导区 + `on_done()`；「取消」→ 收起 + `on_cancel()`。
  - 全程不设 `-topmost`；主窗口保持普通窗口，能最小化。

### `gui_app.py`

- `_start_worker` 里那串 `self.attributes("-topmost", True)` + 阻塞 `gui_chrome_flow.ensure_chrome_ready` + 「请切到全屏」`messagebox.askokcancel` → **全删**。Chrome 起在调度器状态机里，带 `fullscreen=True`，无任何提示。
- `_busy_modal`（AFK 用）保留，但去掉 `-topmost`（AFK 准备本就说「界面仍可操作」）。

## 文件改动

| 文件 | 改动 |
|---|---|
| `app_config.py` | v2 schema + `_coerce` v2 + `migrate_v1` + `_valid_time` / `expand_block_days` / `blocks_overlap` / `active_block` 纯函数。`DEFAULTS` 保留（worker `active` 缺失兜底 + 迁移取值）。`_VALID_MAPS` 不变 |
| `gui_app.py` | 侧栏 `控制台`→`时间表`，`账号` 页接 `AccountsPage`。中间换时块列表（`CTkScrollableFrame` + 折叠行 + ＋）。`▶ 开始/停止` → 调度启停 + `_sched_tick` 状态机 + 编辑锁。挂 `LoginGuide` 的引导区 `CTkFrame`。删 `_start_worker` 的 `-topmost` / 切全屏 `messagebox` / 阻塞 `ensure_chrome_ready`。索敌开关文案去「YOLO」。worker `Popen` / reader 线程 / `_on_worker_exit` 复位逻辑复用阶段1，`_on_worker_exit` 里加「调度中则清 `_running_block_id`」 |
| `gui_schedule.py` **新** | `TimeBlockEditor(CTkToplevel)`；`validate_block(block, others)` 纯函数；折叠行渲染 + 列表增删；`_Tooltip`；`_safe_dirname` |
| `gui_accounts.py` **新** | `AccountsPage(CTkFrame)`：新建 / 登录 / 改名 / 删除（删除拦引用，改名同步时块 `profile`） |
| `gui_chrome_flow.py` | 重写成 `LoginGuide`（非模态轮询），删阻塞 `messagebox` 循环 + `ChromeSetupCancelled` |
| `cdp_bridge.py` | `launch_chrome_for_profile(dir, *, open_url, fullscreen)`；`quit_and_launch_chrome` / `launch_dedicated_chrome` 变 wrapper；`_CHROME_PROFILE_DIR` → `chrome-profiles/默认` |
| `main.py` | `run_worker` 读 `cfg.get("active")`，缺则回退顶层扁平键再回退 `DEFAULTS`。其余不动 |
| `README.md` / `PACKAGING.md` | GUI = 周计划调度 + 账号页；`config.json` v2；**删掉所有「自备 `models/desert.pt`」段落**（索敌已换 canvas decode，默认开）；`chrome-profiles/` 说明 |
| `requirements.txt` / `main.spec` | 无新依赖。`chrome-profiles/` 是运行时目录不打包 |

## 测试

- `test_app_config.py` **扩**：
  - v1→v2 迁移：扁平 7 键 → `version=2` + `profiles=[默认]` + 一个 `days=[0..6] start=end="00:00"` 时块 + `active` = 那 7 值；`afk_enabled` 保留。
  - v2 往返 `save`→`load` 一致。
  - 坏时块整块丢弃（`days` 空 / `start` 非法 / `map` 非法 / `location` 给字符串各一例）+ 好块留下。
  - 悬空 `profile` → 该块 `enabled=false`，不丢。
  - `profiles` 空 → 补「默认」；别名重复 → 去重。
  - `_valid_time`：`09:00` 真，`9:00` / `24:00` / `12:60` / `""` 假。
  - `expand_block_days`：普通块单区间；`00:00/00:00` → 全天 `[0,1440)`；`22:00→02:00` 勾周一 → 周一 `[1320,1440)` + 周二 `[0,120)`。
  - `blocks_overlap`：同天相交真；不同天假；边界相接（`09:00–12:00` vs `12:00–15:00`）假；跨午夜 `22:00–02:00`（周一）vs `01:00–03:00`（周二）真。
  - `active_block`：命中返回该块；空档返回 `None`；跨午夜 01:00 命中前一天起始的块。
- `test_gui_schedule.py` **新**：`validate_block` —— 星期空 / 时间非法 / 无坐标 / 与 `others` 重叠 各返回非空错误串；全合法返回 `None`。`_safe_dirname`：汉字保留、空格→`_`、纯符号→拒。不实例化 tk。
- `test_gui_accounts.py` **新**：改名把引用它的时块 `profile` 一起改；删除被引用 → 返回拦截原因；新建重名 → 拒。逻辑函数抽出来测，不实例化 tk。
- `test_cdp_bridge.py` **扩**：`launch_chrome_for_profile` 拼参数 —— 含 `--user-data-dir=<dir>` / `open_url` 位置参数 / `fullscreen=True` 带 `--start-fullscreen`、`False` 不带（`subprocess.Popen` / `_find_windows_chrome` 打桩，`sys.platform` 参数化 win32 / darwin）。
- `test_main_worker.py` **扩**：`run_worker` 读 `cfg["active"]` 时各局部变量取自 `active`；`cfg` 无 `active` 时回退顶层扁平键；都没有回退 `DEFAULTS`。
- `test_gui_chrome_flow.py` **重写**：`LoginGuide.start` → `launch_chrome_for_profile` 被调；轮询到标签页前按钮 `disabled`、之后 `normal`；「完成」调 `on_done`、「取消」调 `on_cancel`（`cdp_bridge` 全打桩，不起真 Chrome、不弹窗）。
- 现有 241 测试保持全绿（阶段1 的 `test_gui_app.py` 若断言了「控制台」字样 / 单页结构需同步改）。

## 收尾 task（plan 里单列，非本 spec 主线）

- 删 `models/desert.pt` + `models/sandstorm.pt`（canvas decode 后无引用；`git rm`）。
- `docs/bilibili/视频1-*.md` / `视频2-*.md` / `视频开头-*.md` 里 YOLO / 模型文件的镜头改写成 canvas 解码 + 「周计划调度」。
- `enemy_detect.py` / `main.py` 里纯注释的 `YOLO` 字样顺手改（低优先，不影响运行）。
