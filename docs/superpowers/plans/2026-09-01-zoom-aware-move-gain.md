# Zoom-aware Move Gain + Area Leash — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the bot walking out of the farming area after the zoom gate zooms the camera in — scale `move_to_position`'s steering gain by the achieved zoom level for wander legs, and abort a wander leg the instant the player crosses the area boundary.

**Architecture:** Two pure helpers in `main.py` (`_move_gain_for_zoom`, `_leaving_area`); `move_to_position` gains an optional `extend_gain`; `ensure_zoom_for_rarity` returns the achieved HP-bar thickness (`float | None`) instead of `bool`; `auto_farming` takes `zoom_thick`, derives the wander gain, threads it into the wander `move_to_position` call, and its `on_tick` hook returns `"out_of_area"` when the player leaves.

**Tech Stack:** Python 3.11, pytest. No new deps. `from utils import *` provides `if_in_area`, `mouse_scale`, `pyautogui`, `SCREEN_WIDTH/HEIGHT`.

## Global Constraints

- Tests run with `./venv/bin/python -m pytest` (`venv/`, NOT `.venv/`).
- Only `main.py` and `test_main_worker.py` change.
- `move_to_position` currently: `def move_to_position(current_pos, target_pos, max_attempts=200, stall_limit=13, progress_epsilon=1.5, on_tick=None):` and inside, `extend = max(min(dist * 45, 500), 50) * mouse_scale()`.
- `auto_farming` currently: `def auto_farming(farming_area, duration=300, *, enemy_ai_enabled=True):`.
- `_wander_enemy_watch(_pos)` is `auto_farming`'s `on_tick` closure; `farming_area` and `if_in_area` are in its enclosing scope. `if_in_area(areas, point)` takes a **list** of areas: call it `if_in_area([farming_area], pos)`.
- `ensure_zoom_for_rarity` has 6 `return` sites: disabled → `return False`; wait-cap give-up → `return False`; success (`median >= ZOOM_MIN_THICK`) → `return True`; both-directions-regress give-up → `return False`; scroll-cap give-up → `return False`; `except Exception` → `return False`.
- `run_worker` call site (~line 807):
  ```python
          zoom_ok = ensure_zoom_for_rarity(w["enemy_ai_enabled"])
          if w["enemy_ai_enabled"] and not zoom_ok:
              print("⚠️ 视角未调到位, 本轮稀有度识别可能不准 (Mythic 锁定可能不触发)")
          auto_farming(farming_area, farming_duration,
                       enemy_ai_enabled=w["enemy_ai_enabled"])
  ```
- Placeholder tuning constants, use verbatim: `MOVE_EXTEND_GAIN = 45`, `ZOOM_BASELINE_THICK = 2`, gain floor multiplier `0.3`.
- Chinese comments / print strings matching surrounding style. English commit messages, Conventional Commits, trailer `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
- `test_main_worker.py` already has: `import inspect`, `import main`, `monkeypatch` fixtures, `_stub_zoom_env(monkeypatch, thick_seq)` (records `calls["scan"|"scroll"|"sleep"|"moveto"]`, fakes `main.time.time`/`sleep` clock, patches `main.enemy_detect.scan_bar_thickness` / `main.cdp_bridge.scroll_wheel` / `main.pyautogui.moveTo` / `main.overlay` / `main.afk_watch.poll_afk_pause`), and a move-env stub used by `test_move_to_position_*`.

---

## Task 1: `main.py` — gain constants, `_move_gain_for_zoom`, `_leaving_area`, `move_to_position(extend_gain=)`

**Files:**
- Modify: `main.py` — tuning block (after the `ZOOM_WAIT_CAP` line, before `# 以上数值...`); add two helpers next to `_drive_and_check_stall`; `move_to_position` signature + the `extend =` line.
- Test: `test_main_worker.py` — append near the `test_move_to_position_*` / `_stub_zoom_env` tests.

