# Loadout-Swap Keys Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:**每个调度时块可配两组按键，bot 进游戏时按第一组、寻路到刷怪区时按第二组，用来切换 florr loadout。

**Architecture:** 新增无状态模块 `loadout_swap.py`（`press` / `sleep` 注入，纯单测）。`app_config.py` 的时块 schema 加两个字符串键（宽松 coerce，旧配置不炸）。`gui_schedule.py` 时块编辑器加两个下拉。`main.py` 的 `run_worker` 在两个已存在的位置各调一次 `loadout_swap.press_swap(...)`，warn-only。

**Tech Stack:** Python 3、pyautogui、CustomTkinter、pytest。venv 在 `venv/`（非 `.venv/`），测试用 `venv/bin/pytest`。

## Global Constraints

- 按键发送走 `pyautogui.press`，发到前台 florr 窗口 —— 跟现有 `pyautogui.press('m')` / `pyautogui.keyUp('space')` 一致，不引入 CDP 输入通道。
- 切装备是附加动作：任何异常必须吞掉 + `print` 一句 `⚠️`，绝不打断刷怪轮次（对齐 `main._reassert_invert_attack` 的 warn-only 风格）。
- 配置合法值集合：`("none", "digits", "k", "l")`。未知 / 缺失 → `"none"`。
- `digits` = 顺序点按 `"1234567890"` 每个字符，键间 `sleep(0.04)`。
- 每个时块一对键，跟随 `app_config._ACTIVE_KEYS`。不做全局配置、不做全局+覆盖。
- 中文注释 / 中文 print，跟仓库既有风格一致。
- 频繁提交：每个 Task 末尾一次 commit。

---

### Task 1: `loadout_swap.py` — 按键发送模块

**Files:**
- Create: `loadout_swap.py`
- Test: `test_loadout_swap.py`

**Interfaces:**
- Consumes: 无（叶子模块）。
- Produces: `loadout_swap.press_swap(spec, *, press=pyautogui.press, sleep=time.sleep) -> None`
  - `spec: str | None` —— `"none"` / `"digits"` / `"k"` / `"l"`，其它值（含 `None`、`""`）当 `"none"`。
  - `press: Callable[[str], object]` —— 注入点，默认 `pyautogui.press`。
  - `sleep: Callable[[float], object]` —— 注入点，默认 `time.sleep`。
  - 无返回值。异常内部吞掉，不外抛。

- [ ] **Step 1: Write the failing test**

创建 `test_loadout_swap.py`：

```python
import pytest

import loadout_swap


def _recorder():
    calls = []
    return calls, lambda k: calls.append(k)


@pytest.mark.parametrize("spec", ["none", None, "", "zzz", "1", "digit"])
def test_noop_specs_never_press(spec):
    calls, rec = _recorder()
    loadout_swap.press_swap(spec, press=rec, sleep=lambda _s: None)
    assert calls == []


def test_k_presses_k_once():
    calls, rec = _recorder()
    loadout_swap.press_swap("k", press=rec, sleep=lambda _s: None)
    assert calls == ["k"]


def test_l_presses_l_once():
    calls, rec = _recorder()
    loadout_swap.press_swap("l", press=rec, sleep=lambda _s: None)
    assert calls == ["l"]


def test_digits_presses_1_through_0_with_sleeps():
    calls, rec = _recorder()
    slept = []
    loadout_swap.press_swap("digits", press=rec, sleep=slept.append)
    assert calls == list("1234567890")
    assert len(slept) == 10
    assert all(s == pytest.approx(0.04) for s in slept)


def test_press_exception_is_swallowed(capsys):
    def boom(_k):
        raise RuntimeError("no focus")
    loadout_swap.press_swap("k", press=boom, sleep=lambda _s: None)   # 不抛
    assert "装备切换按键失败" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest test_loadout_swap.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'loadout_swap'`

- [ ] **Step 3: Write minimal implementation**

创建 `loadout_swap.py`：

