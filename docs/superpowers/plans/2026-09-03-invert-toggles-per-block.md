# invert-attack/defense toggles: global → per-time-block — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** move `invert_attack` / `invert_defense` from global top-level `config.json` keys (+ GUI sidebar switches) to per-time-block keys in `_ACTIVE_KEYS` (+ GUI time-block editor switches), same as `enemy_ai_enabled` / `enter_game_swap`.

**Architecture:** `_ACTIVE_KEYS` gains the two keys → `migrate_v1` block-loop + `DEFAULTS_V2["active"]` comprehension carry them automatically. `_coerce_block` validates leniently (missing/non-bool → default, never drops the block). `main.run_worker` reads `want_attack`/`want_defense` from `_apply_worker_config(cfg)` output (the active slice) instead of `cfg` top-level. GUI sidebar switches deleted; two `CTkSwitch` added to `TimeBlockEditor`.

**Tech Stack:** Python 3, pytest, CustomTkinter. venv at `venv/` (symlink); tests `venv/bin/pytest`.

## Global Constraints

- The two keys move INTO `app_config._ACTIVE_KEYS`; they are removed from every top-level location (`_coerce`, `DEFAULTS_V2` top level, `migrate_v1` return-dict top level). Flat `DEFAULTS["invert_attack"]=True` / `["invert_defense"]=False` STAY (used by the `_ACTIVE_KEYS` migration loop + empty-schedule `active` fallback).
- Per-block default: `invert_attack` **True** (on), `invert_defense` **False** (off). Everywhere — `DEFAULTS`, `_coerce_block` fallback, `gui_schedule.new_block_template`, `block_to_active` `.get(...)` default.
- `_coerce_block` handling is LENIENT: missing key → default, non-bool value → default, block NOT dropped (unlike `enemy_ai_enabled` which `return None`s). Old v2 time-blocks lack these keys.
- Toggle semantics unchanged: `worker` writes `1 if want else 0` (switch ON→1, OFF→0 force-disable) at startup + each round via the untouched `florr_settings.ensure_flag` / `main._reassert_florr_toggles`.
- No global fallback switch. Fully per-block.
- No migration of a top-level `invert_attack` from old configs (`ba89f39` shipped minutes ago; no real configs have it).
- GUI widgets are NOT auto-tested (repo norm); tests cover pure-function layers only.
- Chinese comments/labels per repo style. Each Task ends with one commit. TDD.

---

### Task 1: `app_config.py` — move the two keys into `_ACTIVE_KEYS`, lenient `_coerce_block`

**Files:**
- Modify: `app_config.py`
  - `_ACTIVE_KEYS` tuple (around `app_config.py:54-58`)
  - `_coerce()` — remove 2 lines (around `app_config.py:268-269`)
  - `DEFAULTS_V2` — remove 2 top-level lines (around `app_config.py:142-143`)
  - `migrate_v1()` — remove 2 return-dict lines (around `app_config.py:305-306`)
  - `_coerce_block()` — add lenient bool coercion (around `app_config.py:212-232`)
  - `DEFAULTS` flat comment (around `app_config.py:31-34`) — reword, keep the two entries
- Test: `test_app_config.py` (rewrite `TestInvertToggles`)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `"invert_attack"` and `"invert_defense"` in `app_config._ACTIVE_KEYS`.
  - `app_config.DEFAULTS["invert_attack"] is True`, `["invert_defense"] is False` (unchanged).
  - `"invert_attack" not in app_config.DEFAULTS_V2` (top level); `app_config.DEFAULTS_V2["active"]["invert_attack"] is True`, `["invert_defense"] is False`.
  - `load_config()` result: NO top-level `invert_attack`/`invert_defense`; every `schedule` block AND `active` carries both as bool.
  - `_coerce_block(raw, aliases, n)`: a `raw` missing the keys → block returned with `invert_attack=True` / `invert_defense=False`, NOT `None`. Non-bool value → default.

- [ ] **Step 1: Write the failing tests**

Replace the whole `class TestInvertToggles:` block in `test_app_config.py` with:

```python
_INV_DEFAULTS = {"invert_attack": True, "invert_defense": False}


class TestInvertToggles:
    def test_flat_defaults_unchanged(self):
        assert app_config.DEFAULTS["invert_attack"] is True
        assert app_config.DEFAULTS["invert_defense"] is False

    def test_now_in_active_keys(self):
        assert "invert_attack" in app_config._ACTIVE_KEYS
        assert "invert_defense" in app_config._ACTIVE_KEYS

    def test_not_top_level_in_defaults_v2(self):
        assert "invert_attack" not in app_config.DEFAULTS_V2
        assert "invert_defense" not in app_config.DEFAULTS_V2
        assert app_config.DEFAULTS_V2["active"]["invert_attack"] is True
        assert app_config.DEFAULTS_V2["active"]["invert_defense"] is False

    def test_load_has_them_per_block_not_top_level(self, cfg_path):
        cfg = _v2_cfg()
        cfg["schedule"] = [_v2_block()]
        app_config.save_config(cfg)
        got = app_config.load_config()
        assert "invert_attack" not in got            # gone from top level
        blk = got["schedule"][0]
        assert blk["invert_attack"] is True
        assert blk["invert_defense"] is False
        assert got["active"]["invert_attack"] is True

    def test_block_explicit_values_roundtrip(self, cfg_path):
        cfg = _v2_cfg()
        cfg["schedule"] = [_v2_block(invert_attack=False, invert_defense=True)]
        app_config.save_config(cfg)
        blk = app_config.load_config()["schedule"][0]
        assert blk["invert_attack"] is False
        assert blk["invert_defense"] is True

    def test_block_non_bool_falls_back_to_default(self, cfg_path):
        cfg = _v2_cfg()
        cfg["schedule"] = [_v2_block(invert_attack="yes", invert_defense=1)]
        app_config.save_config(cfg)
        blk = app_config.load_config()["schedule"][0]
        assert blk["invert_attack"] is True
        assert blk["invert_defense"] is False

    def test_old_block_without_keys_kept_with_defaults(self, cfg_path):
        cfg = _v2_cfg()
        b = _v2_block()
        b.pop("invert_attack", None)
        b.pop("invert_defense", None)
        cfg["schedule"] = [b]
        app_config.save_config(cfg)
        got = app_config.load_config()
        assert len(got["schedule"]) == 1            # not dropped
        assert got["schedule"][0]["invert_attack"] is True
        assert got["schedule"][0]["invert_defense"] is False

    def test_v1_migration_per_block_and_active_not_top_level(self, cfg_path):
        cfg_path.write_text(json.dumps({
            "map": "desert", "location": [1, 2], "farming_area": [[0, 0], [3, 3]],
            "farming_duration": 100, "consecutive_short_round_limit": 1,
            "enemy_ai_enabled": False, "auto_switch_server": True,
        }), encoding="utf-8")
        got = app_config.load_config()
        assert "invert_attack" not in got
        assert got["schedule"][0]["invert_attack"] is True
        assert got["schedule"][0]["invert_defense"] is False
        assert got["active"]["invert_attack"] is True
        assert got["active"]["invert_defense"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest test_app_config.py::TestInvertToggles -v`
Expected: FAIL — `test_now_in_active_keys` (keys not in `_ACTIVE_KEYS`), `test_not_top_level_in_defaults_v2` (still top level), `test_load_has_them_per_block_not_top_level` (`"invert_attack" in got` top level; block has no such key → KeyError).

- [ ] **Step 3: Implement**

**3a.** `_ACTIVE_KEYS` — append the two keys:

```python
_ACTIVE_KEYS = (
    "map", "location", "farming_area", "farming_duration",
    "consecutive_short_round_limit", "enemy_ai_enabled", "auto_switch_server",
    "enter_game_swap", "reach_area_swap",
    "invert_attack", "invert_defense",
)
```

**3b.** `_coerce()` — DELETE these two lines (the ones right after the `afk_enabled` line):

```python
    cfg["invert_attack"] = raw["invert_attack"] if isinstance(raw.get("invert_attack"), bool) else True
    cfg["invert_defense"] = raw["invert_defense"] if isinstance(raw.get("invert_defense"), bool) else False
```

