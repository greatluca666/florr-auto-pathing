# 反转攻击键 / 反转防御键 开关: 全局 → 每时块 — design

## Problem

刚 ship 的 `invert_attack` / `invert_defense`(merge `ba89f39`)是**全局**顶层
config + GUI 侧栏两个开关。用户要改成**每时块**单独配 —— 跟
`enemy_ai_enabled` / `auto_switch_server` / `enter_game_swap` / `reach_area_swap`
一样,放进每个调度时块,GUI 在时块编辑器里配。

## Goal

把 `invert_attack` / `invert_defense` 从"顶层全局"挪进 `_ACTIVE_KEYS`(= 每个
时块 + `active` 切片的键)。worker 从 `_apply_worker_config` 的输出(active 切片)
读,不再从 `cfg` 顶层读。GUI 侧栏那两个开关删掉,时块编辑器加两个开关。

行为不变:每时块 `invert_attack` 默认 **开**、`invert_defense` 默认 **关**;
worker 启动 + 每轮把对应 WASM 字节写成 `1 if want else 0`(开→1,关→0)。
调度器换时块会重启 worker,所以 per-block 值天然生效(跟 `farming_area` 等一样)。

## Non-goals

- **不动 `florr_settings.ensure_flag` / `_reassert_florr_toggles` 的实现。**
  只改"want 从哪来"(顶层 → active 切片)。
- **不保留全局回退开关。** 完全 per-block。
- **不改开关语义。** 开=每轮写 1,关=每轮写 0(强制关,不是"留 florr 账号值")。
- **不迁移旧 config 的顶层 `invert_attack`。** `ba89f39` 才合并几分钟,没有真实
  用户 config 带顶层这键。旧 v2 时块没这键 → `_coerce_block` 回落默认(不丢块)。
- **GUI 控件本身不做自动化测试**(仓库惯例,GUI smoke 从未真跑)。

## app_config.py

- `_ACTIVE_KEYS` 尾部加 `"invert_attack", "invert_defense"`。
- **删掉**顶层的三处:
  - `_coerce()` 里 `cfg["invert_attack"] = ...` / `cfg["invert_defense"] = ...` 两行。
  - `DEFAULTS_V2` 里 `"invert_attack": True,` / `"invert_defense": False,` 两行。
  - `migrate_v1()` 返回 dict 里 `"invert_attack": flat["invert_attack"],` /
    `"invert_defense": flat["invert_defense"],` 两行。
  - `migrate_v1` 的 `for k in _ACTIVE_KEYS: block[k] = deepcopy(flat[k])` 和
    `DEFAULTS_V2["active"] = {k: deepcopy(DEFAULTS[k]) for k in _ACTIVE_KEYS}`
    会自动把这两键带进时块 + active —— 不用手加。
- 扁平 `DEFAULTS` 里 `"invert_attack": True` / `"invert_defense": False` **保留**
  (`_ACTIVE_KEYS` 迁移 + 空 schedule 时 active 兜底读它)。注释更新:不再提"顶层"。
- `_coerce_block()`:**宽松**校验,不像 `enemy_ai_enabled` 那样 `return None`。
  在 `eai, asw = ...` 那段附近加:
  ```python
  def _bool_or(key, dflt):
      v = raw.get(key, dflt)
      return v if isinstance(v, bool) else dflt
  ```
  返回 dict 里(`"enter_game_swap": ...` 那两行旁边)加:
  ```python
  "invert_attack": _bool_or("invert_attack", DEFAULTS["invert_attack"]),
  "invert_defense": _bool_or("invert_defense", DEFAULTS["invert_defense"]),
  ```
  > 旧时块没这两键 → `raw.get(key, dflt)` 拿默认,不 KeyError、不丢块。
  > 非 bool 值 → 回落默认。

## main.py

- `_apply_worker_config()` 返回 dict 加两行(`d = app_config.DEFAULTS`,`src` 是
  active 切片):
  ```python
  "invert_attack": src.get("invert_attack", d["invert_attack"]),
  "invert_defense": src.get("invert_defense", d["invert_defense"]),
  ```
- `run_worker()`:
  - **删掉** `w = _apply_worker_config(cfg)` **之前**那段(当前约 769–776 行):
    ```
    # 反转攻击键 / 反转防御键的目标值直接从整份 cfg 取(顶层键 ...)
    _d = app_config.DEFAULTS
    want_attack = cfg.get("invert_attack", _d["invert_attack"])
    want_defense = cfg.get("invert_defense", _d["invert_defense"])

    if "failed" in _reassert_florr_toggles(want_attack, want_defense).values():
        overlay.update(message="⚠️ 反转键未全部确认, 见日志")
    ```
  - 在 `w = _apply_worker_config(cfg)` **之后**(`CONSECUTIVE_SHORT_ROUND_LIMIT
    = w["short_round_limit"]` 那行下面)加:
    ```python
    # 反转攻击键 / 反转防御键的目标值来自当前时块 (active 切片). 调度器换时块会
    # 重启 worker, 所以这里取一次、整个 worker 生命周期用同一份就够.
    want_attack = w["invert_attack"]
    want_defense = w["invert_defense"]
    if "failed" in _reassert_florr_toggles(want_attack, want_defense).values():
        overlay.update(message="⚠️ 反转键未全部确认, 见日志")
    ```
  - 每轮那次 `_reassert_florr_toggles(want_attack, want_defense)`(约 827 行)
    **不动** —— `want_attack` / `want_defense` 现在是循环外的闭包变量,仍在作用域。
  - `import app_config` 保留(`_apply_worker_config` 已经用它;`_d` 局部变量删掉后
    如无其它引用可留 import)。