```python
"""进游戏 / 到刷怪区时按一组键切换 florr loadout.

跟 florr_settings.py / server_lookup.py 一样: 小、单一职责、不 import GUI、
不 import main. press / sleep 通过参数注入, 方便单测.
"""
import time

import pyautogui

_DIGITS = "1234567890"


def press_swap(spec, *, press=pyautogui.press, sleep=time.sleep):
    """按一组切换 loadout 的键.

    spec:
      - "none" / None / "" / 未知值: 什么都不做.
      - "k" / "l": 按一下 (florr 里绑的 loadout 预设键).
      - "digits": 顺序点按 1..0, 每键之间 ~40ms —— 把每个槽位在主/副行之间
        对调一遍 = 换整套.

    任何异常吞掉 + 打日志, 绝不抛给调用方: 切装备是附加动作, 不能打断刷怪轮次
    (对齐 main._reassert_invert_attack 的 warn-only).
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

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/pytest test_loadout_swap.py -v`
Expected: PASS（全部 8 个参数化 + 具名用例）

- [ ] **Step 5: Commit**

```bash
git add loadout_swap.py test_loadout_swap.py
git commit -m "feat(loadout_swap): press_swap — none/digits/k/l loadout keys"
```

---

### Task 2: `app_config.py` — 时块 schema 加 enter_game_swap / reach_area_swap

**Files:**
- Modify: `app_config.py`
  - `DEFAULTS` dict (around `app_config.py:19-32`)
  - `_ACTIVE_KEYS` tuple (`app_config.py:39-42`)
  - `_coerce_v1` per-key validation (`app_config.py:62-91`)
  - `_coerce_block` (`app_config.py:135-184`)
- Test: `test_app_config.py`

**Interfaces:**
- Consumes: Task 1 nothing. Uses existing `app_config.DEFAULTS`, `_ACTIVE_KEYS`, `_coerce_block`, `_coerce_v1`, `migrate_v1`, `load_config`, `save_config`.
- Produces:
  - `app_config.DEFAULTS["enter_game_swap"] == "none"`, `app_config.DEFAULTS["reach_area_swap"] == "none"`.
  - `"enter_game_swap"` and `"reach_area_swap"` in `app_config._ACTIVE_KEYS`.
  - `_coerce_block(raw, aliases, n)` tolerates a `raw` **without** these keys (returns a block with them set to `"none"`, does NOT return `None`); illegal value → `"none"`.
  - `DEFAULTS_V2["active"]` automatically gains the two keys (it is `{k: deepcopy(DEFAULTS[k]) for k in _ACTIVE_KEYS}` at `app_config.py:109`).
- New module-level constant: `app_config._SWAP_VALUES = ("none", "digits", "k", "l")`.

- [ ] **Step 1: Write the failing tests**

追加到 `test_app_config.py` 末尾（复用文件顶部已有的 `_v2_block` / `_v2_cfg` / `cfg_path`）：

```python
class TestLoadoutSwapKeys:
    def test_defaults_have_none(self):
        assert app_config.DEFAULTS["enter_game_swap"] == "none"
        assert app_config.DEFAULTS["reach_area_swap"] == "none"

    def test_active_keys_include_swaps(self):
        assert "enter_game_swap" in app_config._ACTIVE_KEYS
        assert "reach_area_swap" in app_config._ACTIVE_KEYS

    def test_defaults_v2_active_slice_has_swaps(self):
        assert app_config.DEFAULTS_V2["active"]["enter_game_swap"] == "none"
        assert app_config.DEFAULTS_V2["active"]["reach_area_swap"] == "none"

    def test_valid_values_roundtrip(self, cfg_path):
        cfg = _v2_cfg()
        cfg["schedule"] = [_v2_block(enter_game_swap="k", reach_area_swap="digits")]
        app_config.save_config(cfg)
        blk = app_config.load_config()["schedule"][0]
        assert blk["enter_game_swap"] == "k"
        assert blk["reach_area_swap"] == "digits"

    def test_illegal_value_falls_back_to_none(self, cfg_path):
        cfg = _v2_cfg()
        cfg["schedule"] = [_v2_block(enter_game_swap="capslock", reach_area_swap=7)]
        app_config.save_config(cfg)
        blk = app_config.load_config()["schedule"][0]
        assert blk["enter_game_swap"] == "none"
        assert blk["reach_area_swap"] == "none"

    def test_old_block_without_keys_still_valid(self, cfg_path):
        # 旧 config.json: 时块 dict 里根本没有这两个键 —— 不能被整块丢
        cfg = _v2_cfg()
        blk = _v2_block()
        blk.pop("enter_game_swap", None)
        blk.pop("reach_area_swap", None)
        cfg["schedule"] = [blk]
        app_config.save_config(cfg)
        got = app_config.load_config()
        assert len(got["schedule"]) == 1
        assert got["schedule"][0]["enter_game_swap"] == "none"
        assert got["schedule"][0]["reach_area_swap"] == "none"

    def test_v1_migration_adds_defaults(self, cfg_path):
        cfg_path.write_text(json.dumps({
            "map": "desert", "location": [1, 2], "farming_area": [[0, 0], [3, 3]],
            "farming_duration": 100, "consecutive_short_round_limit": 1,
            "enemy_ai_enabled": False, "auto_switch_server": True,
        }), encoding="utf-8")
        got = app_config.load_config()
        assert got["schedule"][0]["enter_game_swap"] == "none"
        assert got["schedule"][0]["reach_area_swap"] == "none"
        assert got["active"]["enter_game_swap"] == "none"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest test_app_config.py::TestLoadoutSwapKeys -v`