**Interfaces:**
- Consumes: `mouse_scale` (from `utils`), `if_in_area` (from `utils`).
- Produces:
  - `MOVE_EXTEND_GAIN = 45`, `ZOOM_BASELINE_THICK = 2` (module constants).
  - `_move_gain_for_zoom(zoom_thick) -> float` — `None`/≤0 → `MOVE_EXTEND_GAIN`; else `max(MOVE_EXTEND_GAIN * ZOOM_BASELINE_THICK / zoom_thick, MOVE_EXTEND_GAIN * 0.3)`.
  - `_leaving_area(pos, farming_area) -> bool` — `not if_in_area([farming_area], pos)`.
  - `move_to_position(..., extend_gain=None)` — new trailing param; `extend_gain=None` ⇒ uses `MOVE_EXTEND_GAIN` ⇒ behaviour identical to today.

- [ ] **Step 1: Write the failing tests**

Append to `test_main_worker.py`:

```python
def test_move_gain_for_zoom_none_and_nonpositive_give_base():
    assert main._move_gain_for_zoom(None) == main.MOVE_EXTEND_GAIN
    assert main._move_gain_for_zoom(0) == main.MOVE_EXTEND_GAIN
    assert main._move_gain_for_zoom(-3) == main.MOVE_EXTEND_GAIN


def test_move_gain_for_zoom_scales_inversely_with_thickness():
    # thickness == baseline -> scale 1
    assert main._move_gain_for_zoom(main.ZOOM_BASELINE_THICK) == main.MOVE_EXTEND_GAIN
    # thickness 2x baseline -> half gain
    assert main._move_gain_for_zoom(2 * main.ZOOM_BASELINE_THICK) == main.MOVE_EXTEND_GAIN * 0.5


def test_move_gain_for_zoom_has_a_floor():
    # a huge thickness reading must not shrink the gain to ~nothing
    g = main._move_gain_for_zoom(1000)
    assert g == main.MOVE_EXTEND_GAIN * 0.3


def test_leaving_area():
    area = [(7, 3), (52, 63)]
    assert main._leaving_area((25, 20), area) is False
    assert main._leaving_area((25, 120), area) is True
    assert main._leaving_area((60, 20), area) is True


def test_move_to_position_extend_gain_scales_mouse_offset(monkeypatch):
    # same start/target, different extend_gain -> mouse offset from centre scales
    # linearly (pick dist small enough that dist*gain stays under the 500 clamp).
    import types
    monkeypatch.setattr(main, "get_player_position", lambda *a, **k: (0, 0), raising=False)
    monkeypatch.setattr(main, "on_death_screen", lambda: False, raising=False)
    monkeypatch.setattr(main, "on_start_screen", lambda: False, raising=False)
    monkeypatch.setattr(main, "reset_keyboard", lambda: None, raising=False)
    monkeypatch.setattr(main.afk_watch, "poll_afk_pause", lambda: False)
    monkeypatch.setattr(main.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(main, "overlay",
                        types.SimpleNamespace(update=lambda **k: None), raising=False)
    seen = []
    monkeypatch.setattr(main.pyautogui, "moveTo", lambda *a, **k: seen.append(a))

    cx = main.SCREEN_WIDTH // 2
    # target 10 units to the +x of the player at (0,0); dist = 10.
    # run one tick each: on the first tick last_dist is None so no arrival/stall
    # short-circuit; on_tick returns a sentinel to stop after that tick.
    def one_tick(_pos):
        return "stop"

    seen.clear()
    main.move_to_position((0, 0), (10, 0), max_attempts=5, on_tick=one_tick, extend_gain=10)
    off_a = seen[0][0] - cx        # x offset from centre, gain 10 -> dist*gain = 100

    seen.clear()
    main.move_to_position((0, 0), (10, 0), max_attempts=5, on_tick=one_tick, extend_gain=40)
    off_b = seen[0][0] - cx        # gain 40 -> dist*gain = 400

    assert off_b == pytest.approx(off_a * 4)


def test_move_to_position_extend_gain_none_matches_base_constant(monkeypatch):
    import types
    for stub in ("get_player_position", "on_death_screen", "on_start_screen", "reset_keyboard"):
        pass
    monkeypatch.setattr(main, "get_player_position", lambda *a, **k: (0, 0), raising=False)
    monkeypatch.setattr(main, "on_death_screen", lambda: False, raising=False)
    monkeypatch.setattr(main, "on_start_screen", lambda: False, raising=False)
    monkeypatch.setattr(main, "reset_keyboard", lambda: None, raising=False)
    monkeypatch.setattr(main.afk_watch, "poll_afk_pause", lambda: False)
    monkeypatch.setattr(main.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(main, "overlay",
                        types.SimpleNamespace(update=lambda **k: None), raising=False)
    seen = []
    monkeypatch.setattr(main.pyautogui, "moveTo", lambda *a, **k: seen.append(a))

    def one_tick(_pos):
        return "stop"

    seen.clear()
    main.move_to_position((0, 0), (10, 0), max_attempts=5, on_tick=one_tick, extend_gain=None)
    off_none = seen[0][0]
    seen.clear()
    main.move_to_position((0, 0), (10, 0), max_attempts=5, on_tick=one_tick,
                          extend_gain=main.MOVE_EXTEND_GAIN)
    off_const = seen[0][0]
    assert off_none == off_const
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest test_main_worker.py -k "move_gain_for_zoom or leaving_area or extend_gain" -v`
Expected: FAIL — `AttributeError: module 'main' has no attribute '_move_gain_for_zoom'` etc., and `move_to_position() got an unexpected keyword argument 'extend_gain'`.

