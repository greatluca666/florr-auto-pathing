# 进游戏 / 到刷怪区域时按键切换装备 — design

## Problem

刷怪需要两套 loadout:赶路时想要移动 / 生存花瓣,到刷怪区想要输出花瓣。现在 bot
全程一套,用户只能手动折中。

florr.io 切 loadout 的方式:

- 按某个数字键 `1`…`0` = 把那个槽位的花瓣在**主行 / 副行**之间对调。把 `1`…`0`
  全按一遍 = 整行主副对调 = 一次换整套。
- 用户也可能给「loadout 预设」绑了单键(实测用户绑的是 `k` / `l`),按一下换一套。

要 bot 在两个时机自动按这些键。

## Goal

[main.py](../../../main.py) 的 `run_worker` 主循环里,每轮:

1. **进游戏后**(点开始 → `_reassert_invert_attack()` 之后,main.py:734 一带):按
   「进游戏切换」这组键。
2. **`lazy_theta_pathing(...)` 返回 `True`**(到达刷怪区)后、`auto_farming(...)`
   之前(main.py:740):按「到区域切换」这组键。

两组键各自可配 `none`(不切换)/ `digits`(按 `1`…`0`)/ `k` / `l`。**每个调度时块
一对**,跟着 `_ACTIVE_KEYS` 走。

## Non-goals

- **不做第三个时机 / 不做"离开区域再换回"。** 只有进游戏 + 到区域两个点。死亡 / 掉线
  下一轮重新进游戏会再按一次「进游戏切换」(florr 重生时本来就把 loadout 拉回账号
  默认,重按是对的)。
- **不校验 florr 里 `k` / `l` 真绑了预设。** 没绑就是按下去游戏内无反应,无害。spec
  不管,README 提一句。
- **不走 CDP 发键。** 跟现有 `pyautogui.press('m')` / `keyUp('space')` 一致,发到前台
  florr 窗口。bot 本来就要求 florr 在前台(所有鼠标转向都靠这个),不引入第二套输入
  通道。
- **不做全局配置 / 不做"全局 + 时块覆盖"。** 就是每时块一对,已跟用户确认。
- **不加按键间隔 / 按住时长的可配项。** `digits` 固定 ~40ms 间隔顺序点按,够用。

## 触发点行为

| 动作 | 触发 | 用途 |
|---|---|---|
| 进游戏切换 | 每轮,进游戏 + `_reassert_invert_attack()` 之后 | 换赶路 loadout |
| 到区域切换 | `lazy_theta_pathing(...)` 返回 `True` 后,`auto_farming` 前 | 换输出 loadout |

- `lazy_theta_pathing` 返回 `False`(没到区域,死亡 / 卡死放弃)→ 到区域切换**不触发**
  (没到就不换,正确)。
- 任何异常 warn-only,不打断这一轮 —— 跟 `_reassert_invert_attack` 一个风格。

## 配置 schema (config.json v2)

时块里新增两个键:

```
"enter_game_swap": "none" | "digits" | "k" | "l"   # 默认 "none"
"reach_area_swap": "none" | "digits" | "k" | "l"   # 默认 "none"
```

[app_config.py](../../../app_config.py) 改动:

- `_ACTIVE_KEYS` — 加这两个键。
- `DEFAULTS`(扁平)— 加 `"enter_game_swap": "none"`, `"reach_area_swap": "none"`。v1
  迁移取值 + 空 schedule 时 `active` 兜底都读这份。
- `_coerce_block` — **宽松**:`raw.get("enter_game_swap", "none")`,值不在
  `{"none","digits","k","l"}` 里 → 回落 `"none"`。**不能**像 `map` / `location` 那样
  值不对就 `return None` 整块丢 —— 旧 config.json 的时块没这两个键,丢了就把用户
  整个调度表清空。
- `_coerce_v1` — 同样的集合校验,不在集合里 → `"none"` + 一句 `⚠️` 警告。
- `_coerce_block` 返回的 dict 里带上这两个键。

[gui_schedule.py](../../../gui_schedule.py) 改动:

- `block_to_active`(那份跟 `_ACTIVE_KEYS` 重复的 7 键投影)— 加这两个键。
- `new_block_template` — 默认 `"none"`。

## `loadout_swap.py`(新)

