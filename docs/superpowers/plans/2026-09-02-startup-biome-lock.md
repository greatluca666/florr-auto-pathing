# Startup Biome-Lock + GUI-Disable ocean/anthell — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every time the worker is back at florr's start screen, force the Chrome client onto a server for the `config.json`-configured biome via CDP `cp6.forceServerID`, so pathing's map always matches the biome the player actually spawns in; and grey out ocean/anthell in the schedule editor.

**Architecture:** florr does not remember the last-picked biome, so `click_start_game()` drops the player into florr's default (usually Garden) while pathing uses `maps/<configured>.png`. Reuse the already-proven `utils.switch_server(biome)` (fetches a live server code for a biome, runs `cp6.forceServerID` over CDP → reconnect) as a biome lock: call it once before the worker's main loop and once inside the loop right after `click_start_game()`. Warn-only on failure, retry a few times first — same posture as `_reassert_invert_attack`. Separately, the schedule editor's map picker becomes radio buttons with ocean/anthell disabled, gated by one new `app_config` constant.

**Tech Stack:** Python 3.11, pytest, CustomTkinter (GUI), CDP-over-WebSocket (`cdp_bridge`), `pyautogui` (unrelated here).

## Global Constraints

- Small single-purpose modules; no GUI import and no `import main` from `server_lookup.py` / worker helpers. Inject dependencies for testability (repo pattern: `florr_settings.py`, `server_lookup.py`).
- `app_config._VALID_MAPS` MUST stay `("desert", "ocean", "anthell")` — `test_app_config.py` exercises `map="ocean"` surviving coercion, and old `config.json` blocks with ocean must still load and run. The GUI restriction is a separate constant.
- Biome-lock failure is **warn-only**: log + let the worker proceed into the game. Never `sys.exit`, never raise out of `_lock_biome`. Same as `_reassert_invert_attack`.
- `switch_server(biome)` takes a `server_lookup.BIOME_INDEX` key (`desert`/`ocean`/`ant_hell`/…), NOT a `config.json` map name. `anthell` (config) ≠ `ant_hell` (index) — translate.
- Do not touch `on_guest_screen` / `click_play_as_guest` / `loadout_swap` — different branches/specs. Biome-lock wiring sits after those, around `_reassert_invert_attack`.
- Chinese comments/log strings to match surrounding code style.
- Run the full suite with `python -m pytest -q` from the repo root (uses `venv/`).

---

### Task 1: `server_lookup.biome_key_for_map` — config map name → biome index key

**Files:**
- Modify: `server_lookup.py` (append after `BIOME_INDEX`, ~line 41)
- Test: `test_server_lookup.py`

**Interfaces:**
- Consumes: nothing (leaf).
- Produces: `server_lookup.biome_key_for_map(map_name: str) -> str` — returns one of `"desert"`, `"ocean"`, `"ant_hell"`; unknown input returns `"desert"`. Also module-level `server_lookup._MAP_TO_BIOME: dict[str, str]`.

- [ ] **Step 1: Write the failing test**

Add to `test_server_lookup.py`:

```python
from server_lookup import biome_key_for_map


@pytest.mark.parametrize("map_name, expected", [
    ("desert", "desert"),
    ("ocean", "ocean"),
    ("anthell", "ant_hell"),      # config 用 anthell, 接口 key 是 ant_hell
    ("garden", "desert"),         # 不是 config 里会出现的 map —— 回退
    ("", "desert"),
])
def test_biome_key_for_map(map_name, expected):
    assert biome_key_for_map(map_name) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_server_lookup.py::test_biome_key_for_map -q`
Expected: FAIL — `ImportError: cannot import name 'biome_key_for_map'`.

- [ ] **Step 3: Write minimal implementation**

Append to `server_lookup.py` after the `BIOME_INDEX` dict (after line 41):