- [ ] **Step 3: Add the constants**

In `main.py`, in the tuning block, after `ZOOM_WAIT_CAP      = 60   # ...` and before `# 以上数值是没实机测过的占位默认值`:

```python
MOVE_EXTEND_GAIN    = 45  # move_to_position: minimap距离→鼠标px 增益 (基准, 没缩放相机时调的)
ZOOM_BASELINE_THICK = 2   # MOVE_EXTEND_GAIN 是在血条厚度≈这个值时调的; 相机拉近后按 1/厚度 缩
```

- [ ] **Step 4: Add the two helpers**

In `main.py`, right after `_drive_and_check_stall` (ends `return "moved"`) and before `def ensure_zoom_for_rarity`:

```python
def _move_gain_for_zoom(zoom_thick):
    """wander 腿的转向增益. 相机拉得越近 (血条越厚), 同样鼠标偏移玩家动得越多,
    增益要按比例调小 —— 不然每条腿冲过头, 净漂移一次就甩出刷怪区 (实测).
    模型: gain ∝ 1/厚度, 以 ZOOM_BASELINE_THICK 归一. zoom_thick=None (没测到 zoom /
    没缩放) → 用基准. 夹一个下限, 免得某次厚度读特别大把移动缩到几乎不动."""
    if zoom_thick is None or zoom_thick <= 0:
        return MOVE_EXTEND_GAIN
    scaled = MOVE_EXTEND_GAIN * ZOOM_BASELINE_THICK / zoom_thick
    return max(scaled, MOVE_EXTEND_GAIN * 0.3)


def _leaving_area(pos, farming_area):
    """玩家是不是已经出了刷怪区. _wander_enemy_watch 用它当"硬牵引": wander 腿途中
    一出界就收手, 让外层从出界 ~1 格处重寻路, 而不是等这条腿走完 (可能已经甩出 60 格)."""
    return not if_in_area([farming_area], pos)
```

- [ ] **Step 5: Wire `extend_gain` into `move_to_position`**

Signature — add the trailing param:

```python
def move_to_position(current_pos, target_pos, max_attempts=200, stall_limit=13,
                     progress_epsilon=1.5, on_tick=None, extend_gain=None):
```

Inside, replace:

```python
        extend = max(min(dist * 45, 500), 50) * mouse_scale()
```

with:

```python
        gain = extend_gain if extend_gain is not None else MOVE_EXTEND_GAIN
        extend = max(min(dist * gain, 500), 50) * mouse_scale()
```

- [ ] **Step 6: Run tests**

Run: `./venv/bin/python -m pytest test_main_worker.py -q`
Expected: PASS — the 6 new tests + the existing `test_move_to_position_*` (unchanged behaviour with `extend_gain=None`).

- [ ] **Step 7: Commit**