跟 [florr_settings.py](../../../florr_settings.py) / [server_lookup.py](../../../server_lookup.py)
一样:小、单一职责、不 import GUI、不 import `main`。`press` / `sleep` 通过参数注入,
方便单测。

```python
import time
import pyautogui

_DIGITS = "1234567890"
_VALID = ("none", "digits", "k", "l")


def press_swap(spec, *, press=pyautogui.press, sleep=time.sleep):
    """按一组切换 loadout 的键. spec: none / digits / k / l.

    - none (含 None / "" / 未知值): 什么都不做.
    - k / l: 按一下.
    - digits: 顺序点按 1..0, 每键之间 ~40ms.

    任何异常吞掉 + 打日志, 绝不抛给调用方 —— 切装备是附加动作, 不能打断刷怪轮次.
    """
    if spec not in ("k", "l", "digits"):
        return
    try:
        if spec in ("k", "l"):
            press(spec)
        else:
            for k in _DIGITS:
                press(k)
                sleep(0.04)
    except Exception as e:
        print(f"⚠️ 装备切换按键失败 ({spec}): {e}")
```

## main.py 接线

- `_apply_worker_config` 返回的 dict 加 `"enter_game_swap"` / `"reach_area_swap"`
  (`src.get("enter_game_swap", d["enter_game_swap"])`,`d = app_config.DEFAULTS`)。
- 主循环 `_reassert_invert_attack()` 之后:
  ```python
  loadout_swap.press_swap(w["enter_game_swap"])
  ```
  可选:`overlay.update(message="切换进游戏装备")`(只在非 `none` 时,免得刷屏)。
- 主循环 `if lazy_theta_pathing(location, [farming_area]):` 成功分支内、
  `auto_farming(...)` 之前:
  ```python
  loadout_swap.press_swap(w["reach_area_swap"])
  ```
- 文件顶部 `import loadout_swap`。

## GUI (gui_schedule.py 时块编辑器)

`_autosw`(自动换服务器开关)下面加两个 `CTkOptionMenu`:

| 界面显示 | 存储值 |
|---|---|
| 不切换 | `none` |
| 全部数字键 1–0 | `digits` |
| k | `k` |
| l | `l` |

- `_build`:读 `self._block.get("enter_game_swap", "none")` / `reach_area_swap` 设初值
  (存储值 → 界面显示,反查一张小映射表)。
- `_collect` / 保存那段:两个下拉的界面显示 → 存储值,写进 block dict。
- 各配一个 `_Tooltip` 说明("进游戏时按这组键换 loadout" / "寻路到刷怪区后按这组键
  换 loadout";"全部数字键 = 把 1 到 0 都点一遍,整套主副对调")。

## 测试

- `test_loadout_swap.py`(新):
  - `none` / `None` / `""` / `"zzz"` → `press` 零次调用。
  - `"k"` → `press` 收到 `["k"]`;`"l"` → `["l"]`。
  - `"digits"` → `press` 收到 `["1","2","3","4","5","6","7","8","9","0"]`,`sleep`
    被调用 10 次。
  - `press` 抛异常 → `press_swap` 不抛,吞掉。
- `test_app_config.py`:
  - 新键正常 coerce;非法值 → `"none"`。
  - **旧时块(dict 里没这两个键)仍然合法**,不被 `_coerce_schedule` 丢,结果 dict
    里补上 `"none"`。
  - v1 迁移:迁出来的时块 + `active` 都带 `"enter_game_swap": "none"` 等默认。
- `test_main_worker.py`:monkeypatch `loadout_swap.press_swap`,跑一轮 worker,断言
  两个触发点各按配置里的 spec 调一次;`lazy_theta_pathing` 返 `False` 时到区域切换
  不被调用。
- `test_gui_schedule.py`:`block_to_active` 带新键;编辑器两个下拉 round-trip
  (存储值 → 显示 → 存回,值不变)。

## 已接受的风险(spec 内不解决)

1. florr 未来改数字键 = 槽位对调的语义 → `digits` 行为跟着变。florr 侧机制,不管。
2. `pyautogui.press` 发键时 florr 不在前台 → 键发给别的窗口。bot 全程已假设 florr
   前台(每个 `moveTo` 都靠这个),不额外防。
3. `k` / `l` 在 florr 里没绑预设 → 无反应。README 提示用户去 florr 设置里绑好。