Expected: FAIL —— `KeyError: 'enter_game_swap'` on `DEFAULTS`, and roundtrip drops the keys.

- [ ] **Step 3: Write minimal implementation**

**3a.** `app_config.py` `DEFAULTS` —— 在 `"auto_switch_server": True,` 之后、`"afk_enabled": False,` 之前加：

```python
    # 进游戏 / 寻路到刷怪区时按一组键切换 florr loadout. "none"=不切换,
    # "digits"=顺序点按 1..0 (整套主副对调), "k"/"l"=按 florr 里绑的预设键.
    "enter_game_swap": "none",
    "reach_area_swap": "none",
```

**3b.** `app_config.py` 在 `_TIME_RE = ...` 一行下面加模块常量：

```python
_SWAP_VALUES = ("none", "digits", "k", "l")
```

**3c.** `app_config.py` `_ACTIVE_KEYS` —— 尾部加两个键：

```python
_ACTIVE_KEYS = (
    "map", "location", "farming_area", "farming_duration",
    "consecutive_short_round_limit", "enemy_ai_enabled", "auto_switch_server",
    "enter_game_swap", "reach_area_swap",
)
```

**3d.** `app_config.py` `_coerce_v1` —— 把最后的 `else:` 分支（`app_config.py:85-86`，当前处理 `enemy_ai_enabled` / `auto_switch_server` / `afk_enabled`）改成先分流 swap 键：

```python
        elif key in ("enter_game_swap", "reach_area_swap"):
            ok = isinstance(val, str) and val in _SWAP_VALUES
        else:  # enemy_ai_enabled / auto_switch_server / afk_enabled
            ok = isinstance(val, bool)
```

**3e.** `app_config.py` `_coerce_block` —— 在 `return {...}` 之前（`app_config.py:176` 那个 `return {` 上面）加：

```python
    def _swap(key):
        v = raw.get(key, "none")
        return v if isinstance(v, str) and v in _SWAP_VALUES else "none"
```

然后在 `_coerce_block` 的返回 dict 里，`"enemy_ai_enabled": eai, "auto_switch_server": asw,` 之后加一行：

```python
        "enter_game_swap": _swap("enter_game_swap"),
        "reach_area_swap": _swap("reach_area_swap"),
```

