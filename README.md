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

## florr「反转攻击键」

The worker never presses attack — it relies on florr's **Settings → Controls →
"Invert attack button"** being on (so flowers stay open and keep attacking
without a held key). On startup `run_worker` tries to enable it automatically by
writing one byte in florr's WASM memory over CDP (`florr_settings.py`).

That write needs a calibrated address in `florr_settings.INVERT_ATTACK_ADDR`,
which is `None` out of the box. Until it's set — or after florr ships a new
build that moves the byte — the worker just logs a warning (`⚠️ 没能确认 …
反转攻击键`) and keeps farming (it will path and circle correctly but deal no
damage). To (re)calibrate: open the florr.io devtools console, paste
`settings_finder.js`, follow its USAGE header (toggle the checkbox 4–6× calling
`set.mark()` each time, then `set.solve()`), and put the returned address into
`florr_settings.INVERT_ATTACK_ADDR`.

## Implements

Resolution is auto-detected at startup (`pyautogui.size()`) and every screen coordinate is scaled
from its original 1920x1080 calibration — see
`docs/superpowers/specs/2026-08-26-resolution-adaptation-design.md` for how, and its "Known risk"
section for the one thing that isn't verified (non-16:9 windows, if florr.io turns out to letterbox
instead of stretching its UI — recalibrate the affected constant with `debug_screen_pos.py` if so).

Run `python main.py` to open the control-panel GUI.

**时间表 (schedule) page** — the main view is a list of time blocks. Each block
covers a set of weekdays + a start/end time (a block whose start ≥ end crosses
midnight; `00:00–00:00` means all day) and carries: which account, which map, a
target point / farming area (map picker), enemy-detection AI, auto-switch-server,
and — under 高级选项 — farming duration and the consecutive-short-round threshold
(both with hover `?` explanations). Overlapping blocks (shared weekday + time)
are rejected on save. Click **▶ 开始调度** and the GUI drives everything by the
weekly plan: at each block boundary it stops the worker, relaunches Chrome on
that block's account profile (`--start-fullscreen`, opens florr.io) if the
account changed, writes the block's params into `config.json`, and starts a
fresh worker. Gaps in the plan leave the worker stopped. The whole scheduled run
needs zero interaction — the worker clicks the in-game start button and handles
death screens itself.

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