**3c.** `DEFAULTS_V2` — DELETE the two top-level lines:

```python
    "invert_attack": True,
    "invert_defense": False,
```

(the `"active": {k: copy.deepcopy(DEFAULTS[k]) for k in _ACTIVE_KEYS}` line stays and now auto-includes them.)

**3d.** `migrate_v1()` — DELETE the two return-dict lines:

```python
        "invert_attack": flat["invert_attack"],
        "invert_defense": flat["invert_defense"],
```

(the `for k in _ACTIVE_KEYS: block[k] = copy.deepcopy(flat[k])` loop + `"active": {k: ... for k in _ACTIVE_KEYS}` now carry them.)

**3e.** `_coerce_block()` — after the `eai, asw = raw.get(...)` / `if not isinstance(...): return None` block and before the final `return {`, add:

```python
    def _bool_or(key, dflt):
        v = raw.get(key, dflt)
        return v if isinstance(v, bool) else dflt
```

and in the returned dict, after `"enter_game_swap": ...` / `"reach_area_swap": ...`, add:

```python
        "invert_attack": _bool_or("invert_attack", DEFAULTS["invert_attack"]),
        "invert_defense": _bool_or("invert_defense", DEFAULTS["invert_defense"]),
```

**3f.** `DEFAULTS` flat — reword the comment above `"invert_attack": True`:

```python
    # florr 反转攻击键 / 反转防御键: 每个时块单独配 (在 _ACTIVE_KEYS 里). worker 每轮
    # 把对应 WASM 字节写成 (1 if True else 0). 这里的扁平默认给 _ACTIVE_KEYS 迁移 +
    # 空 schedule 时 active 兜底用; 新建时块的默认在 gui_schedule 里.
    "invert_attack": True,
    "invert_defense": False,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest test_app_config.py -v`
Expected: PASS. Note: `test_missing_file_returns_defaults_v2` / `test_v2_roundtrips` compare against `DEFAULTS_V2` — they stay green because both the expected and actual lose the two top-level keys together and gain them in `active`.

- [ ] **Step 5: Commit**

```bash
git add app_config.py test_app_config.py
git commit -m "refactor(app_config): invert_attack/invert_defense move to _ACTIVE_KEYS (per-block)"
```

---

### Task 2: `main.py` — read `want_*` from the active slice, reorder `run_worker` startup

**Files:**
- Modify: `main.py`
  - `_apply_worker_config()` return dict (around `main.py:659-671`)
  - `run_worker()` — remove the pre-`_apply_worker_config` want-block (around `main.py:769-776`), add it after `w = _apply_worker_config(cfg)` (around `main.py:778-782`)
- Test: `test_main_worker.py`

**Interfaces:**
- Consumes: `app_config._ACTIVE_KEYS` now contains the two keys; `app_config.DEFAULTS["invert_attack"]` / `["invert_defense"]` (Task 1).
- Produces:
  - `main._apply_worker_config(cfg)` return dict gains `"invert_attack"` / `"invert_defense"` (read `src.get("invert_attack", d["invert_attack"])`, `src` = the `active` slice, `d = app_config.DEFAULTS`).
  - `run_worker` computes `want_attack = w["invert_attack"]` / `want_defense = w["invert_defense"]` (from `_apply_worker_config` output), AFTER `w = _apply_worker_config(cfg)` and before the startup `_reassert_florr_toggles(...)` call. The per-round `_reassert_florr_toggles(want_attack, want_defense)` call is unchanged.
  - No `cfg.get("invert_attack", ...)` anywhere; no top-level read.

- [ ] **Step 1: Write / adjust the failing tests**

**1a.** In `test_main_worker.py`, `_stub_run_worker_env`'s stub `_apply_worker_config` lambda — add the two keys:

```python
    monkeypatch.setattr(main, "_apply_worker_config", lambda cfg: {
        "location": (1, 2), "farming_area": [(0, 0), (9, 9)], "farming_duration": 300,
        "short_round_limit": 2, "enemy_ai_enabled": False, "auto_switch_server": False,
        "biome": "desert",
        "enter_game_swap": {"enabled": False, "mod": "none", "digit": "1"},
        "reach_area_swap": {"enabled": False, "mod": "none", "digit": "1"},
        "invert_attack": True, "invert_defense": False,
    })
```