> 注意：用 `raw.get(key, "none")`，**不要**用 `raw[key]` —— 旧 config.json 的时块没有这两个键，`raw[key]` 会 `KeyError` 让整块被丢。

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest test_app_config.py -v`
Expected: PASS（新 `TestLoadoutSwapKeys` 全绿，且原有用例不回归 —— `test_missing_file_returns_defaults_v2` / `test_v2_roundtrips` 仍过，因为它们从 `DEFAULTS_V2` 构造，两边同步长出新键）。

- [ ] **Step 5: Commit**

```bash
git add app_config.py test_app_config.py
git commit -m "feat(app_config): enter_game_swap / reach_area_swap per time-block (lenient coerce)"
```

---

### Task 3: `gui_schedule.py` — 时块编辑器两个下拉

**Files:**
- Modify: `gui_schedule.py`
  - `block_to_active` (`gui_schedule.py:27-40`)
  - `new_block_template` (`gui_schedule.py:73-82`)
  - `TimeBlockEditor._build` (add widgets after `self._autosw`, around `gui_schedule.py:217`)
  - `TimeBlockEditor._collect` (`gui_schedule.py:312-337`)
- Test: `test_gui_schedule.py`

**Interfaces:**
- Consumes: `app_config._SWAP_VALUES` (Task 2), `app_config._ACTIVE_KEYS` (Task 2).
- Produces:
  - `gui_schedule.block_to_active(block)` output dict contains `"enter_game_swap"` / `"reach_area_swap"` (coerced via same set, default `"none"`).
  - `gui_schedule.new_block_template(cfg)` output contains `"enter_game_swap": "none"`, `"reach_area_swap": "none"`.
  - Module-level mapping constants:
    - `_SWAP_LABELS = {"none": "不切换", "digits": "全部数字键 1–0", "k": "k", "l": "l"}`
    - `_SWAP_FROM_LABEL = {v: k for k, v in _SWAP_LABELS.items()}`
  - `TimeBlockEditor` instance attrs `self._enter_swap`, `self._reach_swap` (both `ctk.CTkOptionMenu`).

- [ ] **Step 1: Write the failing tests**

追加到 `test_gui_schedule.py` 末尾：

```python
class TestLoadoutSwapInGuiSchedule:
    def test_block_to_active_carries_swaps(self):
        blk = _blk(enter_game_swap="k", reach_area_swap="digits")
        act = gs.block_to_active(blk)
        assert act["enter_game_swap"] == "k"
        assert act["reach_area_swap"] == "digits"

    def test_block_to_active_defaults_missing_swaps_to_none(self):
        blk = _blk()
        blk.pop("enter_game_swap", None)
        blk.pop("reach_area_swap", None)
        act = gs.block_to_active(blk)
        assert act["enter_game_swap"] == "none"
        assert act["reach_area_swap"] == "none"

    def test_new_block_template_has_none_swaps(self):
        cfg = {"profiles": [{"alias": "默认", "dir": "d"}], "schedule": []}
        tpl = gs.new_block_template(cfg)
        assert tpl["enter_game_swap"] == "none"
        assert tpl["reach_area_swap"] == "none"

    def test_label_maps_are_inverse(self):
        for k in ("none", "digits", "k", "l"):
            assert gs._SWAP_FROM_LABEL[gs._SWAP_LABELS[k]] == k
```

检查 `test_gui_schedule.py` 顶部 `_blk(**kw)` helper 是否 `base.update(kw)` 收未知键 —— 若 `_blk` 用固定 dict 不 merge kwargs，改成 `base.update(kw); return base`（当前它已支持 kwargs，见 `test_gui_schedule.py:29` 用法）。

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest test_gui_schedule.py::TestLoadoutSwapInGuiSchedule -v`
Expected: FAIL —— `KeyError: 'enter_game_swap'` in `block_to_active`; `AttributeError: module 'gui_schedule' has no attribute '_SWAP_LABELS'`.

- [ ] **Step 3: Write minimal implementation**

**3a.** `gui_schedule.py` —— 在 `_ACTIVE_KEYS = app_config._ACTIVE_KEYS`（`gui_schedule.py:15`）下面加：

```python
_SWAP_LABELS = {"none": "不切换", "digits": "全部数字键 1–0", "k": "k", "l": "l"}
_SWAP_FROM_LABEL = {v: k for k, v in _SWAP_LABELS.items()}


def _coerce_swap(v):
    return v if isinstance(v, str) and v in _SWAP_LABELS else "none"
```

**3b.** `block_to_active` —— 返回 dict 里，`"auto_switch_server": bool(block["auto_switch_server"]),` 之后加：

```python
        "enter_game_swap": _coerce_swap(block.get("enter_game_swap")),
        "reach_area_swap": _coerce_swap(block.get("reach_area_swap")),
```

**3c.** `new_block_template` —— 返回 dict 里，`"enemy_ai_enabled": True, "auto_switch_server": True,` 之后加：

```python
        "enter_game_swap": "none", "reach_area_swap": "none",
```

**3d.** `TimeBlockEditor._build` —— 在 `self._autosw ... .pack(...)`（`gui_schedule.py:217`）之后、`self._adv_open = False`（`gui_schedule.py:219`）之前加：