```bash
git add main.py test_main_worker.py
git commit -m "feat: move_to_position extend_gain + _move_gain_for_zoom + _leaving_area

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: `main.py` — `ensure_zoom_for_rarity` returns thickness; `run_worker` warn condition

**Files:**
- Modify: `main.py` — `ensure_zoom_for_rarity` (6 `return` sites + docstring line); `run_worker` call site (var rename + warn condition). Do **not** touch the `auto_farming(...)` call yet (Task 3).
- Test: `test_main_worker.py` — update the 12 `test_ensure_zoom_*` assertions.

**Interfaces:**
- Produces: `ensure_zoom_for_rarity(enemy_ai_enabled) -> float | None`. Success (`median >= ZOOM_MIN_THICK`) → the `median` float. Every give-up path (disabled / wait-cap / scroll-cap / both-regress / `except`) → `None`.
- `run_worker`: local var is now `zoom_thick`; warn when `w["enemy_ai_enabled"] and zoom_thick is None`.

- [ ] **Step 1: Update the failing tests**

In `test_main_worker.py`, in the 12 `test_ensure_zoom_*` functions:

- Success tests — replace `assert main.ensure_zoom_for_rarity(True) is True` with the median of that test's `thick_seq` last entry:
  - `test_ensure_zoom_reaches_target` (`[2,2],[3,3],[4,4]`): `assert main.ensure_zoom_for_rarity(True) == 4.0`
  - `test_ensure_zoom_already_ok_no_scroll` (`[[5,6]]`): `assert main.ensure_zoom_for_rarity(True) == 5.5`
  - `test_ensure_zoom_waits_for_mobs_then_succeeds` (`[],[],[4,4]`): `== 4.0`
  - `test_ensure_zoom_flips_scroll_direction` (`[3,3],[2,2],[4,4]`): `== 4.0`
  - `test_ensure_zoom_success_does_not_restore` (`[2,2],[4,4]`): `== 4.0`
- Give-up tests — replace `is True`/`is False` with `is None`:
  - `test_ensure_zoom_disabled_returns_immediately`: `assert main.ensure_zoom_for_rarity(False) is None`
  - `test_ensure_zoom_scroll_cap`: `... is None`
  - `test_ensure_zoom_wait_cap`: `... is None`
  - `test_ensure_zoom_bails_and_restores_when_both_directions_regress`: `... is None`
  - `test_ensure_zoom_restores_on_scroll_cap`: `... is None`
  - `test_ensure_zoom_wait_cap_bounds_afk`: `... is None`
  - `test_ensure_zoom_recenters_mouse_on_entry`: `... is None`

Leave every other line in those tests (scroll counts, sleep, moveto) unchanged.

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest test_main_worker.py -k ensure_zoom -q`
Expected: FAIL — success tests still get `True` (not `4.0`), give-up tests still get `False` (not `None`).

- [ ] **Step 3: Change `ensure_zoom_for_rarity`'s returns**

In `main.py`:
- The success branch:
  ```python
              if median >= ZOOM_MIN_THICK:
                  print(f"✅ 视角OK (血条中位厚度 {median})")
                  return median
  ```
- Every other `return False` inside `ensure_zoom_for_rarity` (the disabled guard, the wait-cap give-up, the both-directions-regress give-up, the scroll-cap give-up, and the `except Exception` handler) → `return None`.
- Docstring last line: change `返回是否达到目标厚度; 调用方 (run_worker) 只打日志` to `返回达成的血条中位厚度 (float); 任何没调到位的情况返回 None. 调用方 (run_worker) 打日志并把它传给 auto_farming 当转向增益的缩放依据`.

- [ ] **Step 4: Change the `run_worker` call site**

Replace:

```python
        zoom_ok = ensure_zoom_for_rarity(w["enemy_ai_enabled"])
        if w["enemy_ai_enabled"] and not zoom_ok:
            print("⚠️ 视角未调到位, 本轮稀有度识别可能不准 (Mythic 锁定可能不触发)")
```

with:

```python
        zoom_thick = ensure_zoom_for_rarity(w["enemy_ai_enabled"])
        if w["enemy_ai_enabled"] and zoom_thick is None:
            print("⚠️ 视角未调到位, 本轮稀有度识别可能不准 (Mythic 锁定可能不触发)")
```

(The `auto_farming(...)` call two lines below stays exactly as it is for now — Task 3 changes it.)

- [ ] **Step 5: Run tests**

Run: `./venv/bin/python -m pytest test_main_worker.py -q`
Expected: PASS (12 updated + everything else).

Run: `./venv/bin/python -m pytest -q`
Expected: PASS — whole suite.

- [ ] **Step 6: Commit**

