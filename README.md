# florr-auto-pathing

Time to upload some of my useful codes.

The whole codes stand on CLIENT-SIDE.

This project is **welcomed** to be used in any other florr.io projects.

## The Lazy Theta Star

After I used `a*` pathing for a few months, I found this method caused a lot of time wasting on collision with florr's walls, I quickly turned to use `lazyθ*`. And here's the differences (Green for lazy_theta_star and Red for a_star)

![](./compare.jpg)

## Maps

To decide on the positions and areas, I've already prepared `map_select.py` and `area_select.py` for you.

> \> python3 map_select.py # And you click anywhere you want on the map
>
> Map position: (53, 144)
> Map position: (55, 109)

> \> python3 area_select.py # And you first click on the left_top bound, next click on the right_bottom bound.
>
> Area added: [(6, 5), (40, 44)]
> Area added: [(1, 43), (27, 87)]
> Final areas: [[(6, 5), (40, 44)], [(1, 43), (27, 87)]]

## Enemy Detection (Sandstorm Zone)

`auto_farming()` can chase/avoid mobs by rarity. It reads mob position, HP and
rarity by decoding florr.io's canvas draw calls over CDP — no model file, no
screenshot inference (the old YOLO `models/desert.pt` path is gone). Enabled by
default; currently only meaningful on the desert map. See
`docs/superpowers/specs/2026-09-01-canvas-decode-enemy-detection-design.md`
and `2026-08-16-sszone-enemy-detection-design.md` for the rarity-color-table
caveats.

## florr 反转键（反转攻击 / 反转防御）

The worker never presses attack — it relies on florr's **Settings → Controls →
反转攻击键 / "Invert attack button"** being ON, so flowers stay open and keep
attacking without a held key. `反转防御键` is the symmetric optional control.

每个调度时块单独配 `invert_attack`（默认开）/ `invert_defense`（默认关），在时块编辑器里勾。调度器切到某个时块时会重启 worker，把该时块的值写进 florr。开→每轮写 `1`，关→每轮写 `0`（强制关，不是「留 florr 账号里的值」）。

The addresses are calibrated constants in `florr_settings.py`:
`INVERT_ATTACK_ADDR = 0x53430E`, `INVERT_DEFENSE_ADDR = 0x534310`. The
invert-defense address is user-supplied and not re-verified with
`settings_finder.js`. If florr ships a new build that moves a byte, the worker
logs a warning (`⚠️ … 未确认 …`) for that flag and keeps farming (it will
path/circle fine but deal no damage if invert-attack is the broken one). To
(re)calibrate: open the florr.io devtools console, paste `settings_finder.js`,
follow its USAGE header (toggle the checkbox 4–6× calling `set.mark()` each
time, then `set.solve()`), and put the returned address into the matching
constant in `florr_settings.py`.

## 按区域切换 loadout（可选）

每个调度时块有两个切换点，各配一次**和弦**（像 Ctrl+C：按住修饰键 → 按数字 → 松开）：

- **进游戏切换装备**：每轮真的（重新）进入游戏后按一次。
- **到刷怪区切换装备**：寻路到刷怪区域后按一次。

每个切换点三个控件：

- **开关** —— 关（默认）= 什么都不按。
- **按住** —— `无` / `k` / `l`。`无` 就直接按数字，不按修饰键。
- **按** —— 数字键 `1`…`0` 十选一。

例：`k` + `3` = 按住 `k`、按一下 `3`、松开 `k`。修饰键和数字键的含义由你在 florr
键位里怎么绑决定（没绑对就是按下去没反应）。

按键通过前台窗口发给 florr，跟 bot 的其它输入一样要求 florr 窗口在最前。

## Implements

Resolution is auto-detected at startup (`pyautogui.size()`) and every screen coordinate is scaled
from its original 1920x1080 calibration — see
`docs/superpowers/specs/2026-08-26-resolution-adaptation-design.md` for how, and its "Known risk"
section for the one thing that isn't verified (non-16:9 windows, if florr.io turns out to letterbox
instead of stretching its UI — recalibrate the affected constant with `debug_screen_pos.py` if so).

Run `python main.py` to open the control-panel GUI.

**时间表 (schedule) page** — the main view is a list of time blocks. Each block
covers a set of weekdays + a start/end time (a block whose start ≥ end crosses
midnight; `00:00–00:00` means all day) and carries: which account, which map
(海洋 / 蚁狱 are greyed out for now — desert only), a target point / farming area
(map picker), enemy-detection AI, auto-switch-server,
and — under 高级选项 — farming duration and the consecutive-short-round threshold
(both with hover `?` explanations). Overlapping blocks (shared weekday + time)
are rejected on save. Click **▶ 开始调度** and the GUI drives everything by the
weekly plan: at each block boundary it stops the worker, relaunches Chrome on
that block's account profile (`--start-fullscreen`, opens florr.io) if the
account changed, writes the block's params into `config.json`, and starts a
fresh worker. Gaps in the plan leave the worker stopped. The whole scheduled run
needs zero interaction — the worker clicks the in-game start button, handles
death screens itself, and re-locks the florr server to the block's biome every
time it's back at the start screen (florr doesn't remember the last-picked
biome).

**账号 (accounts) page** — each florr.io account is its own Chrome profile
directory under `chrome-profiles/<别名>/`. New / login / re-login opens a
non-modal login guide (Chrome opens florr.io in a normal window; log in, click
完成). Rename updates every block that referenced the old name; delete is
blocked while any block still uses the account.

The bottom-left **AFK** switch is global and independent of the schedule.

**Direct CLI mode:** with a ready Chrome instance you can skip the GUI and run
`python main.py --worker` — it reads `config.json`'s `active` slice (the params
the GUI last wrote for the running block) and runs the bot loop immediately.

Configuration saves to `config.json` next to the script (schema v2:
`profiles` + `schedule` + `active` + `afk_enabled`). An old flat v1 config is
migrated automatically on first launch into a single all-week block under a
`默认` account (the old `chrome-profile/` dir is renamed to
`chrome-profiles/默认/`). First run with no config file uses built-in defaults.

To package this as a standalone Windows `.exe` instead, see [PACKAGING.md](PACKAGING.md).