```python
        _swap_vals = list(_SWAP_LABELS.values())
        ctk.CTkLabel(self, text="进游戏切换装备").pack(anchor="w", padx=12, pady=(6, 0))
        self._enter_swap = ctk.CTkOptionMenu(self, values=_swap_vals)
        self._enter_swap.set(_SWAP_LABELS[_coerce_swap(self._block.get("enter_game_swap"))])
        self._enter_swap.pack(anchor="w", padx=12, pady=2)
        _Tooltip(self._enter_swap,
                 "每轮进游戏后按这组键换 loadout. 全部数字键 = 把 1 到 0 都点一遍, 整套主副对调.")
        ctk.CTkLabel(self, text="到刷怪区切换装备").pack(anchor="w", padx=12, pady=(6, 0))
        self._reach_swap = ctk.CTkOptionMenu(self, values=_swap_vals)
        self._reach_swap.set(_SWAP_LABELS[_coerce_swap(self._block.get("reach_area_swap"))])
        self._reach_swap.pack(anchor="w", padx=12, pady=2)
        _Tooltip(self._reach_swap, "寻路到刷怪区后按这组键换 loadout.")
```

**3e.** `TimeBlockEditor._collect` —— `blk.update(...)` 调用里，`auto_switch_server=bool(self._autosw.get()),` 之后加：

```python
            enter_game_swap=_SWAP_FROM_LABEL.get(self._enter_swap.get(), "none"),
            reach_area_swap=_SWAP_FROM_LABEL.get(self._reach_swap.get(), "none"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest test_gui_schedule.py -v`
Expected: PASS（新用例绿，原有不回归）。

> GUI 控件本身（下拉渲染 / 保存回读）仍靠手动 smoke —— 见 memory `gui-phase2-shipped`，本仓库 GUI smoke 从未真跑过；这里只测纯函数层。

- [ ] **Step 5: Commit**

```bash
git add gui_schedule.py test_gui_schedule.py
git commit -m "feat(gui_schedule): two loadout-swap dropdowns in time-block editor"
```

---

### Task 4: `main.py` — run_worker 两个触发点接线 + README

**Files:**
- Modify: `main.py`
  - top imports (`main.py:1-13`)
  - `_apply_worker_config` return dict (`main.py:657-665`)
  - `run_worker` main loop: after per-round `_reassert_invert_attack()` (`main.py:734`) and inside the `lazy_theta_pathing(...)` success branch before `auto_farming(...)` (`main.py:740-743`)
- Modify: `README.md` (add a short subsection)
- Test: `test_main_worker.py`

**Interfaces:**
- Consumes:
  - `loadout_swap.press_swap(spec)` (Task 1).
  - `app_config.DEFAULTS["enter_game_swap"]` / `["reach_area_swap"]` (Task 2).
- Produces:
  - `main._apply_worker_config(cfg)` return dict gains `"enter_game_swap"` and `"reach_area_swap"` (read from `src.get(..., d[...])`, same pattern as neighbours).
  - `run_worker` calls `loadout_swap.press_swap(w["enter_game_swap"])` once per round right after the per-round invert-attack re-assert; calls `loadout_swap.press_swap(w["reach_area_swap"])` once immediately after `lazy_theta_pathing(...)` returns truthy, before `auto_farming(...)`.

- [ ] **Step 1: Write the failing tests**

先把 `test_main_worker.py` 的 `_stub_run_worker_env` helper（`test_main_worker.py:285-288`）里那个 stub `_apply_worker_config` 补上两个键：

```python
    monkeypatch.setattr(main, "_apply_worker_config", lambda cfg: {
        "location": (1, 2), "farming_area": [(0, 0), (9, 9)], "farming_duration": 300,
        "short_round_limit": 2, "enemy_ai_enabled": False, "auto_switch_server": False,
        "enter_game_swap": "none", "reach_area_swap": "none",
    })
```

再追加用例到 `test_main_worker.py` 末尾：