```bash
git add main.py test_main_worker.py
git commit -m "feat: ensure_zoom_for_rarity returns the achieved HP-bar thickness

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: `main.py` — `auto_farming` zoom-aware gain + area leash

**Files:**
- Modify: `main.py` — `auto_farming` signature + `move_gain` derivation + log; the wander `move_to_position` call; `_wander_enemy_watch`; the wander-branch `move_result` check; `run_worker` `auto_farming(...)` call.
- Test: `test_main_worker.py` — one signature test.

**Interfaces:**
- Consumes: `_move_gain_for_zoom` / `_leaving_area` (Task 1), `ensure_zoom_for_rarity -> float | None` (Task 2).
- Produces: `auto_farming(farming_area, duration=300, *, enemy_ai_enabled=True, zoom_thick=None)`. `_wander_enemy_watch` now also returns `"out_of_area"`. The wander `move_to_position` call uses `extend_gain=move_gain`.

- [ ] **Step 1: Write the failing test**

Append to `test_main_worker.py`:

```python
def test_auto_farming_accepts_zoom_thick_kwarg():
    sig = inspect.signature(main.auto_farming)
    assert "zoom_thick" in sig.parameters
    assert sig.parameters["zoom_thick"].kind == inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["zoom_thick"].default is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest test_main_worker.py::test_auto_farming_accepts_zoom_thick_kwarg -v`
Expected: FAIL — `zoom_thick` not in the signature.

- [ ] **Step 3: `auto_farming` signature + `move_gain`**

Signature:

```python
def auto_farming(farming_area, duration=300, *, enemy_ai_enabled=True, zoom_thick=None):
```

Just after `farming_area` is normalised to `[(min_x, min_y), (max_x, max_y)]` and `binary_map = load_binary_map()` (before the `print("\n🎮 开始在区域 ...")`), add:

```python
    move_gain = _move_gain_for_zoom(zoom_thick)
    print(f"🎯 wander 转向增益: {move_gain:.1f} (zoom 厚度 {zoom_thick})")
```

- [ ] **Step 4: Area leash in `_wander_enemy_watch`**

Change its parameter `_pos` → `pos`, and make the first statement in the body (before `nonlocal` / the enemy scan):

```python
    def _wander_enemy_watch(pos):
        """... (keep the existing docstring; add one line:) 还兼硬牵引: 玩家一出刷怪区
        就返回 "out_of_area" 收手, 别等这条腿走完 (拉近相机后一条腿可能甩出 60 格)."""
        if _leaving_area(pos, farming_area):
            return "out_of_area"
        nonlocal enemy_decision, detections, last_enemy_scan
        ...
```

- [ ] **Step 5: Wander `move_to_position` call + `move_result` check**

The wander call becomes:

```python
        move_result = move_to_position(current_pos, (random_x, random_y),
                                       max_attempts=20, on_tick=_wander_enemy_watch,
                                       extend_gain=move_gain)
```

And the handling of its result — change:

```python
        if move_result == "enemy":
            # 路途中扫到怪(该 flee/chase/锁 Mythic) —— 立刻回外层, 下个 tick 用
            # _wander_enemy_watch 刚更新的 enemy_decision 处理, 不算走完一趟.
            continue
```

to:

```python
        if move_result in ("enemy", "out_of_area"):
            # enemy: 途中扫到该 flee/chase/锁 Mythic 的怪. out_of_area: 途中出了刷怪区.
            # 两种都立刻回外层 —— 外层自己的索敌 / if_in_area 检查接手 (out_of_area 时
            # 从出界 ~1 格处重寻路, 不是等甩出 60 格). 都不算走完一趟.
            continue
```

- [ ] **Step 6: `run_worker` passes `zoom_thick`**

The `auto_farming(...)` call in `run_worker`:

```python
            auto_farming(farming_area, farming_duration,
                         enemy_ai_enabled=w["enemy_ai_enabled"], zoom_thick=zoom_thick)