## gui_app.py

- **删掉** `_build` 里 `inv_box` 那一段(当前约 174–188 行:`inv_box` +
  `invert_attack_switch` + `invert_defense_switch`)。
- `_persist_flag(self, key, value)` **保留** —— `_persist_afk` 还委托给它。
- grid `row=6` 空出来了,不用补(afk_box 是 row=5,下面就是主区)。

## gui_schedule.py

- `TimeBlockEditor._build`:在 `self._autosw ... .pack(...)`(约 244 行)之后、
  loadout-swap 那段之前,加:
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
- `_collect()`:`blk.update(...)` 里 `auto_switch_server=bool(self._autosw.get()),`
  之后加:
  ```python
  invert_attack=bool(self._inv_attack.get()),
  invert_defense=bool(self._inv_defense.get()),
  ```
- `block_to_active(block)`:返回 dict 里 `"auto_switch_server": bool(...)` 之后加:
  ```python
  "invert_attack": bool(block.get("invert_attack", True)),
  "invert_defense": bool(block.get("invert_defense", False)),
  ```
- `new_block_template(cfg)`:返回 dict 里 `"enemy_ai_enabled": True,
  "auto_switch_server": True,` 之后加:
  ```python
  "invert_attack": True, "invert_defense": False,
  ```

## 测试

- `test_app_config.py`(改 `TestInvertToggles`):
  - `"invert_attack"` / `"invert_defense"` **in** `app_config._ACTIVE_KEYS`。
  - `DEFAULTS["invert_attack"] is True` / `["invert_defense"] is False` 不变。
  - `DEFAULTS_V2` 顶层 **不再有** `invert_attack`(`"invert_attack" not in
    DEFAULTS_V2`);但 `DEFAULTS_V2["active"]["invert_attack"] is True` /
    `["invert_defense"] is False`。
  - `load_config()` 结果顶层 **不再有** `invert_attack`;每个 `schedule` 时块 +
    `active` 有(bool)。
  - 时块显式 `invert_attack=False` / `invert_defense=True` → round-trip 保留。
  - 时块非 bool(`"yes"` / `1`)→ 回落默认(attack True / defense False),**块不丢**。
  - 旧时块(`_v2_block()` 里 `pop` 掉这两键)→ load 后块还在,值 = 默认。
  - v1 迁移:迁出来的时块 + `active` 都带默认值;顶层不带。
- `test_main_worker.py`:
  - `_apply_worker_config({"version":2,"active":{"map":"desert",
    "invert_attack":False,"invert_defense":True}})` → `w["invert_attack"] is False`
    / `w["invert_defense"] is True`。
  - `_apply_worker_config({"version":2,"active":{"map":"desert"}})` →
    `w["invert_attack"] is True` / `w["invert_defense"] is False`(默认)。
  - `_stub_run_worker_env` 的 stub `_apply_worker_config` lambda 加
    `"invert_attack": True, "invert_defense": False`;`_swap_env` 同。
  - `test_run_worker_reasserts_florr_toggles_at_startup_and_each_round` /
    `test_run_worker_toggle_wants_follow_cfg` 改成:want 来自 `_apply_worker_config`
    的返回(stub 里给 `invert_attack` / `invert_defense`),不再是 `cfg` 顶层。
    默认(stub True/False)→ `[(A,1),(D,0),(A,1),(D,0)]`;stub 给 False/True →
    `[(A,0),(D,1),(A,0),(D,1)]`。
  - `test_run_worker_survives_toggle_failure` / `_reassert_florr_toggles` 单测
    不受影响(那是直接调函数)。
- `test_gui_app.py`:`TestInvertTogglePersistence` → 改成测 `_persist_flag` 对
  `afk_enabled`(唯一剩下的真实调用方),或直接删掉靠现有 afk 测试覆盖。保留一个
  `_persist_flag` 纯逻辑测试即可。断言 GUI 不再有 `invert_attack_switch` 属性可
  不测(GUI 不自动化)。
- `test_gui_schedule.py`:
  - `block_to_active` 输出带 `invert_attack` / `invert_defense`(默认 True / False,
    缺键也是)。
  - `new_block_template` 输出带 `invert_attack: True` / `invert_defense: False`。

## README.md

`ba89f39` 刚给 `## florr 反转键（反转攻击 / 反转防御）` 小节写了"两个全局
config.json 键 + 侧栏两个开关"。现在改 per-block —— 把那两句改成:每个调度时块
单独配 `invert_attack`(默认开)/ `invert_defense`(默认关),在时块编辑器里;
调度器换时块时重启 worker 应用。其余(开→1/关→0、地址、重标定)不变。

## 已接受的风险

1. `0x534310`(反转防御键地址)仍是用户提供、未 `settings_finder.js` 二次核对。
2. florr 认不认对这两个字节的裸写(不开关设置菜单)—— 真机验证,同 `ba89f39`。
3. 真 Windows / live florr 没跑过(仓库惯例)。
