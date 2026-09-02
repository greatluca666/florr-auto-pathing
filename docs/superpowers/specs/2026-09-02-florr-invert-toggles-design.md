# worker 启动时按配置强制 florr 反转攻击键 / 反转防御键 — design

## Problem

现在 [florr_settings.py](../../../florr_settings.py) 的 `ensure_invert_attack_on()`
**无条件**在 worker 启动 + 每轮把 florr 的「反转攻击键」字节 `0x53430E` 写成 1
(见 [main.py](../../../main.py) `_reassert_invert_attack`)。没有开关。

用户要:

1. 反转攻击键可配 —— 开 = 每轮写 1,关 = 每轮写 0。
2. 新增反转防御键(字节 `0x534310`,用户提供),同样开=写 1 / 关=写 0。

两个都是**全局** GUI 设置(不是每个刷怪时块),跟 `afk_enabled` 一类。

```js
const M = window.Module;
M.HEAPU8[0x53430e]        // 反转攻击键  1/0
M.HEAPU8[0x534310]        // 反转防御键  1/0
```

## Goal

`config.json` v2 顶层加两个 bool:

```jsonc
"invert_attack":  true,    // 默认 true  —— worker 每轮把 0x53430E 写成 (1 if true else 0)
"invert_defense": false    // 默认 false —— 同上, 0x534310
```

worker 启动探一次 + 每轮进游戏后一次,按这两个值把对应字节写成 1 / 0。GUI 侧栏
AFK 开关旁两个 `CTkSwitch` 控制,切换即落盘,下一轮 worker 生效。

## Non-goals

- **不从 GUI 直接 CDP 写。** GUI 只落盘 config,CDP 写全归 worker(跟现有
  per-time-block 设置一个路子)。
- **不做「关=不管这个字节」。** 用户明确要 关=写 0(强制关),不是「留 florr
  账号里的值」。
- **不自动重找地址。** florr 大版本重构 → 地址漂移 → worker 日志报
  `addr-out-of-range` / `not-bool`,让用户重跑 `settings_finder.js`。跟现有
  反转攻击键的处理一致。
- **不迁移 / 不动别的 florr 设置。** 只这两个反转键。
- **默认值保底现状:** `invert_attack` 默认 `true` —— 旧 config 没这键 → 取默认
  → 行为跟现在(无条件写 1)完全一样,不会静默把 bot 的伤害关掉。
- **不加「只启动一次不每轮」选项。** 每轮重写是必须的(florr 每次从菜单进局
  会从账号数据把这两个字节盖回),沿用反转攻击键现有的每轮 re-assert。

## 配置 schema (app_config.py)

- `DEFAULTS`(扁平,v1 迁移取值用)加:
  ```python
  "invert_attack": True,
  "invert_defense": False,
  ```
- `DEFAULTS_V2` 顶层加同样两键(跟 `afk_enabled` 并列)。
- `_coerce()` 在 `cfg["afk_enabled"] = ...` 那行旁边加:
  ```python
  cfg["invert_attack"] = raw["invert_attack"] if isinstance(raw.get("invert_attack"), bool) else True
  cfg["invert_defense"] = raw["invert_defense"] if isinstance(raw.get("invert_defense"), bool) else False
  ```
- `migrate_v1()` 的返回 dict 里带上 `"invert_attack": flat["invert_attack"]`,
  `"invert_defense": flat["invert_defense"]`。
- `_coerce_v1()` 无需新分支 —— 这两键在扁平 `DEFAULTS` 里、值是 bool,落到函数
  末尾那个 `else: ok = isinstance(val, bool)`,缺失时从 `copy.deepcopy(DEFAULTS)`
  拿默认。
- **不进 `_ACTIVE_KEYS`**(全局,不是时块参数)。`block_to_active` /
  `_coerce_block` / gui_schedule 一律不碰。

## florr_settings.py

把攻击键专用的 `ensure_invert_attack_on` 泛化成任意 bool 字节:

- 加常量 `INVERT_DEFENSE_ADDR = 0x534310`(挨着 `INVERT_ATTACK_ADDR = 0x53430E`)。
- `_JS_TEMPLATE` 加 `{want}` 占位;逻辑改成:
  ```
  const A = {addr}, W = {want};
  ... 越界 → addr-out-of-range
  const before = u8[A];
  if (before > 1) return {ok:false, reason:"not-bool:"+before};
  if (before !== W) u8[A] = W;
  return {ok:true, before, after:u8[A]};
  ```
- `_js(addr)` → `_js(addr, want)`。
- `ensure_invert_attack_on(eval_js, addr=None)` → `ensure_flag(eval_js, addr, want)`:
  - `want`: 0 或 1。
  - 返回 `(status, detail)`:
    - `"unchanged"` —— `before == want`
    - `"changed"`   —— 本来不等,已写成 `want`
    - `"failed"`    —— detail = 原因(`addr-not-calibrated` / `cdp-error:...` /
      `no-value` / `bad-json` / `addr-out-of-range` / `not-bool:N` / `unknown`)
  - `addr is None` → `("failed", "addr-not-calibrated")`(不变)。
- 旧名 `ensure_invert_attack_on` 删掉(唯一调用方是 main,一起改)。模块 docstring
  更新成「两个反转键」。

## main.py