```python
# config.json 的 map 名 -> 本模块 BIOME_INDEX 的 key. 目前只差 anthell/ant_hell
# 这一个不一致; desert/ocean 一模一样. 未知名回退 desert —— 调用方
# (main._apply_worker_config) 已保证传的是 app_config._VALID_MAPS 之一, 这里只是
# 多一层不炸.
_MAP_TO_BIOME = {"desert": "desert", "ocean": "ocean", "anthell": "ant_hell"}


def biome_key_for_map(map_name):
    """把 config.json 的 map 名翻成 fetch_server_ids() / BIOME_INDEX 认的生态区 key."""
    return _MAP_TO_BIOME.get(map_name, "desert")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_server_lookup.py -q`
Expected: PASS (new test + the existing 4 stay green).

- [ ] **Step 5: Commit**

```bash
git add server_lookup.py test_server_lookup.py
git commit -m "feat(server_lookup): biome_key_for_map — config map name to biome index key"
```

---

### Task 2: `app_config._GUI_ENABLED_MAPS` — the "which maps can be picked in the GUI" switch

**Files:**
- Modify: `app_config.py:36` area (right after `_VALID_MAPS`)
- Test: `test_app_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `app_config._GUI_ENABLED_MAPS: tuple[str, ...]` == `("desert",)`. `_VALID_MAPS` is unchanged.

- [ ] **Step 1: Write the failing test**

Add to `test_app_config.py` (top-level function, alongside the others):

```python
def test_gui_enabled_maps_is_desert_only_but_valid_maps_untouched():
    # GUI 里暂时只让选沙漠; coerce 层仍认全部 3 个(旧 ocean 时块不被丢).
    assert app_config._GUI_ENABLED_MAPS == ("desert",)
    assert app_config._VALID_MAPS == ("desert", "ocean", "anthell")
    assert set(app_config._GUI_ENABLED_MAPS).issubset(app_config._VALID_MAPS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_app_config.py::test_gui_enabled_maps_is_desert_only_but_valid_maps_untouched -q`
Expected: FAIL — `AttributeError: module 'app_config' has no attribute '_GUI_ENABLED_MAPS'`.

- [ ] **Step 3: Write minimal implementation**

In `app_config.py`, immediately after the `_VALID_MAPS = (...)` line and its comment (line 36):

```python
# GUI 时块编辑器里实际可选的地图. 不在这里的 (ocean / anthell) 在界面上置灰、
# 标「暂不可用」. 「暂时」措施: 索敌 canvas decode 只做了沙漠、启动锁生态区也只
# 在沙漠上验证过. 以后放开 ocean 只需把它加回这个元组 —— coerce 层 (_VALID_MAPS)
# 不受影响, 手写 config.json / 旧时块里的 ocean 仍能跑.
_GUI_ENABLED_MAPS = ("desert",)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_app_config.py -q`
Expected: PASS (new test + all existing app_config tests, including the `map="ocean"` coercion ones, stay green).

- [ ] **Step 5: Commit**

```bash
git add app_config.py test_app_config.py
git commit -m "feat(app_config): _GUI_ENABLED_MAPS — desert-only GUI map picker gate"
```

---

### Task 3: `gui_schedule` pure logic — reject disabled map on save + radio-state helper

**Files:**
- Modify: `gui_schedule.py` — `validate_block` (~line 50-70), add `_map_radio_state` helper near `new_block_template`
- Test: `test_gui_schedule.py`

**Interfaces:**
- Consumes: `app_config._GUI_ENABLED_MAPS` (Task 2).
- Produces:
  - `gui_schedule._map_radio_state(map_name: str) -> str` — `"normal"` if `map_name in app_config._GUI_ENABLED_MAPS` else `"disabled"`.
  - `validate_block(block, others)` now also returns the string `"海洋 / 蚁狱暂不可用, 请选沙漠"` when `block["map"]` is not in `app_config._GUI_ENABLED_MAPS`.

- [ ] **Step 1: Write the failing tests**

Add to `test_gui_schedule.py`:

```python
def test_map_radio_state():
    assert gs._map_radio_state("desert") == "normal"
    assert gs._map_radio_state("ocean") == "disabled"
    assert gs._map_radio_state("anthell") == "disabled"


def test_validate_rejects_disabled_map():
    msg = gs.validate_block(_blk(map="ocean"), [])
    assert msg is not None and "暂不可用" in msg


def test_validate_accepts_desert_map():
    # _blk() 默认 map="desert" —— 已被 test_validate_ok 覆盖, 这里显式再钉一次
    assert gs.validate_block(_blk(map="desert"), []) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test_gui_schedule.py::test_map_radio_state test_gui_schedule.py::test_validate_rejects_disabled_map -q`
Expected: FAIL — `AttributeError: module 'gui_schedule' has no attribute '_map_radio_state'`, and `test_validate_rejects_disabled_map` fails because `validate_block(_blk(map="ocean"), [])` currently returns `None`.

- [ ] **Step 3: Write minimal implementation**

In `gui_schedule.py`, add the helper just above `new_block_template` (~line 72):

```python
def _map_radio_state(map_name):
    """时块编辑器地图 radio 的 tk state: 不在 _GUI_ENABLED_MAPS 里的置灰."""
    return "normal" if map_name in app_config._GUI_ENABLED_MAPS else "disabled"
```

In `validate_block`, add the check right after the equal-times check (after line 58, before the `location` check on line 59):

```python
    if block.get("map") not in app_config._GUI_ENABLED_MAPS:
        return "海洋 / 蚁狱暂不可用, 请选沙漠"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test_gui_schedule.py -q`
Expected: PASS. All existing `test_validate_*` stay green (they use `_blk()` which defaults `map="desert"`; `test_block_to_active_shapes` tests `block_to_active`, not `validate_block`, so its `map="ocean"` is unaffected).

- [ ] **Step 5: Commit**

```bash
git add gui_schedule.py test_gui_schedule.py
git commit -m "feat(gui_schedule): reject ocean/anthell on save + _map_radio_state helper"
```

---

### Task 4: `gui_schedule.TimeBlockEditor` — map OptionMenu → radio buttons (ocean/anthell greyed) + README note

**Files:**
- Modify: `gui_schedule.py` — `TimeBlockEditor._build` (lines 194-197), `_on_map_change` (lines 299-304)
- Modify: `README.md` (schedule-page paragraph, ~line 66-78)
- Test: none automated (repo has zero CTk-widget tests — no Tk root in the suite). Verification = import check + full suite still green + manual GUI note below.

**Interfaces:**
- Consumes: `gui_schedule._map_radio_state` (Task 3), `app_config._VALID_MAPS`.
- Produces: `self._map` is now a `tk.StringVar` (was a `CTkOptionMenu`). `self._map.get()` / `self._map.set(...)` keep the same call sites in `_build` (line 203) and `_collect` (line 323) working unchanged. `_on_map_change` now takes no positional value arg.

- [ ] **Step 1: Replace the OptionMenu with a radio row in `_build`**

In `gui_schedule.py`, replace lines 194-197:

```python
        self._map = ctk.CTkOptionMenu(self, values=list(app_config._VALID_MAPS),
                                      command=self._on_map_change)
        self._map.set(self._block.get("map", "desert"))
        self._map.pack(anchor="w", padx=12, pady=4)
```

with:

```python
        self._map = tk.StringVar(value=self._block.get("map", "desert"))
        map_row = ctk.CTkFrame(self, fg_color="transparent")
        map_row.pack(anchor="w", padx=12, pady=4)
        _MAP_LABELS = {"desert": "沙漠", "ocean": "海洋", "anthell": "蚁狱"}
        for m in app_config._VALID_MAPS:
            state = _map_radio_state(m)
            text = _MAP_LABELS.get(m, m)
            if state == "disabled":
                text += "(暂不可用)"
            ctk.CTkRadioButton(map_row, text=text, variable=self._map, value=m,
                               state=state, command=self._on_map_change).pack(
                side="left", padx=(0, 10))
```

`tk` is already imported (`import tkinter as tk`, line 7). Note the existing `_picker.load_map(self._map.get())` on line 203 still works (`StringVar.get()`).

- [ ] **Step 2: Fix `_on_map_change` to take no value arg**

Replace lines 299-304:

```python
    def _on_map_change(self, name):
        self._point = None
        self._area = None
        self._picker.load_map(name)
        self._picker.set_point(None)
        self._picker.set_area(None)
```

with:

```python
    def _on_map_change(self):
        # CTkRadioButton 的 command 不带值, 从 StringVar 自己读.
        self._point = None
        self._area = None
        self._picker.load_map(self._map.get())
        self._picker.set_point(None)
        self._picker.set_area(None)
```

`_collect` (line 323, `map=self._map.get()`) is unchanged — `StringVar.get()` has the same interface as the old `CTkOptionMenu.get()`.

- [ ] **Step 3: Import-check + full suite**

Run: `python -c "import gui_schedule"`
Expected: no error.

Run: `python -m pytest -q`
Expected: PASS — whole suite green. `test_gui_schedule.py` never instantiates `TimeBlockEditor`, so the widget swap doesn't touch it; the pure functions (`validate_block`, `_map_radio_state`, `block_to_active`, `new_block_template`) are unaffected.

- [ ] **Step 4: Manual GUI check (document result in the commit body)**

`python main.py` → schedule page → add / edit a time block. Confirm: three map choices shown as radios; 沙漠 selectable; 海洋(暂不可用) / 蚁狱(暂不可用) greyed and unclickable; picking 沙漠 still loads `maps/desert.png` in the picker and clears any point/area. Open an existing block (if any) whose map is `ocean` → radio sits on the greyed 海洋; hitting 保存 shows the red "海洋 / 蚁狱暂不可用, 请选沙漠" and does not save until 沙漠 is picked.

(If no Windows/GUI box is handy this session, note "manual GUI check deferred" in the commit body — consistent with prior GUI-phase commits.)

- [ ] **Step 5: README note**

In `README.md`, in the schedule-page paragraph, change "which account, which map, a target point" (line 68) to:

```
which account, which map (海洋 / 蚁狱 are greyed out for now — desert only), a target point
```

and change the last sentence (line 77-78) "the worker clicks the in-game start button and handles death screens itself." to:

```
the worker clicks the in-game start button, handles death screens itself, and
re-locks the florr server to the block's biome every time it's back at the start
screen (florr doesn't remember the last-picked biome).
```

- [ ] **Step 6: Commit**

```bash
git add gui_schedule.py README.md
git commit -m "feat(gui_schedule): map picker as radio buttons, ocean/anthell greyed out"
```

---

### Task 5: `main._apply_worker_config` — expose the configured biome key

**Files:**
- Modify: `main.py` — imports (line 1-13), `_apply_worker_config` (lines 646-665)
- Test: `test_main_worker.py`

**Interfaces:**
- Consumes: `server_lookup.biome_key_for_map` (Task 1).
- Produces: the dict returned by `main._apply_worker_config(cfg)` now has key `"biome"` — the `server_lookup` biome key for `cfg`'s active map (`"desert"` / `"ocean"` / `"ant_hell"`).

- [ ] **Step 1: Write the failing tests**

Add to `test_main_worker.py`:

```python
def test_apply_worker_config_maps_config_map_to_biome_key(monkeypatch):
    monkeypatch.setattr(main, "apply_map", lambda name: None)
    w = main._apply_worker_config({"version": 2, "active": {"map": "anthell"}})
    assert w["biome"] == "ant_hell"        # config anthell -> index key ant_hell
    w2 = main._apply_worker_config({"version": 2, "active": {"map": "ocean"}})
    assert w2["biome"] == "ocean"
    w3 = main._apply_worker_config({"version": 2, "active": {"map": "desert"}})
    assert w3["biome"] == "desert"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_main_worker.py::test_apply_worker_config_maps_config_map_to_biome_key -q`
Expected: FAIL — `KeyError: 'biome'`.

- [ ] **Step 3: Write minimal implementation**

In `main.py`, add to the import block (after line 12, `import app_config`):

```python
import server_lookup
```

In `_apply_worker_config`, add one entry to the returned dict (after the `"auto_switch_server"` line, line 664):

```python
        "biome": server_lookup.biome_key_for_map(src.get("map", d["map"])),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test_main_worker.py -q`
Expected: PASS — new test green; existing `test_apply_worker_config_*` still green (they don't assert dict equality, only individual keys).

- [ ] **Step 5: Commit**

```bash
git add main.py test_main_worker.py
git commit -m "feat(main): _apply_worker_config exposes biome key for the active map"
```

---

### Task 6: `main._lock_biome` — retrying, warn-only biome lock

**Files:**
- Modify: `main.py` — add function + constants next to `_reassert_invert_attack` (~line 668-679)
- Test: `test_main_worker.py`

**Interfaces:**
- Consumes: `switch_server` (already in `main`'s namespace via `from utils import *`; signature `switch_server(biome="desert") -> str`), `time.sleep`.
- Produces: `main._lock_biome(biome: str) -> bool` — calls `switch_server(biome)`; retries up to `main._BIOME_LOCK_RETRIES` times with `main._BIOME_LOCK_RETRY_SLEEP`s between attempts; on the first success sleeps `main._BIOME_RECONNECT_SLEEP`s and returns `True`; if every attempt raises, logs and returns `False` (never raises). Also module-level ints/floats `_BIOME_LOCK_RETRIES` (3), `_BIOME_LOCK_RETRY_SLEEP` (3.0), `_BIOME_RECONNECT_SLEEP` (3.0). Does NOT touch `overlay` — caller does.

- [ ] **Step 1: Write the failing tests**

Add to `test_main_worker.py`:

```python
def test_lock_biome_success_first_try(monkeypatch):
    seen = []
    monkeypatch.setattr(main, "switch_server", lambda b: seen.append(b) or "srv-1")
    monkeypatch.setattr(main.time, "sleep", lambda *a, **k: None)
    assert main._lock_biome("ocean") is True
    assert seen == ["ocean"]


def test_lock_biome_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky(b):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("cdp boom")
        return "srv-9"

    monkeypatch.setattr(main, "switch_server", flaky)
    monkeypatch.setattr(main.time, "sleep", lambda *a, **k: None)
    assert main._lock_biome("desert") is True
    assert calls["n"] == 3


def test_lock_biome_all_attempts_fail_is_warn_only(monkeypatch):
    calls = {"n": 0}

    def always_fail(b):
        calls["n"] += 1
        raise RuntimeError("network down")

    monkeypatch.setattr(main, "switch_server", always_fail)
    monkeypatch.setattr(main.time, "sleep", lambda *a, **k: None)
    assert main._lock_biome("desert") is False        # no raise
    assert calls["n"] == main._BIOME_LOCK_RETRIES


def test_lock_biome_constants_are_numbers():
    for name in ("_BIOME_LOCK_RETRIES", "_BIOME_LOCK_RETRY_SLEEP", "_BIOME_RECONNECT_SLEEP"):
        assert isinstance(getattr(main, name), (int, float))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test_main_worker.py -k lock_biome -q`
Expected: FAIL — `AttributeError: module 'main' has no attribute '_lock_biome'`.

- [ ] **Step 3: Write minimal implementation**

In `main.py`, right after `_reassert_invert_attack` (after line 678):

```python
_BIOME_LOCK_RETRIES = 3
_BIOME_LOCK_RETRY_SLEEP = 3.0
_BIOME_RECONNECT_SLEEP = 3.0


def _lock_biome(biome):
    """把客户端钉到 biome 对应生态区的服务器. florr 不记忆上次选的生态区 —— 不锁
    的话 click_start_game() 进的是 florr 默认那个(通常花园), 跟寻路用的地图对不上.
    复用 switch_server(biome) 的 CDP forceServerID(仓库历史确认过能触发重连).

    失败重试 _BIOME_LOCK_RETRIES 次(隔 _BIOME_LOCK_RETRY_SLEEP 秒), 都不成只警告
    不阻断(跟 _reassert_invert_attack 一个风格)—— 宁可这轮进错生态区, 也不卡死在
    开局菜单外面. 成功后 sleep 等重连落地再让调用方开始寻路. 返回 True/False.
    """
    for attempt in range(1, _BIOME_LOCK_RETRIES + 1):
        try:
            sid = switch_server(biome)
            print(f"🗺️ 已锁定生态区 {biome} (服务器 {sid})")
            time.sleep(_BIOME_RECONNECT_SLEEP)
            return True
        except Exception as e:
            print(f"⚠️ 锁定生态区第 {attempt}/{_BIOME_LOCK_RETRIES} 次失败: {e}")
            if attempt < _BIOME_LOCK_RETRIES:
                time.sleep(_BIOME_LOCK_RETRY_SLEEP)
    print("⚠️ 生态区没锁上, 先按当前服务器进游戏 (下轮回开局菜单再试)")
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test_main_worker.py -k lock_biome -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add main.py test_main_worker.py
git commit -m "feat(main): _lock_biome — retrying, warn-only biome lock via switch_server"
```

---

### Task 7: `main.run_worker` wiring — lock biome pre-loop + after click_start_game + fix switch_server() biome arg

**Files:**
- Modify: `main.py` — `run_worker` (pre-loop ~line 707-713; `on_start_screen` branch ~line 726-730; switch-server branch ~line 763)
- Test: `test_main_worker.py` (including updating the shared `_stub_run_worker_env` helper, lines 280-291)

**Interfaces:**
- Consumes: `main._lock_biome` (Task 6), `main._apply_worker_config` returning `"biome"` (Task 5).
- Produces: no new public symbol. Behavior:
  1. `run_worker` calls `_lock_biome(w["biome"])` once after `_apply_worker_config`, before `while True`; on `False` it does `overlay.update(message="⚠️ 生态区未锁定, 见日志")`.
  2. Inside the loop, in the `if on_start_screen():` block, after `click_start_game()` + `time.sleep(3)`, it calls `_lock_biome(w["biome"])`.
  3. The consecutive-short-round switch-server call passes the configured biome: `switch_server(w["biome"])` instead of `switch_server()`.

- [ ] **Step 1: Update the shared stub + write the failing tests**

In `test_main_worker.py`, update `_stub_run_worker_env` (lines 280-291) so the fake `_apply_worker_config` returns a `"biome"` and `switch_server` is harmless:

```python
def _stub_run_worker_env(monkeypatch, overlay=None):
    monkeypatch.setattr(main.cdp_bridge, "is_dedicated_chrome_ready", lambda: True)
    monkeypatch.setattr(main, "create_overlay",
                        lambda *a, **k: overlay or _StubOverlay())
    monkeypatch.setattr(main, "overlay", None, raising=False)
    monkeypatch.setattr(main, "_apply_worker_config", lambda cfg: {
        "location": (1, 2), "farming_area": [(0, 0), (9, 9)], "farming_duration": 300,
        "short_round_limit": 2, "enemy_ai_enabled": False, "auto_switch_server": False,
        "biome": "desert",
    })
    monkeypatch.setattr(main, "switch_server", lambda *a, **k: "stub-srv")
    monkeypatch.setattr(main, "on_death_screen", lambda: False)
    monkeypatch.setattr(main, "on_start_screen", lambda: False)
    monkeypatch.setattr(main.time, "sleep", lambda *a, **k: None)
```

Then add these tests:

```python
def test_run_worker_locks_biome_before_loop_and_after_start_click(monkeypatch):
    _stub_run_worker_env(monkeypatch)
    locks = []
    monkeypatch.setattr(main, "_lock_biome", lambda b: locks.append(b) or True)
    # 第 1 轮 on_start_screen True(要进游戏), 之后 raise 掐断
    seq = iter([True])
    monkeypatch.setattr(main, "on_start_screen",
                        lambda: next(seq, False) or (_ for _ in ()).throw(KeyboardInterrupt))
    monkeypatch.setattr(main, "click_start_game", lambda: True)
    monkeypatch.setattr(main, "_reassert_invert_attack", lambda: "on_already")
    monkeypatch.setattr(main, "lazy_theta_pathing",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    # 进循环前 1 次 + 第 1 轮 on_start_screen 分支里 1 次
    assert locks == ["desert", "desert"]


def test_run_worker_does_not_lock_biome_while_farming(monkeypatch):
    _stub_run_worker_env(monkeypatch)   # on_start_screen 恒 False
    locks = []
    monkeypatch.setattr(main, "_lock_biome", lambda b: locks.append(b) or True)
    monkeypatch.setattr(main, "_reassert_invert_attack", lambda: "on_already")
    monkeypatch.setattr(main, "lazy_theta_pathing",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    assert locks == ["desert"]          # 只有进循环前那一次, 循环体内不锁


def test_run_worker_prewarns_when_biome_lock_fails(monkeypatch):
    ov = _StubOverlay()
    warned = []
    ov.update = lambda **kw: warned.append(kw.get("message"))
    _stub_run_worker_env(monkeypatch, overlay=ov)
    monkeypatch.setattr(main, "_lock_biome", lambda b: False)
    monkeypatch.setattr(main, "_reassert_invert_attack", lambda: "on_already")
    monkeypatch.setattr(main, "lazy_theta_pathing",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    assert any(m and "生态区未锁定" in m for m in warned)


def test_run_worker_switch_server_uses_configured_biome(monkeypatch):
    _stub_run_worker_env(monkeypatch)
    # 让 auto_switch_server 开着、短局阈值 1, 第一轮就触发换服
    monkeypatch.setattr(main, "_apply_worker_config", lambda cfg: {
        "location": (1, 2), "farming_area": [(0, 0), (9, 9)], "farming_duration": 9999,
        "short_round_limit": 1, "enemy_ai_enabled": False, "auto_switch_server": True,
        "biome": "ocean",
    })
    monkeypatch.setattr(main, "_lock_biome", lambda b: True)
    monkeypatch.setattr(main, "_reassert_invert_attack", lambda: "on_already")
    monkeypatch.setattr(main, "lazy_theta_pathing", lambda *a, **k: False)  # 没到区 -> 短局
    sw = []
    monkeypatch.setattr(main, "switch_server", lambda *a, **k: sw.append(a) or "srv")
    # 换服后 time.sleep(2) 再进下一轮 —— 第 2 轮 lazy_theta_pathing 再 False -> 再换,
    # 用一个计数在第 2 次换服时掐断
    orig = main.switch_server

    def counting(*a, **k):
        sw.append(a)
        if len(sw) >= 1:
            raise KeyboardInterrupt
        return "srv"

    monkeypatch.setattr(main, "switch_server", counting)
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    assert sw and sw[0] == ("ocean",)      # switch_server 收到配置里的 biome, 不是空参
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test_main_worker.py -k "run_worker" -q`
Expected: the 4 new tests FAIL (`_lock_biome` not called yet → `locks` empty; `switch_server` still called with no args → `sw[0] == ()` not `("ocean",)`). Existing `test_run_worker_*` still pass.

- [ ] **Step 3: Wire `run_worker`**

In `main.py`, **pre-loop** — after line 711 (`CONSECUTIVE_SHORT_ROUND_LIMIT = w["short_round_limit"]`), before line 713 (`print("🎮 ...`):

```python

    # florr 不记忆上次选的生态区 —— 进主循环前先把服务器钉到配置的生态区.
    # worker 刚起时 florr 可能还停在标题页 (cp6 未必加载好), 这次是 best-effort;
    # 循环里 click_start_game() 之后那次 (一定在局内) 才是可靠的一发.
    if not _lock_biome(w["biome"]):
        overlay.update(message="⚠️ 生态区未锁定, 见日志")
```

In the `if on_start_screen():` block, after line 730 (`time.sleep(3)`), still inside the `if`:

```python
            # 已进游戏 -> cp6 就绪 -> forceServerID 重连到配置的生态区 (florr 默认
            # 通常是花园, 跟寻路用的地图对不上).
            _lock_biome(w["biome"])
```

At line 763, change:

```python
                    switch_server()
```

to:

```python
                    switch_server(w["biome"])
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS — all new `run_worker` tests green; `test_run_worker_reasserts_invert_attack_at_startup_and_each_round`, `test_run_worker_survives_invert_attack_failure`, `test_run_worker_does_not_start_florr_auto_afk` still green (the updated `_stub_run_worker_env` gives them `"biome"` + a harmless `switch_server`; the real `_lock_biome` runs, calls the stub `switch_server`, returns `True`, and `main.time.sleep` is stubbed so it's instant).

- [ ] **Step 5: Commit**

```bash
git add main.py test_main_worker.py
git commit -m "feat(main): lock biome pre-loop + after start click; switch_server uses configured biome"
```

---

## Self-Review

**1. Spec coverage**

| Spec section | Task |
|---|---|
| `server_lookup.biome_key_for_map` + `_MAP_TO_BIOME` | Task 1 |
| `app_config._GUI_ENABLED_MAPS`, `_VALID_MAPS` untouched | Task 2 |
| `gui_schedule` save-validation rejects disabled map | Task 3 (`validate_block`) |
| `gui_schedule` map selector → radio, ocean/anthell `state="disabled"` + "(暂不可用)" | Task 4 |
| Opening an old `map=ocean` block → save blocked until desert | Task 3 (validation) + Task 4 (manual check step 4) |
| `new_block_template` stays `"desert"` | unchanged — noted in Task 4 interfaces; existing `test_gui_schedule` covers via `_blk()` default |
| README note | Task 4 step 5 |
| `main._apply_worker_config` adds `"biome"` | Task 5 |
| `main._lock_biome` retry + warn-only + reconnect sleep | Task 6 |
| Wire pre-loop lock (`overlay.update` on fail) | Task 7 |
| Wire in-loop lock after `click_start_game()` | Task 7 |
| Not triggered while farming | Task 7 (`test_run_worker_does_not_lock_biome_while_farming`) |
| Fix `switch_server()` → `switch_server(w["biome"])` | Task 7 |
| `test_server_lookup` "7 biomes" case untouched | confirmed — Task 1 only adds a test, `BIOME_INDEX` unchanged |
| `test_app_config` `map="ocean"` coercion cases stay green | Task 2 step 4, Task 3 step 4 |

No gaps.

**2. Placeholder scan** — every code step has literal content; no "TBD"/"add error handling"/"similar to Task N". Task 4 has no automated test *by design* (repo has no CTk-widget test harness — verified: `test_gui_*.py` are all pure-function, no `Tk()` root); its deliverable is verified by import + full-suite-green + an explicit manual checklist, matching how prior GUI-phase work was landed.

**3. Type consistency**
- `biome_key_for_map(map_name) -> str` — defined Task 1, consumed Task 5 with the same name.
- `_lock_biome(biome) -> bool` — defined Task 6, called in Task 7 as `_lock_biome(w["biome"])`.
- `w["biome"]` — produced Task 5, consumed Task 7 (pre-loop, in-loop, switch-server).
- `_map_radio_state(map_name) -> str` (`"normal"`/`"disabled"`) — defined Task 3, consumed Task 4's `_build` loop.
- `switch_server(biome="desert") -> str` — pre-existing in `utils.py`, reached in `main` via `from utils import *`; Task 6 calls it, Task 7 changes one call site's arg.
- `self._map`: `CTkOptionMenu` → `tk.StringVar` in Task 4; `.get()`/`.set()` call sites (`_build` line 203, `_collect` line 323) keep working — same interface.
- Constants `_BIOME_LOCK_RETRIES` / `_BIOME_LOCK_RETRY_SLEEP` / `_BIOME_RECONNECT_SLEEP` — named identically in Task 6 impl, Task 6 tests, and the reconnect-sleep is referenced in Task 7 step 4's reasoning.

No mismatches.
