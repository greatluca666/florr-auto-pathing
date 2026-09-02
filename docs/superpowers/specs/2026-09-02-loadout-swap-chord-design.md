# loadout-swap keys → 开关 + 修饰键和弦 + 单数字 — design

## Problem

刚 ship 的 loadout-swap（merge `5691bdd`）把每个字段做成单个 4 选 1 下拉
`none / digits / k / l`，`digits` = 按遍 `1`…`0`。**跟用户实际要的不一样。**

用户要的：每个字段(进游戏 / 到刷怪区)三个控件 —

- **开关** — 这个字段功能开 / 关
- **下拉 A（修饰键）**：`无` / `k` / `l`
- **下拉 B（数字）**：`1`…`0` 十选一

按键是**和弦**（像 Ctrl+C）：按住修饰键 → 按数字 → 松开修饰键。`无` = 直接按数字。

## Goal

把 `enter_game_swap` / `reach_area_swap` 从字符串改成对象：

```json
{ "enabled": false, "mod": "none", "digit": "1" }
```

`enabled` 打开时，`run_worker` 在原来两个触发点按对应和弦：

- `mod == "none"` → `press(digit)`
- `mod in ("k", "l")` → `keyDown(mod)` → `press(digit)` → `keyUp(mod)`（`keyUp` 走
  `finally`，`press` 抛了也要松开）
- `enabled == false` → 什么都不按

触发点、`swap_this_round = entered_game or round_count == 1` 的门控、warn-only、
走 `pyautogui` 发前台窗口 —— 全部不变。

## Non-goals

- **不迁移旧字符串形式。** `5691bdd` 才合并几分钟，没有真实用户 config。
  `_coerce_*` 遇到非对象 / 键坏 → 回落默认对象 `{enabled:false, mod:"none",
  digit:"1"}`，不试图从 `"k"` / `"digits"` 猜。
- **不做多数字。** 下拉 B 十选一，就一个数字。
- **不加和弦里按键之间的可配延时。** `keyDown`→`press`→`keyUp` 紧挨着。
- 不改触发时机 / 门控 / GUI 其它部分。

## 配置 schema (config.json v2)

`app_config.py`：

- `DEFAULTS["enter_game_swap"]` / `["reach_area_swap"]` = `{"enabled": False,
  "mod": "none", "digit": "1"}`（`copy.deepcopy` 时注意是新 dict）。
- 新常量：`_SWAP_MODS = ("none", "k", "l")`，`_SWAP_DIGITS = tuple("1234567890")`。
- 新模块级 `_coerce_swap_obj(v)` —— 输入任意值，输出规范对象：
  - 非 dict → `{"enabled": False, "mod": "none", "digit": "1"}`
  - `enabled` = `bool(v.get("enabled"))`（非 bool 当 False）
  - `mod` = `v.get("mod")` 若在 `_SWAP_MODS` 否则 `"none"`
  - `digit` = `str(v.get("digit"))` 若在 `_SWAP_DIGITS` 否则 `"1"`
- `_coerce_v1`：`elif key in ("enter_game_swap", "reach_area_swap")` 分支 →
  `cfg[key] = _coerce_swap_obj(raw[key])`；不再走 `ok` 布尔那套（对象总能规范化，
  没有"非法就警告"这一说，坏了就默认）。
- `_coerce_block`：`_swap(key)` = `_coerce_swap_obj(raw.get(key))`；返回 dict 里带上
  两个键。**仍然绝不 `return None`。**
- `DEFAULTS_V2["active"]` 自动带上（`_ACTIVE_KEYS` 不变）。

## `loadout_swap.py`

```python
import time
import pyautogui

_DIGITS = "1234567890"
_MODS = ("k", "l")


def press_swap(cfg, *, press=pyautogui.press,
               key_down=pyautogui.keyDown, key_up=pyautogui.keyUp):
    """按一次 loadout 切换和弦.

    cfg: {"enabled": bool, "mod": "none"|"k"|"l", "digit": "1".."9"|"0"}.
      - 非 dict / enabled 假 → 什么都不做.
      - digit 不在 1..0 → 什么都不做 (+ ⚠️ 日志).
      - mod == "none" → press(digit).
      - mod in (k, l) → key_down(mod); press(digit); key_up(mod)  (keyUp 在 finally).

    任何异常吞掉 + ⚠️ 日志, 绝不外抛 (对齐 _reassert_invert_attack 的 warn-only).
    """
    if not isinstance(cfg, dict) or not cfg.get("enabled"):
        return
    digit = str(cfg.get("digit", ""))
    mod = cfg.get("mod", "none")
    if digit not in _DIGITS:
        print(f"⚠️ 装备切换和弦: 无效数字键 {digit!r}, 跳过")
        return
    try:
        if mod in _MODS:
            key_down(mod)
            try:
                press(digit)
            finally:
                key_up(mod)
        else:
            press(digit)
    except Exception as e:
        print(f"⚠️ 装备切换按键失败 (mod={mod} digit={digit}): {e}")
```