```

- [ ] **Step 7: Compile + full suite**

Run: `./venv/bin/python -m py_compile main.py`
Run: `./venv/bin/python -m pytest -q`
Expected: PASS — whole suite, no regressions.

- [ ] **Step 8: Diagnostic smoke**

Run:
```bash
./venv/bin/python -c "
import main
print('gain none :', main._move_gain_for_zoom(None))
print('gain 4.0  :', main._move_gain_for_zoom(4.0))
print('gain 20   :', main._move_gain_for_zoom(20))
print('leaving   :', main._leaving_area((25,120), [(7,3),(52,63)]))
import inspect; print('af sig    :', inspect.signature(main.auto_farming))
"
```
Expected: `gain none : 45`, `gain 4.0 : 22.5`, `gain 20 : 13.5`, `leaving : True`, and the signature shows `zoom_thick=None`.

- [ ] **Step 9: Commit + push**

```bash
git add main.py test_main_worker.py
git commit -m "feat: zoom-aware wander gain + mid-leg area leash in auto_farming

auto_farming takes zoom_thick from ensure_zoom_for_rarity and scales the
wander-leg move_to_position gain by 1/thickness (via _move_gain_for_zoom),
so a zoomed-in camera no longer overshoots every leg. _wander_enemy_watch
also returns \"out_of_area\" the moment the player crosses the boundary,
so a single overshoot can't run ~60 units out before the outer loop
repaths. execute_path / lazy_theta_pathing legs are untouched (extend_gain
defaults to MOVE_EXTEND_GAIN = the old 45).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
git push origin main
```

---

## Self-Review

**1. Spec coverage:**

| Spec item | Task |
|---|---|
| `ensure_zoom_for_rarity -> float \| None` (median on success, None on every give-up) | Task 2 Step 3 |
| `run_worker` var `zoom_thick`, warn on `is None`, pass to `auto_farming` | Task 2 Step 4 (rename + warn) + Task 3 Step 6 (passthrough) |
| `_move_gain_for_zoom` (None/≤0 → base; `1/thick` model normalised to baseline; `0.3` floor) | Task 1 Step 4 |
| `MOVE_EXTEND_GAIN = 45`, `ZOOM_BASELINE_THICK = 2` in tuning block | Task 1 Step 3 |
| `move_to_position(extend_gain=None)`, `45` → `MOVE_EXTEND_GAIN`, default unchanged | Task 1 Step 5 |
| `auto_farming(zoom_thick=None)`, `move_gain` computed once + logged, wander call passes `extend_gain=move_gain`, no other call changed | Task 3 Steps 3, 5, 6 |
| `_wander_enemy_watch` `_pos`→`pos` + `_leaving_area` → `"out_of_area"` | Task 1 Step 4 (`_leaving_area`) + Task 3 Step 4 |
| wander branch `move_result in ("enemy", "out_of_area")` | Task 3 Step 5 |
| `execute_path` / `lazy_theta_pathing` legs keep old gain | Task 1 Step 5 (`extend_gain=None` default) — those calls are untouched |
| Tests: `_move_gain_for_zoom`, `_leaving_area`, `move_to_position` gain wiring, `ensure_zoom` return-type, `auto_farming` sig | Task 1 Step 1, Task 2 Step 1, Task 3 Step 1 |

No gaps.

**2. Placeholder scan:** No "TBD"/"handle errors"/"similar to Task N". Every step has literal code. The one loose spot in the spec ("if any test covers the run_worker warn line") is resolved by Task 3's explicit `test_auto_farming_accepts_zoom_thick_kwarg` + the existing suite; no run_worker warn-line test is added because that line's behaviour (print vs not) is not independently observable without a full `run_worker` harness, and the condition change is mechanical.

**3. Type consistency:**
- `_move_gain_for_zoom(zoom_thick) -> float` — same call in Task 1 tests, Task 1 impl, Task 3 Step 3 (`move_gain = _move_gain_for_zoom(zoom_thick)`).
- `_leaving_area(pos, farming_area) -> bool` — same in Task 1 tests, Task 1 impl, Task 3 Step 4 (`if _leaving_area(pos, farming_area):`).
- `move_to_position(..., extend_gain=None)` — trailing kwarg in Task 1 impl; Task 3's wander call passes `extend_gain=move_gain`; all other existing calls omit it (default `None`).
- `ensure_zoom_for_rarity -> float | None` — Task 2 impl; Task 2 `run_worker` checks `zoom_thick is None`; Task 3 passes `zoom_thick=zoom_thick` to `auto_farming(zoom_thick=None)`.
- `auto_farming(..., zoom_thick=None)` — Task 3 sig; Task 3 `run_worker` call; Task 3 sig test.

No inconsistencies.