```python
def test_apply_worker_config_reads_swap_keys(monkeypatch):
    monkeypatch.setattr(main, "apply_map", lambda name: None)
    cfg = {"version": 2, "active": {"map": "desert",
                                    "enter_game_swap": "k", "reach_area_swap": "digits"}}
    w = main._apply_worker_config(cfg)
    assert w["enter_game_swap"] == "k"
    assert w["reach_area_swap"] == "digits"


def test_apply_worker_config_swap_keys_default_none(monkeypatch):
    monkeypatch.setattr(main, "apply_map", lambda name: None)
    w = main._apply_worker_config({"version": 2, "active": {"map": "desert"}})
    assert w["enter_game_swap"] == "none"
    assert w["reach_area_swap"] == "none"


def _swap_env(monkeypatch, *, enter="k", reach="l"):
    """_stub_run_worker_env + 记录 press_swap 调用 + _apply_worker_config 带 swap 键."""
    _stub_run_worker_env(monkeypatch)
    monkeypatch.setattr(main, "_apply_worker_config", lambda cfg: {
        "location": (1, 2), "farming_area": [(0, 0), (9, 9)], "farming_duration": 300,
        "short_round_limit": 2, "enemy_ai_enabled": False, "auto_switch_server": False,
        "enter_game_swap": enter, "reach_area_swap": reach,
    })
    monkeypatch.setattr(main.florr_settings, "ensure_invert_attack_on",
                        lambda ej, *a, **k: ("on_already", ""))
    seen = []
    monkeypatch.setattr(main.loadout_swap, "press_swap", lambda spec: seen.append(spec))
    return seen


def test_run_worker_presses_enter_swap_each_round(monkeypatch):
    seen = _swap_env(monkeypatch, enter="k", reach="l")
    monkeypatch.setattr(main, "lazy_theta_pathing",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    assert seen == ["k"]                 # 进游戏切换按了; 没到区域, reach 没按


def test_run_worker_presses_reach_swap_on_arrival(monkeypatch):
    seen = _swap_env(monkeypatch, enter="k", reach="l")
    calls = {"n": 0}

    def fake_path(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return True
        raise KeyboardInterrupt

    monkeypatch.setattr(main, "lazy_theta_pathing", fake_path)
    monkeypatch.setattr(main, "auto_farming",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    assert seen == ["k", "l"]            # enter 先, 到区域后 reach


def test_run_worker_skips_reach_swap_when_pathing_fails(monkeypatch):
    seen = _swap_env(monkeypatch, enter="k", reach="l")
    calls = {"n": 0}

    def fake_path(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return False
        raise KeyboardInterrupt

    monkeypatch.setattr(main, "lazy_theta_pathing", fake_path)
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    assert "l" not in seen               # 没到区域 → reach 永不触发
    assert seen == ["k", "k"]            # 两轮各按一次 enter
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest test_main_worker.py -v`
Expected: FAIL —— `AttributeError: module 'main' has no attribute 'loadout_swap'`；`_apply_worker_config` 结果无 `enter_game_swap`。

- [ ] **Step 3: Write minimal implementation**

**3a.** `main.py` 顶部 import —— 在 `import florr_settings`（`main.py:13`）下面加：

```python
import loadout_swap
```

**3b.** `main.py` `_apply_worker_config` return dict —— `"auto_switch_server": src.get("auto_switch_server", d["auto_switch_server"]),`（`main.py:664`）之后加：

```python
        "enter_game_swap": src.get("enter_game_swap", d["enter_game_swap"]),
        "reach_area_swap": src.get("reach_area_swap", d["reach_area_swap"]),
```

**3c.** `main.py` `run_worker` 主循环 —— 每轮 `_reassert_invert_attack()`（`main.py:734`，就是注释 “每轮重写一次” 那句下面那行）之后加：

```python
        # 进游戏了: 按配置的键切到"赶路" loadout (florr 重生会把 loadout 拉回账号
        # 默认, 所以每轮进来都按一次). press_swap 内部 warn-only, 不打断轮次.
        loadout_swap.press_swap(w["enter_game_swap"])
```

**3d.** `main.py` `run_worker` —— `if lazy_theta_pathing(location, [farming_area]):`（`main.py:740`）成功分支里，`print("✅ 到达刷怪区域！")` 之后、`auto_farming(...)`（`main.py:742`）之前加：

```python
            # 到刷怪区了: 按配置的键切到"输出" loadout.
            loadout_swap.press_swap(w["reach_area_swap"])
```