## main.py

- `_apply_worker_config`：`src.get("enter_game_swap", d["enter_game_swap"])` /
  `reach_area_swap` —— 现在值是 dict，直接透传（app_config 已在 load 时 coerce；
  手写调试 cfg 传进来的垃圾由 `press_swap` 的 `isinstance` 挡）。
- 两个调用点 `loadout_swap.press_swap(w["enter_game_swap"])` /
  `press_swap(w["reach_area_swap"])` —— 调用不变，参数变成 dict。
- `swap_this_round` 门控不变。

## GUI (gui_schedule.py)

- 删掉字符串版 `_SWAP_LABELS` / `_SWAP_FROM_LABEL` / `_coerce_swap`。
- 新：`_SWAP_MOD_LABELS = {"none": "无", "k": "k", "l": "l"}` + 反查；数字下拉值
  `list("1234567890")`。
- `_coerce_swap_obj` —— import `app_config._coerce_swap_obj`（单一真源），或本地
  同名薄封装。用 import。
- `block_to_active`：两个键 → `app_config._coerce_swap_obj(block.get(key))`。
- `new_block_template`：两个键 = `{"enabled": False, "mod": "none", "digit": "1"}`。
- `TimeBlockEditor._build`：每个字段一行 —
  `CTkSwitch("进游戏切换装备")` + `CTkOptionMenu(无/k/l)` + `CTkOptionMenu(1..0)`。
  实例属性 `self._enter_swap_on` / `_enter_swap_mod` / `_enter_swap_digit`，reach 同理。
  初值从 `self._block.get(key)` 经 `_coerce_swap_obj` 取。
- `_collect`：两个键 = `{"enabled": bool(sw.get()), "mod":
  _SWAP_MOD_FROM_LABEL.get(menu_a.get(), "none"), "digit": menu_b.get()}`。

## 测试

- `test_loadout_swap.py`（重写）：
  - 非 dict / `enabled:False` → `press` / `key_down` / `key_up` 零调用。
  - `enabled:True, mod:"none", digit:"5"` → `press(["5"])`，无 key_down/up。
  - `enabled:True, mod:"k", digit:"3"` → 调用顺序 `key_down("k")`, `press("3")`,
    `key_up("k")`。
  - `press` 抛异常时 `key_up("k")` 仍被调用（finally），且 `press_swap` 不外抛。
  - `enabled:True, digit:"x"`（或缺 digit）→ 零 press + `⚠️` 日志。
  - `mod` 非法（`"ctrl"`）当 `"none"` 处理 → 只 `press(digit)`。
- `test_app_config.py`：
  - `_coerce_swap_obj`：非 dict → 默认；`enabled` 非 bool → False；`mod` 非法 →
    `"none"`；`digit` 非法 / int `3` → `"3"` 若合法否则 `"1"`。
  - 时块里合法对象 round-trip；旧字符串 `"k"` / `"digits"` → 默认禁用对象，块不丢。
  - v1 迁移：两键都补默认对象；`active` 同。
  - `DEFAULTS["enter_game_swap"]["enabled"] is False` 等。
- `test_gui_schedule.py`：`block_to_active` 两键是规范对象；缺键 → 默认对象；
  `new_block_template` 两键默认对象；`_SWAP_MOD_LABELS` ↔ 反查互逆。
- `test_main_worker.py`：`_stub_run_worker_env` / `_swap_env` 里两个 swap 键改成
  对象（默认 `{"enabled": False, "mod": "none", "digit": "1"}`；`_swap_env` 的
  `enter=` / `reach=` 参数改收对象）。swap 行为测试断言 `press_swap` 收到对应对象。
  门控测试（survived-continuation / real-respawn）逻辑不变。

## 已接受的风险

1. florr 是否把"按住 k + 数字"识别成 loadout 操作 —— 用户 florr 键位配置的事，
   spec 不管；没配好就是按下去没反应。
2. `pyautogui.keyDown` / `keyUp` 发键仍要求 florr 窗口在前台（跟全项目一致）。
3. 真 Windows / live florr 没跑过（仓库惯例）。