**1b.** Same addition to the `_swap_env` helper's inline `_apply_worker_config` stub (search `def _swap_env` — it has its own `monkeypatch.setattr(main, "_apply_worker_config", lambda cfg: {...})` with `"biome": "desert"`). Add `"invert_attack": True, "invert_defense": False,`.

**1c.** Replace `test_run_worker_toggle_wants_follow_cfg` with a version that varies the stub (not `cfg` top level, which `_apply_worker_config` now ignores):

```python
def test_run_worker_toggle_wants_come_from_active_slice(monkeypatch):
    _stub_run_worker_env(monkeypatch)
    monkeypatch.setattr(main, "_apply_worker_config", lambda cfg: {
        "location": (1, 2), "farming_area": [(0, 0), (9, 9)], "farming_duration": 300,
        "short_round_limit": 2, "enemy_ai_enabled": False, "auto_switch_server": False,
        "biome": "desert",
        "enter_game_swap": {"enabled": False, "mod": "none", "digit": "1"},
        "reach_area_swap": {"enabled": False, "mod": "none", "digit": "1"},
        "invert_attack": False, "invert_defense": True,
    })
    calls = []
    monkeypatch.setattr(main.florr_settings, "ensure_flag",
                        lambda ej, addr, want: calls.append((addr, want)) or ("unchanged", ""))
    monkeypatch.setattr(main, "lazy_theta_pathing",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    A, D = main.florr_settings.INVERT_ATTACK_ADDR, main.florr_settings.INVERT_DEFENSE_ADDR
    assert calls == [(A, 0), (D, 1), (A, 0), (D, 1)]
```

(`test_run_worker_reasserts_florr_toggles_at_startup_and_each_round` needs no change — the stub now returns `invert_attack: True` / `invert_defense: False`, so its `[(A,1),(D,0),(A,1),(D,0)]` assertion still holds. `test_run_worker_survives_toggle_failure` unchanged.)

**1d.** Add two direct `_apply_worker_config` tests near the other `test_apply_worker_config_*`:

```python
def test_apply_worker_config_reads_invert_from_active(monkeypatch):
    monkeypatch.setattr(main, "apply_map", lambda name: None)
    w = main._apply_worker_config({"version": 2, "active": {
        "map": "desert", "invert_attack": False, "invert_defense": True}})
    assert w["invert_attack"] is False
    assert w["invert_defense"] is True


def test_apply_worker_config_invert_defaults_when_absent(monkeypatch):
    monkeypatch.setattr(main, "apply_map", lambda name: None)
    w = main._apply_worker_config({"version": 2, "active": {"map": "desert"}})
    assert w["invert_attack"] is True
    assert w["invert_defense"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest test_main_worker.py -v`
Expected: FAIL — `test_apply_worker_config_reads_invert_from_active` (`KeyError: 'invert_attack'` on `w`); `test_run_worker_toggle_wants_come_from_active_slice` (still `[(A,1),(D,0),...]` because `run_worker` reads `cfg.get(...)` not `w`).

- [ ] **Step 3: Implement**

**3a.** `_apply_worker_config()` return dict — after `"reach_area_swap": src.get("reach_area_swap", d["reach_area_swap"]),` add:

```python
        "invert_attack": src.get("invert_attack", d["invert_attack"]),
        "invert_defense": src.get("invert_defense", d["invert_defense"]),
```

**3b.** `run_worker()` — DELETE this block that sits BEFORE `w = _apply_worker_config(cfg)` (currently ~769–776):

```python
    # 反转攻击键 / 反转防御键的目标值直接从整份 cfg 取(顶层键, 不在 active 切片 /
    # _apply_worker_config 输出里), 缺键回退 app_config.DEFAULTS.
    _d = app_config.DEFAULTS
    want_attack = cfg.get("invert_attack", _d["invert_attack"])
    want_defense = cfg.get("invert_defense", _d["invert_defense"])

    if "failed" in _reassert_florr_toggles(want_attack, want_defense).values():
        overlay.update(message="⚠️ 反转键未全部确认, 见日志")
```