**3e.** `README.md` —— 找到讲调度时块 / Invert Attack 的段落附近，加一个小节：

```markdown
### 按区域切换 loadout（可选）

每个调度时块可以配两组按键：

- **进游戏切换装备**：每轮进入游戏后按一次。
- **到刷怪区切换装备**：寻路到刷怪区域后按一次。

取值：

- `不切换` —— 默认，什么都不按。
- `全部数字键 1–0` —— 顺序点按 `1 2 3 4 5 6 7 8 9 0`，把每个花瓣槽位在主行 / 副行之间对调一遍（换整套）。
- `k` / `l` —— 按一下 florr 里绑到 loadout 预设的键（需要你先在 florr 设置里绑好，没绑就无反应）。

按键通过前台窗口发给 florr，跟 bot 的其它输入一样要求 florr 窗口在最前。
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest test_main_worker.py -v`
Expected: PASS（新 5 个用例绿；原有 `test_run_worker_reasserts_invert_attack_*` 等不回归 —— stub env 补了 swap 键后仍然只断言 invert-attack 调用次数）。

- [ ] **Step 5: Full suite + commit**

Run: `venv/bin/pytest -q`
Expected: PASS（无回归；已知无关的 GUI smoke / 需真显示器的用例按仓库现状跳过或 xfail）。

```bash
git add main.py test_main_worker.py README.md
git commit -m "feat(main): press loadout-swap keys on enter-game and on reaching farm area"
```

---

## Self-Review

**1. Spec coverage:**

| Spec section | Task |
|---|---|
| 触发点：进游戏后按 enter swap | Task 4 (3c) |
| 触发点：`lazy_theta_pathing` 返 True 后按 reach swap | Task 4 (3d) |
| `lazy_theta_pathing` 返 False → reach swap 不触发 | Task 4 test `test_run_worker_skips_reach_swap_when_pathing_fails` |
| warn-only，不打断轮次 | Task 1 `press_swap` try/except + test `test_press_exception_is_swallowed` |
| 配置键 `enter_game_swap` / `reach_area_swap`，默认 `none` | Task 2 (3a) |
| `_ACTIVE_KEYS` 加键 | Task 2 (3c) |
| `DEFAULTS` 扁平加键（v1 迁移 + active 兜底） | Task 2 (3a) + test `test_v1_migration_adds_defaults` |
| `_coerce_block` 宽松（旧块不丢，非法值→none） | Task 2 (3e) + tests `test_old_block_without_keys_still_valid` / `test_illegal_value_falls_back_to_none` |
| `_coerce_v1` 集合校验 | Task 2 (3d) |
| `block_to_active` 加键 | Task 3 (3b) |
| `new_block_template` 默认 none | Task 3 (3c) |
| GUI 两个下拉 + 显示↔存储映射 | Task 3 (3a/3d/3e) |
| `loadout_swap.py` 新模块，press/sleep 注入 | Task 1 |
| `digits` = 1..0 顺序 + 40ms | Task 1 + test `test_digits_presses_1_through_0_with_sleeps` |
| `k`/`l` 按一下 | Task 1 |
| pyautogui.press 通道，不走 CDP | Task 1 (import pyautogui, default arg) |
| README 提示 k/l 需在 florr 绑定 + 前台要求 | Task 4 (3e) |

无缺口。

**2. Placeholder scan:** 无 TBD / “add error handling” / 无代码块的代码步骤 —— 每个实现步骤都给了确切代码与插入锚点。

**3. Type consistency:**
- `press_swap(spec, *, press=..., sleep=...)` —— Task 1 定义，Task 4 只按位置传 `spec`（`lambda spec: seen.append(spec)` 也是单参），一致。
- `_SWAP_VALUES`（app_config，Task 2）vs `_SWAP_LABELS` / `_SWAP_FROM_LABEL` / `_coerce_swap`（gui_schedule，Task 3）—— 两处独立常量，键集合都是 `("none","digits","k","l")`，一致。
- `_apply_worker_config` 返回键名 `enter_game_swap` / `reach_area_swap` == config schema 键名 == `w[...]` 读取名，全一致。
- `block_to_active` 用 `block.get("enter_game_swap")`（可能 None）→ `_coerce_swap` 兜 None → `"none"`，安全。