- `import florr_settings` 不变。
- `_reassert_invert_attack()` → `_reassert_florr_toggles(want_attack, want_defense)`:
  ```python
  def _reassert_florr_toggles(want_attack, want_defense):
      """按配置把 florr 的反转攻击键 / 反转防御键字节写成 1/0. florr 每次进局会从
      账号数据盖回, 所以每轮都要重写. 返回 dict {"attack": status, "defense": status}.
      unchanged 静默; changed / failed 打日志. 任一 failed 都不中断 worker."""
      out = {}
      for name, addr, want in (
          ("attack", florr_settings.INVERT_ATTACK_ADDR, 1 if want_attack else 0),
          ("defense", florr_settings.INVERT_DEFENSE_ADDR, 1 if want_defense else 0),
      ):
          status, detail = florr_settings.ensure_flag(cdp_bridge.eval_js, addr, want)
          out[name] = status
          label = "反转攻击键" if name == "attack" else "反转防御键"
          if status == "changed":
              print(f"✅ {label} 已(重新)设为 {want}")
          elif status == "failed":
              print(f"⚠️ {label} 未确认 ({detail}) —— 手动到 设置→控制 里勾/取消")
      return out
  ```
- `run_worker(cfg)`:
  - 在 `w = _apply_worker_config(cfg)` 附近取全局值:
    ```python
    d = app_config.DEFAULTS
    want_attack = cfg.get("invert_attack", d["invert_attack"])
    want_defense = cfg.get("invert_defense", d["invert_defense"])
    ```
    (`cfg` 是 `run_worker` 收到的整份 config;这两键在顶层,不在 `active` 切片。)
  - 启动探一次(原 `if _reassert_invert_attack() == "failed":` 那处):
    ```python
    st = _reassert_florr_toggles(want_attack, want_defense)
    if "failed" in st.values():
        overlay.update(message="⚠️ 反转键未全部确认, 见日志")
    ```
  - 每轮那处(原 `_reassert_invert_attack()` 单独一行)→
    `_reassert_florr_toggles(want_attack, want_defense)`。

## GUI (gui_app.py)

- 侧栏 AFK box 附近加两个 `CTkSwitch`:「反转攻击键」「反转防御键」。
  - 初值:`self._cfg["invert_attack"]` / `["invert_defense"]` → `.select()`。
  - `command` 回调 → `_persist_flag("invert_attack", bool(sw.get()))`(仿
    `_persist_afk`:读 cfg → 改键 → `app_config.save_config`)。
  - 无平台门(CDP 写跟 pyautogui 无关,跨平台)。
- 一个共用 `_persist_flag(key, value)` 私有方法,两个开关都用。

## 测试

- `test_florr_settings.py`(重写):`_fake_eval` 造 CDP 返回。
  - `want=1, before=0` → `("changed", "")`,JS 里 `u8[A]` 变 1(用返回的
    `after` 断言 —— fake 里模拟 write)。
  - `want=1, before=1` → `("unchanged", "")`。
  - `want=0, before=1` → `("changed", "")`,`after == 0`。
  - `want=0, before=0` → `("unchanged", "")`。
  - `before=2` → `("failed", "not-bool:2")`。
  - `addr=None` → `("failed", "addr-not-calibrated")`。
  - eval_js 抛异常 → `("failed", "cdp-error:...")`。
  - `ok:false, reason:"addr-out-of-range"` → `("failed", "addr-out-of-range")`。
  - 非 JSON value → `("failed", "bad-json")`;无 value → `("failed", "no-value")`。
  - `INVERT_DEFENSE_ADDR == 0x534310`,`INVERT_ATTACK_ADDR == 0x53430E`。
- `test_app_config.py`:
  - `DEFAULTS["invert_attack"] is True`,`DEFAULTS["invert_defense"] is False`;
    `DEFAULTS_V2` 同。
  - 缺键的 v2 config → load 后 `invert_attack=True` / `invert_defense=False`。
  - 显式 `false` / `true` round-trip。
  - 非 bool(`"yes"` / `1`)→ 回落默认 + 不炸。
  - v1 迁移:迁出来的 cfg 顶层带这两键(默认值)。
  - `"invert_attack" not in app_config._ACTIVE_KEYS`。
- `test_main_worker.py`:
  - `_reassert_florr_toggles(True, False)` → `ensure_flag` 被调 2 次,参数
    `(eval_js, INVERT_ATTACK_ADDR, 1)` 和 `(eval_js, INVERT_DEFENSE_ADDR, 0)`。
  - `_reassert_florr_toggles(False, True)` → `(..., ATTACK_ADDR, 0)` /
    `(..., DEFENSE_ADDR, 1)`。
  - monkeypatch `ensure_flag` 返回 `("failed", "x")` → `run_worker` 照进主循环,
    不 SystemExit;overlay 收到含「反转键」的警告(startup 那次)。
  - `run_worker` startup + 第 1 轮各调 `_reassert_florr_toggles` 一次(掐在寻路)。
  - 更新 `_stub_run_worker_env`:stub `_reassert_florr_toggles`(或让它走 stub 的
    `florr_settings.ensure_flag`),别让老 `ensure_invert_attack_on` 名字残留。
  - 现有 `test_run_worker_reasserts_invert_attack_*` 两个 → 改名 + 改断言到新函数。
- `test_gui_app.py`:两个开关 → `_persist_flag` → `save_config` 收到的 cfg
  含更新后的 `invert_attack` / `invert_defense`。

## 已接受的风险

1. `0x534310` 是用户提供、未经 `settings_finder.js` 二次核对的地址。florr 更新
   后两个地址都可能漂移 —— `ensure_flag` 的 `not-bool` / `addr-out-of-range`
   分支会归 `failed` + 警告,worker 不中断。
2. 裸写字节 florr 认不认「反转防御键」的即时变化(可能它只在设置菜单开合时读)
   —— 用户接入时验证一次:开开关、启动 worker、看游戏里花瓣是不是一直张开/收拢。
   跟反转攻击键当初一样是「已接受、用户验证」。
3. 真 Windows / live florr 没跑过(仓库惯例)。