**3c.** `run_worker()` — AFTER `CONSECUTIVE_SHORT_ROUND_LIMIT = w["short_round_limit"]` (the last of the `w[...]` unpack lines) add:

```python
    # 反转攻击键 / 反转防御键的目标值来自当前时块 (active 切片, 见 _apply_worker_config).
    # 调度器换时块会重启 worker, 所以整个 worker 生命周期用同一份就够. florr 每次进局
    # 会从账号数据把这两个字节盖回 —— 每轮进游戏后重写一次(见主循环).
    want_attack = w["invert_attack"]
    want_defense = w["invert_defense"]
    if "failed" in _reassert_florr_toggles(want_attack, want_defense).values():
        overlay.update(message="⚠️ 反转键未全部确认, 见日志")
```

**3d.** The per-round `_reassert_florr_toggles(want_attack, want_defense)` line inside `while True:` — unchanged (still references the closure `want_attack` / `want_defense`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest test_main_worker.py -v` then `venv/bin/pytest -q`.
Expected: PASS, whole suite green (baseline 499).

- [ ] **Step 5: Commit**

```bash
git add main.py test_main_worker.py
git commit -m "refactor(main): read invert_attack/defense want from active slice, not cfg top level"
```

---

### Task 3: GUI — delete sidebar switches, add time-block-editor switches, README

**Files:**
- Modify: `gui_app.py` — delete `inv_box` block in `_build` (around `gui_app.py:174-188`)
- Modify: `gui_schedule.py`
  - `TimeBlockEditor._build` — add 2 `CTkSwitch` after `self._autosw` (around `gui_schedule.py:244`)
  - `_collect()` — add 2 kwargs to `blk.update(...)` (around `gui_schedule.py:390-391`)
  - `block_to_active()` — add 2 keys (around `gui_schedule.py:46-47`)
  - `new_block_template()` — add 2 keys (around `gui_schedule.py:98`)
- Modify: `README.md` — the `## florr 反转键（反转攻击 / 反转防御）` section
- Test: `test_gui_app.py`, `test_gui_schedule.py`

**Interfaces:**
- Consumes: Task 1's `_ACTIVE_KEYS` + defaults. Nothing from Task 2.
- Produces:
  - `gui_app.App` no longer defines `invert_attack_switch` / `invert_defense_switch`. `_persist_flag` still exists (used by `_persist_afk`).
  - `gui_schedule.block_to_active(block)` output contains `invert_attack` / `invert_defense` (bool, default True/False, missing key → default).
  - `gui_schedule.new_block_template(cfg)` output contains `"invert_attack": True`, `"invert_defense": False`.
  - `TimeBlockEditor` instance attrs `self._inv_attack` / `self._inv_defense` (`ctk.CTkSwitch`); `_collect` writes `invert_attack` / `invert_defense` bools.

- [ ] **Step 1: Write the failing tests**

**1a.** `test_gui_schedule.py` — add to the loadout/active test area:

```python
class TestInvertTogglesInGuiSchedule:
    def test_block_to_active_carries_invert(self):
        blk = _blk(invert_attack=False, invert_defense=True)
        act = gs.block_to_active(blk)
        assert act["invert_attack"] is False
        assert act["invert_defense"] is True

    def test_block_to_active_invert_defaults_when_missing(self):
        blk = _blk()
        blk.pop("invert_attack", None)
        blk.pop("invert_defense", None)
        act = gs.block_to_active(blk)
        assert act["invert_attack"] is True
        assert act["invert_defense"] is False

    def test_new_block_template_invert_defaults(self):
        cfg = {"profiles": [{"alias": "默认", "dir": "d"}], "schedule": []}
        tpl = gs.new_block_template(cfg)
        assert tpl["invert_attack"] is True
        assert tpl["invert_defense"] is False
```

(Check `test_gui_schedule.py`'s `_blk(**kw)` helper does `base.update(kw)` — it does; kwargs pass through.)

**1b.** `test_gui_app.py` — the existing `class TestInvertTogglePersistence` tests `_persist_flag` with `invert_attack` / `invert_defense`. Retarget it to `afk_enabled` (the remaining real caller) and rename:

```python
class TestPersistFlag:
    def test_persist_flag_writes_key_and_reloads(self, monkeypatch, tmp_path):
        import app_config
        p = tmp_path / "config.json"
        monkeypatch.setattr(app_config, "CONFIG_PATH", str(p))
        app_config.save_config(app_config.load_config())

        import gui_app
        obj = type("X", (), {})()
        obj._cfg = app_config.load_config()
        gui_app.App._persist_flag(obj, "afk_enabled", 1)
        assert app_config.load_config()["afk_enabled"] is True
        assert obj._cfg["afk_enabled"] is True
        gui_app.App._persist_flag(obj, "afk_enabled", 0)
        assert app_config.load_config()["afk_enabled"] is False
```

(If the old class name / body differs, replace the whole `class TestInvertTogglePersistence` with the above.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest test_gui_schedule.py::TestInvertTogglesInGuiSchedule test_gui_app.py::TestPersistFlag -v`
Expected: FAIL — `block_to_active` has no `invert_attack` key (`KeyError`); `new_block_template` likewise; `TestPersistFlag` fails only if the rename isn't in place yet (class not found).

- [ ] **Step 3: Implement**

**3a.** `gui_app.py` `_build` — DELETE the entire `inv_box` block:

```python
        inv_box = ctk.CTkFrame(side, fg_color="transparent")
        inv_box.grid(row=6, column=0, padx=10, pady=(0, 10), sticky="ew")
        ctk.CTkLabel(inv_box, text="florr 反转键", font=("", 12)).pack()
        self.invert_attack_switch = ctk.CTkSwitch(
            inv_box, text="反转攻击键",
            command=lambda: self._persist_flag("invert_attack", self.invert_attack_switch.get()))
        self.invert_attack_switch.pack(pady=(4, 0), anchor="w")
        if self._cfg["invert_attack"]:
            self.invert_attack_switch.select()
        self.invert_defense_switch = ctk.CTkSwitch(
            inv_box, text="反转防御键",
            command=lambda: self._persist_flag("invert_defense", self.invert_defense_switch.get()))
        self.invert_defense_switch.pack(pady=(2, 0), anchor="w")
        if self._cfg["invert_defense"]:
            self.invert_defense_switch.select()
```

(Match the exact lines in the file — the block starts at `inv_box = ctk.CTkFrame(...)` and ends at the second `.select()`.)

**3b.** `gui_schedule.py` `TimeBlockEditor._build` — after `self._autosw.pack(anchor="w", padx=12, pady=6)` (the auto-switch-server switch), before the loadout-swap section, add:

```python
        self._inv_attack = ctk.CTkSwitch(self, text="反转攻击键")
        if self._block.get("invert_attack", True):
            self._inv_attack.select()
        self._inv_attack.pack(anchor="w", padx=12, pady=(6, 0))
        self._inv_defense = ctk.CTkSwitch(self, text="反转防御键")
        if self._block.get("invert_defense", False):
            self._inv_defense.select()
        self._inv_defense.pack(anchor="w", padx=12, pady=(0, 6))
```

**3c.** `gui_schedule.py` `_collect()` — in the `blk.update(...)` call, after `auto_switch_server=bool(self._autosw.get()),` add:

```python
            invert_attack=bool(self._inv_attack.get()),
            invert_defense=bool(self._inv_defense.get()),
```

**3d.** `gui_schedule.py` `block_to_active()` — in the returned dict, after `"auto_switch_server": bool(block["auto_switch_server"]),` add:

```python
        "invert_attack": bool(block.get("invert_attack", True)),
        "invert_defense": bool(block.get("invert_defense", False)),
```

**3e.** `gui_schedule.py` `new_block_template()` — after `"enemy_ai_enabled": True, "auto_switch_server": True,` add:

```python
        "invert_attack": True, "invert_defense": False,
```

**3f.** `README.md` — in the `## florr 反转键（反转攻击 / 反转防御）` section, change the sentences that describe "two global config.json keys" + "GUI sidebar has two switches" to:

> 每个调度时块单独配 `invert_attack`（默认开）/ `invert_defense`（默认关），在时块编辑器里勾。调度器切到某个时块时会重启 worker，把该时块的值写进 florr。开→每轮写 `1`，关→每轮写 `0`（强制关，不是「留 florr 账号里的值」）。

Keep the address-constants + recalibration paragraphs unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest test_gui_schedule.py test_gui_app.py -v` then `venv/bin/pytest -q`.
Expected: PASS, whole suite green.

> GUI widgets (`_inv_attack` / `_inv_defense` render, sidebar layout after `inv_box` removal) are not auto-tested — repo norm (memory `gui-phase2-shipped`).

- [ ] **Step 5: Commit**

```bash
git add gui_app.py gui_schedule.py README.md test_gui_app.py test_gui_schedule.py
git commit -m "feat(gui): invert-attack/defense switches move to time-block editor"
```

---

## Self-Review

**1. Spec coverage:**

| Spec 要点 | Task |
|---|---|
| `_ACTIVE_KEYS` 加两键 | Task 1 (3a) + `test_now_in_active_keys` |
| 顶层 `_coerce` / `DEFAULTS_V2` / `migrate_v1` 删三处 | Task 1 (3b/3c/3d) + `test_not_top_level_in_defaults_v2` / `test_load_has_them_per_block_not_top_level` |
| 扁平 `DEFAULTS` 保留两键 | Task 1 (3f) + `test_flat_defaults_unchanged` |
| `_coerce_block` 宽松(缺键/非 bool → 默认,不丢块) | Task 1 (3e) + `test_old_block_without_keys_kept_with_defaults` / `test_block_non_bool_falls_back_to_default` |
| `migrate_v1` block-loop + `DEFAULTS_V2["active"]` 自动带 | Task 1 (comment in 3c/3d) + `test_v1_migration_per_block_and_active_not_top_level` |
| `_apply_worker_config` 加两键(从 active 切片) | Task 2 (3a) + `test_apply_worker_config_reads_invert_from_active` |
| `run_worker` want 从 `w` 读,startup 段挪到 `w=` 之后 | Task 2 (3b/3c) + `test_run_worker_toggle_wants_come_from_active_slice` |
| 每轮调用不变 | Task 2 (3d) + `test_run_worker_reasserts_florr_toggles_at_startup_and_each_round` |
| 删 GUI 侧栏两个开关 | Task 3 (3a) |
| 时块编辑器加两个开关 + `_collect` | Task 3 (3b/3c) |
| `block_to_active` / `new_block_template` 带两键 | Task 3 (3d/3e) + `TestInvertTogglesInGuiSchedule` |
| `_persist_flag` 保留(afk 用) | Task 3 (3a keeps it) + `TestPersistFlag` |
| README 改 per-block | Task 3 (3f) |
| 语义不变(开→1/关→0) | 全程未改 `ensure_flag` / `_reassert_florr_toggles` |

无缺口。

**2. Placeholder scan:** 无 TBD / 无 "add validation" / 每个实现步骤给了确切代码 + 锚点。Task 3 (3a/3f) 说 "match the exact lines" —— 因为那是删除/改写已存在的块,实现者需对齐当前文件,已给出完整当前内容。

**3. Type consistency:**
- `invert_attack` / `invert_defense` 键名全程一致(Task 1/2/3 + 所有测试)。
- `_bool_or(key, dflt)` — Task 1 (3e) 定义 + 同处使用,一致。
- `_apply_worker_config` 返回键 `invert_attack` / `invert_defense`(Task 2 3a)== `run_worker` 读 `w["invert_attack"]`(Task 2 3c)== stub 里的键(Task 2 1a/1b),一致。
- `_reassert_florr_toggles(want_attack, want_defense)` 签名未变(Task 2 只改 want 来源)。
- `self._inv_attack` / `self._inv_defense`(Task 3 3b)== `_collect` 里 `self._inv_attack.get()`(3c),一致。
- `block_to_active` 默认 `block.get("invert_attack", True)` == `new_block_template` `True` == `_coerce_block` `_bool_or(..., DEFAULTS["invert_attack"])`(=True),一致。
