# florr invert-attack / invert-defense toggles — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 两个全局 `config.json` 开关 `invert_attack` / `invert_defense`,worker 启动 + 每轮把 florr 的反转攻击键(`0x53430E`)/ 反转防御键(`0x534310`)字节写成 1(开)或 0(关)。

**Architecture:** `florr_settings.ensure_invert_attack_on` 泛化成 `ensure_flag(eval_js, addr, want)`(want∈{0,1}),差异只是 addr + 目标值。`main._reassert_invert_attack` 泛化成 `_reassert_florr_toggles(want_attack, want_defense)`,`run_worker` 从整份 `cfg` 顶层读这两个 bool。GUI 侧栏两个 `CTkSwitch` 落盘,不直接 CDP 写。

**Tech Stack:** Python 3、pytest、CDP via `cdp_bridge.eval_js`、CustomTkinter。venv 在 `venv/`(symlink),测试 `venv/bin/pytest`。

## Global Constraints

- 两键是**全局**顶层 config(跟 `afk_enabled` 并列),**不进** `app_config._ACTIVE_KEYS`,`block_to_active` / `_coerce_block` / gui_schedule 不碰。
- 默认值:`invert_attack = True`(保留现状 —— 旧 config 无键时行为跟现在无条件写 1 完全一致),`invert_defense = False`。
- 开关语义:开 → 每轮写 1;关 → 每轮写 0。`ensure_flag` 只在 `before != want` 时写。
- `ensure_flag` 返回 `(status, detail)`:`"unchanged"` / `"changed"` / `"failed"`。`failed` 的 detail 沿用现有原因串(`addr-not-calibrated` / `cdp-error:...` / `no-value` / `bad-json` / JS 传回的 `addr-out-of-range` / `not-bool:N` / `no-wasm-memory` / `unknown`)。
- 任一 `failed` 都**不中断 worker**,只 `print` `⚠️` + 悬浮窗提示(沿用 `_reassert_invert_attack` 现风格)。`unchanged` 静默,`changed` / `failed` 才打日志。
- 每轮都重写(florr 每次进局从账号数据盖回)—— 沿用现有每轮 re-assert 调用点,不加「只启动一次」选项。
- GUI 不做 CDP 写,只 `app_config.save_config`,下轮 worker 生效。无平台门。
- 地址:`INVERT_ATTACK_ADDR = 0x53430E`(已有),`INVERT_DEFENSE_ADDR = 0x534310`(新,用户提供,未二次核对)。
- 中文注释 / 中文 print,跟仓库风格一致。每个 Task 末尾一次 commit。

---

### Task 1: `florr_settings.py` — 泛化成任意 bool 字节 `ensure_flag(eval_js, addr, want)`

**Files:**
- Modify: `florr_settings.py`
- Test: `test_florr_settings.py` (rewrite)

**Interfaces:**
- Consumes: 无。
- Produces:
  - `florr_settings.INVERT_ATTACK_ADDR == 0x53430E` (不变), `florr_settings.INVERT_DEFENSE_ADDR == 0x534310` (新)。
  - `florr_settings.ensure_flag(eval_js, addr, want) -> tuple[str, str]`
    - `eval_js`: `cdp_bridge.eval_js` 那种签名 `(expression:str) -> dict` 的函数。
    - `addr`: `int | None`。`None` → `("failed", "addr-not-calibrated")`,不调 `eval_js`。
    - `want`: `0` 或 `1`。
    - 返回 `("unchanged", "")` / `("changed", "")` / `("failed", <reason>)`。
  - `florr_settings._js(addr, want) -> str` — 内部,单测断言用。
  - 旧 `ensure_invert_attack_on` **删除**。

- [ ] **Step 1: Write the failing tests**

把 `test_florr_settings.py` 整个替换成:

```python
import json

import pytest

import florr_settings as fs


def _resp(payload):
    """把一个 dict 包成 cdp_bridge.eval_js 那种返回结构(payload 会被 JSON.stringify)."""
    return {"result": {"result": {"type": "string", "value": json.dumps(payload)}}}


def test_addresses_are_calibrated_constants():
    assert fs.INVERT_ATTACK_ADDR == 0x53430E
    assert fs.INVERT_DEFENSE_ADDR == 0x534310


def test_addr_none_does_not_call_eval():
    calls = []
    assert fs.ensure_flag(lambda e: calls.append(e), None, 1) == ("failed", "addr-not-calibrated")
    assert calls == []


def test_changed_when_before_differs_from_want():
    # want=1, before=0 -> 写 1
    resp = _resp({"ok": True, "before": 0, "after": 1})
    assert fs.ensure_flag(lambda e: resp, 0xAD1234, 1) == ("changed", "")
    # want=0, before=1 -> 写 0
    resp0 = _resp({"ok": True, "before": 1, "after": 0})
    assert fs.ensure_flag(lambda e: resp0, 0xAD1234, 0) == ("changed", "")


def test_unchanged_when_before_equals_want():
    assert fs.ensure_flag(lambda e: _resp({"ok": True, "before": 1, "after": 1}), 1, 1) == ("unchanged", "")
    assert fs.ensure_flag(lambda e: _resp({"ok": True, "before": 0, "after": 0}), 1, 0) == ("unchanged", "")


@pytest.mark.parametrize("reason", ["no-wasm-memory", "addr-out-of-range", "not-bool:7"])
def test_failed_passes_through_js_reason(reason):
    assert fs.ensure_flag(lambda e: _resp({"ok": False, "reason": reason}), 1, 1) == ("failed", reason)


def test_failed_on_eval_exception():
    def boom(_e):
        raise RuntimeError("no florr tab")
    assert fs.ensure_flag(boom, 1, 1) == ("failed", "cdp-error:no florr tab")


def test_failed_when_no_value_in_response():
    resp = {"result": {"result": {"type": "object", "className": "Object"}}}
    assert fs.ensure_flag(lambda e: resp, 1, 1) == ("failed", "no-value")


def test_failed_on_bad_json():
    resp = {"result": {"result": {"type": "string", "value": "not json{"}}}
    assert fs.ensure_flag(lambda e: resp, 1, 1) == ("failed", "bad-json")


def test_failed_on_none_response():
    assert fs.ensure_flag(lambda e: None, 1, 1) == ("failed", "no-value")


def test_js_embeds_decimal_addr_and_want_and_is_stringify_iife():
    js = fs._js(0x1234, 0)
    assert "const A = 4660" in js
    assert "const W = 0" in js
    assert js.startswith("JSON.stringify((() => {")
    assert js.rstrip().endswith("})())")
    js1 = fs._js(0x1234, 1)
    assert "const W = 1" in js1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest test_florr_settings.py -v`
Expected: FAIL — `AttributeError: module 'florr_settings' has no attribute 'ensure_flag'` / `INVERT_DEFENSE_ADDR`.

- [ ] **Step 3: Rewrite `florr_settings.py`**

Full new file content:

```python
"""worker 启动 / 每轮按 config 把 florr.io 的两个「反转」控制键强制成 1(开)或 0(关):
  设置→控制→反转攻击键  (Invert attack button)  —— 字节 INVERT_ATTACK_ADDR
  设置→控制→反转防御键  (Invert defend button)  —— 字节 INVERT_DEFENSE_ADDR

bot 自己不按攻击键, 靠「反转攻击键」让 florr 持续攻击 —— 关着的话 bot 到位、绕圈、
寻路全正常, 但一点伤害都不出. 「反转防御键」是可选的对称项.

机制: florr 是 Emscripten/WASM, 这两个设置在运行时各是静态数据段里一个 bool 字节,
游戏每帧读它. 同一个 florr build 里地址稳定; florr 发新 build 才漂移. 地址靠仓库根
的 settings_finder.js 找出来填到下面的常量.

不 import 任何 GUI 库、不 import main. 只通过参数拿 eval_js (= cdp_bridge.eval_js).
"""
import json

# settings_finder.js 找出来的地址. florr 大版本更新后可能失效 —— worker 日志报
# addr-out-of-range / not-bool 时重跑 settings_finder.js 求新值填这里. 设回 None =
# 该项静默降级成"只警告".
INVERT_ATTACK_ADDR = 0x53430E
INVERT_DEFENSE_ADDR = 0x534310

_JS_TEMPLATE = r"""JSON.stringify((() => {{
  const M = window.Module;
  const mem = M && (
    (M.asm && M.asm.memory && M.asm.memory.buffer) ||
    (M.wasmMemory && M.wasmMemory.buffer) ||
    (M.HEAPU8 && M.HEAPU8.buffer) ||
    (M.asm && M.asm.Mf && M.asm.Mf.buffer)
  );
  if (!mem) return {{ok: false, reason: "no-wasm-memory"}};
  const u8 = new Uint8Array(mem);
  const A = {addr};
  const W = {want};
  if (A < 0 || A >= u8.length) return {{ok: false, reason: "addr-out-of-range"}};
  const before = u8[A];
  if (before > 1) return {{ok: false, reason: "not-bool:" + before}};
  if (before !== W) u8[A] = W;
  return {{ok: true, before: before, after: u8[A]}};
}})())"""


def _js(addr, want):
    return _JS_TEMPLATE.format(addr=addr, want=want)


def ensure_flag(eval_js, addr, want):
    """把 florr 某个 bool 字节强制成 want(0/1). 返回 (status, detail):
      "unchanged" —— 字节本来就等于 want
      "changed"   —— 本来不等, 已写成 want
      "failed"    —— 没能确认(detail = 原因), 调用方该警告但别中断 worker

    eval_js: cdp_bridge.eval_js 那种签名的函数(expression -> CDP Runtime.evaluate
             原始返回 dict). addr: None 时直接 failed, 不调 eval_js. want: 0 或 1.
    """
    if addr is None:
        return ("failed", "addr-not-calibrated")
    try:
        resp = eval_js(_js(addr, want))
    except Exception as e:
        return ("failed", f"cdp-error:{e}")
    inner = (resp or {}).get("result", {}).get("result", {})
    if "value" not in inner:
        return ("failed", "no-value")
    try:
        data = json.loads(inner["value"])
    except (ValueError, TypeError):
        return ("failed", "bad-json")
    if not data.get("ok"):
        return ("failed", data.get("reason", "unknown"))
    return ("unchanged" if data.get("before") == want else "changed", "")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest test_florr_settings.py -v`
Expected: PASS (13 cases).

- [ ] **Step 5: Commit**

```bash
git add florr_settings.py test_florr_settings.py
git commit -m "feat(florr_settings): ensure_flag(eval_js, addr, want) — generalize invert-attack, add invert-defense addr"
```

---

### Task 2: `app_config.py` — 顶层 `invert_attack` / `invert_defense` 全局 bool

**Files:**
- Modify: `app_config.py`
  - `DEFAULTS` dict (around `app_config.py:19-36`)
  - `DEFAULTS_V2` dict (around `app_config.py:138-149`)
  - `_coerce()` (around `app_config.py:257-265`)
  - `migrate_v1()` return dict (around `app_config.py:293-305`)
- Test: `test_app_config.py`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `app_config.DEFAULTS["invert_attack"] is True`, `app_config.DEFAULTS["invert_defense"] is False`.
  - `app_config.DEFAULTS_V2["invert_attack"] is True`, `["invert_defense"] is False`.
  - `load_config()` 结果顶层永远有 `invert_attack` / `invert_defense`(bool)。缺键 → 默认;非 bool → 默认。
  - `"invert_attack"` / `"invert_defense"` **不在** `app_config._ACTIVE_KEYS`。

- [ ] **Step 1: Write the failing tests**

追加到 `test_app_config.py` 末尾(复用文件顶部已有的 `_v2_cfg` / `cfg_path`;`_v2_cfg(**kw)` 会 `c.update(kw)`,`cfg_path` 是 `monkeypatch` 掉的 `CONFIG_PATH`):

```python
class TestInvertToggles:
    def test_defaults_attack_on_defense_off(self):
        assert app_config.DEFAULTS["invert_attack"] is True
        assert app_config.DEFAULTS["invert_defense"] is False
        assert app_config.DEFAULTS_V2["invert_attack"] is True
        assert app_config.DEFAULTS_V2["invert_defense"] is False

    def test_not_in_active_keys(self):
        assert "invert_attack" not in app_config._ACTIVE_KEYS
        assert "invert_defense" not in app_config._ACTIVE_KEYS

    def test_missing_keys_get_defaults_on_load(self, cfg_path):
        cfg = _v2_cfg()
        cfg.pop("invert_attack", None)
        cfg.pop("invert_defense", None)
        app_config.save_config(cfg)
        got = app_config.load_config()
        assert got["invert_attack"] is True
        assert got["invert_defense"] is False

    def test_explicit_values_roundtrip(self, cfg_path):
        cfg = _v2_cfg(invert_attack=False, invert_defense=True)
        app_config.save_config(cfg)
        got = app_config.load_config()
        assert got["invert_attack"] is False
        assert got["invert_defense"] is True

    def test_non_bool_falls_back_to_default(self, cfg_path):
        cfg = _v2_cfg(invert_attack="yes", invert_defense=1)
        app_config.save_config(cfg)
        got = app_config.load_config()
        assert got["invert_attack"] is True     # "yes" 非 bool → 默认
        assert got["invert_defense"] is False   # 1 非 bool → 默认

    def test_v1_migration_adds_toggles(self, cfg_path):
        cfg_path.write_text(json.dumps({
            "map": "desert", "location": [1, 2], "farming_area": [[0, 0], [3, 3]],
            "farming_duration": 100, "consecutive_short_round_limit": 1,
            "enemy_ai_enabled": False, "auto_switch_server": True,
        }), encoding="utf-8")
        got = app_config.load_config()
        assert got["invert_attack"] is True
        assert got["invert_defense"] is False
```

> 若 `_v2_cfg` 顶层没自动带 `invert_attack` 键,`test_missing_keys_get_defaults_on_load` 的 `pop` 是 no-op —— 仍有效(证明 load 补默认)。`save_config` 内部会 `_coerce`,所以写出去的文件已经带默认键;测试断言的是 `load_config` 的最终产物,正确。

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest test_app_config.py::TestInvertToggles -v`
Expected: FAIL — `KeyError: 'invert_attack'` on `DEFAULTS`, load 结果无该键。

- [ ] **Step 3: Implement**

**3a.** `DEFAULTS` (扁平) — 在 `"auto_switch_server": True,` 之后、swap 键 / `"afk_enabled"` 之前加:

```python
    # florr 反转攻击键 / 反转防御键: worker 每轮把对应 WASM 字节写成 (1 if True else 0).
    # attack 默认 True —— 旧 config 无键时行为跟"无条件强制开"完全一致, 不静默关伤害.
    "invert_attack": True,
    "invert_defense": False,
```

**3b.** `DEFAULTS_V2` — 在 `"afk_enabled": False,` 之后加:

```python
    "invert_attack": True,
    "invert_defense": False,
```

**3c.** `_coerce()` — 在 `cfg["afk_enabled"] = raw["afk_enabled"] if ... else False` 那行之后加:

```python
    cfg["invert_attack"] = raw["invert_attack"] if isinstance(raw.get("invert_attack"), bool) else True
    cfg["invert_defense"] = raw["invert_defense"] if isinstance(raw.get("invert_defense"), bool) else False
```

**3d.** `migrate_v1()` — 返回 dict 里 `"afk_enabled": flat["afk_enabled"],` 之后加:

```python
        "invert_attack": flat["invert_attack"],
        "invert_defense": flat["invert_defense"],
```

> `_coerce_v1` 无需改 —— 这两键在扁平 `DEFAULTS` 里且值是 bool,循环末尾 `else: ok = isinstance(val, bool)` 已覆盖;缺失时 `copy.deepcopy(DEFAULTS)` 给默认。

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest test_app_config.py -v`
Expected: PASS(新 `TestInvertToggles` 全绿;原有不回归 —— `test_missing_file_returns_defaults_v2` / `test_v2_roundtrips` 从 `DEFAULTS_V2` 构造,两边同步长出新键)。

- [ ] **Step 5: Commit**

```bash
git add app_config.py test_app_config.py
git commit -m "feat(app_config): global invert_attack / invert_defense toggles (attack default on)"
```

---

### Task 3: `main.py` — `_reassert_florr_toggles`,`run_worker` 按 cfg 写两个字节

**Files:**
- Modify: `main.py`
  - `_reassert_invert_attack()` (`main.py:671-681`) → `_reassert_florr_toggles(...)`
  - `run_worker()` startup 段 (`main.py:761` 附近 `if _reassert_invert_attack() == "failed":`) + 每轮段 (`main.py:807` 附近单独一行 `_reassert_invert_attack()`)
- Test: `test_main_worker.py`

**Interfaces:**
- Consumes:
  - `florr_settings.ensure_flag(eval_js, addr, want)` (Task 1) → `(status, detail)`,status ∈ `{"unchanged","changed","failed"}`。
  - `florr_settings.INVERT_ATTACK_ADDR` / `INVERT_DEFENSE_ADDR` (Task 1)。
  - `app_config.DEFAULTS["invert_attack"]` / `["invert_defense"]` (Task 2)。
- Produces:
  - `main._reassert_florr_toggles(want_attack: bool, want_defense: bool) -> dict[str, str]` —— 返回 `{"attack": <status>, "defense": <status>}`。对每项调 `florr_settings.ensure_flag(cdp_bridge.eval_js, addr, 1 if want else 0)`;`changed` / `failed` 打日志,`unchanged` 静默;绝不抛。
  - `run_worker` startup + 每轮各调 `_reassert_florr_toggles(want_attack, want_defense)` 一次,`want_*` 从 `cfg.get("invert_attack", app_config.DEFAULTS["invert_attack"])` 等取。
  - 旧名 `_reassert_invert_attack` 删除。

- [ ] **Step 1: Write the failing tests**

**1a.** `test_main_worker.py` 顶部附近已有 `_stub_run_worker_env`(约 `test_main_worker.py:355`)。它里面 monkeypatch 了别的东西但没 stub `florr_settings`。找现有 `test_run_worker_reasserts_invert_attack_at_startup_and_each_round`(约 `:374`)和 `test_run_worker_survives_invert_attack_failure`(约 `:389`),把这两个整体替换成:

```python
def test_run_worker_reasserts_florr_toggles_at_startup_and_each_round(monkeypatch):
    _stub_run_worker_env(monkeypatch)
    calls = []
    monkeypatch.setattr(main.florr_settings, "ensure_flag",
                        lambda ej, addr, want: calls.append((addr, want)) or ("unchanged", ""))
    # 掐在寻路 —— 它在"每轮重写"之后, 所以第 1 轮那次也算进去
    monkeypatch.setattr(main, "lazy_theta_pathing",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    # 启动 1 次 + 第 1 轮进游戏后 1 次 = _reassert_florr_toggles 调 2 次
    # 每次内部对 attack + defense 各调一次 ensure_flag = 4 次
    assert len(calls) == 4
    A, D = main.florr_settings.INVERT_ATTACK_ADDR, main.florr_settings.INVERT_DEFENSE_ADDR
    # 默认 cfg={} → invert_attack 默认 True → want 1; invert_defense 默认 False → want 0
    assert calls == [(A, 1), (D, 0), (A, 1), (D, 0)]


def test_run_worker_toggle_wants_follow_cfg(monkeypatch):
    _stub_run_worker_env(monkeypatch)
    calls = []
    monkeypatch.setattr(main.florr_settings, "ensure_flag",
                        lambda ej, addr, want: calls.append((addr, want)) or ("unchanged", ""))
    monkeypatch.setattr(main, "lazy_theta_pathing",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({"invert_attack": False, "invert_defense": True})
    A, D = main.florr_settings.INVERT_ATTACK_ADDR, main.florr_settings.INVERT_DEFENSE_ADDR
    assert calls == [(A, 0), (D, 1), (A, 0), (D, 1)]


def test_run_worker_survives_toggle_failure(monkeypatch):
    """ensure_flag 返回 failed 时 worker 照常进主循环, 不 SystemExit, 悬浮窗警告."""
    ov = _StubOverlay()
    warned = []
    ov.update = lambda **kw: warned.append(kw.get("message"))
    _stub_run_worker_env(monkeypatch, overlay=ov)
    monkeypatch.setattr(main.florr_settings, "ensure_flag",
                        lambda ej, addr, want: ("failed", "not-bool:9"))
    monkeypatch.setattr(main, "lazy_theta_pathing",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):   # 到了主循环 = 没被 failed 掐死
        main.run_worker({})
    assert any(m and "反转" in m for m in warned)


def test_reassert_florr_toggles_returns_per_flag_status(monkeypatch):
    seen = []

    def fake(ej, addr, want):
        seen.append((addr, want))
        return ("changed", "") if addr == main.florr_settings.INVERT_ATTACK_ADDR else ("unchanged", "")

    monkeypatch.setattr(main.florr_settings, "ensure_flag", fake)
    out = main._reassert_florr_toggles(True, False)
    assert out == {"attack": "changed", "defense": "unchanged"}
    A, D = main.florr_settings.INVERT_ATTACK_ADDR, main.florr_settings.INVERT_DEFENSE_ADDR
    assert seen == [(A, 1), (D, 0)]
```

**1b.** 同文件里搜 `_reassert_invert_attack` 的其它用法(约 `:414`, `:426`, `:443` —— 都是 `monkeypatch.setattr(main, "_reassert_invert_attack", lambda: "on_already")`)。把这三处替换成:

```python
    monkeypatch.setattr(main, "_reassert_florr_toggles",
                        lambda *a, **k: {"attack": "unchanged", "defense": "unchanged"})
```

**1c.** 同文件里搜 `ensure_invert_attack_on`(约 `:462`, `:478`, `:522` —— 在 play-as-guest 相关测试里 `monkeypatch.setattr(main.florr_settings, "ensure_invert_attack_on", lambda ej, *a, **k: ("on_already", ""))`)。把这三处的属性名改成 `"ensure_flag"`,lambda 改成 `lambda ej, addr, want: ("unchanged", "")`。

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest test_main_worker.py -v`
Expected: FAIL — `AttributeError: module 'main' has no attribute '_reassert_florr_toggles'` / `main.florr_settings` 无 `ensure_flag`(Task 1 已加,若 Task 1 未合则也 fail)。

- [ ] **Step 3: Implement**

**3a.** `main.py` — 把整个 `_reassert_invert_attack()` 函数(`main.py:671-681`)替换成:

```python
def _reassert_florr_toggles(want_attack, want_defense):
    """按 config 把 florr 的反转攻击键 / 反转防御键字节写成 1(True)/0(False).
    florr 每次从菜单进局会从账号数据把这两个字节盖回 —— 所以 run_worker 启动时一次 +
    每轮进游戏后一次都要重写. 返回 {"attack": status, "defense": status}
    (status ∈ unchanged/changed/failed). unchanged 静默; changed / failed 才打日志;
    任一 failed 都不中断 worker."""
    out = {}
    for name, label, addr, want in (
        ("attack", "反转攻击键", florr_settings.INVERT_ATTACK_ADDR, 1 if want_attack else 0),
        ("defense", "反转防御键", florr_settings.INVERT_DEFENSE_ADDR, 1 if want_defense else 0),
    ):
        status, detail = florr_settings.ensure_flag(cdp_bridge.eval_js, addr, want)
        out[name] = status
        if status == "changed":
            print(f"✅ {label} 已(重新)设为 {want}")
        elif status == "failed":
            print(f"⚠️ {label} 未确认 ({detail}) —— 手动到 设置→控制 里勾/取消")
    return out
```

**3b.** `run_worker()` — 在 `w = _apply_worker_config(cfg)` 那行之后(`main.py:764` 附近)加:

```python
    _d = app_config.DEFAULTS
    want_attack = cfg.get("invert_attack", _d["invert_attack"])
    want_defense = cfg.get("invert_defense", _d["invert_defense"])
```

**3c.** `run_worker()` — startup 段,把:

```python
    if _reassert_invert_attack() == "failed":
        overlay.update(message="⚠️ 反转攻击键未确认, 见日志")
```

替换成:

```python
    if "failed" in _reassert_florr_toggles(want_attack, want_defense).values():
        overlay.update(message="⚠️ 反转键未全部确认, 见日志")
```

> 注意:上面这段 startup 调用当前在 `w = _apply_worker_config(cfg)` **之前**(`main.py:761` vs `:764`)。3b 的三行必须挪到 startup 调用**之前** —— 把 `w = _apply_worker_config(cfg)` 连同 `location = ...` 等几行,以及 3b 的三行,一起移到 `if on_guest_screen():` 块之后、startup `_reassert_florr_toggles(...)` 之前。或者更小改动:只在 startup 调用前单独算 `want_attack` / `want_defense`(直接 `cfg.get(...)`,不依赖 `w`),`_apply_worker_config` 保持原位。**采用后者** —— 把 3b 三行放在 `if on_guest_screen(): ...` 块之后、startup `_reassert_florr_toggles` 之前;`w = _apply_worker_config(cfg)` 不动。

**3d.** `run_worker()` 每轮段 — 把那行独立的:

```python
        _reassert_invert_attack()
```

(带上面「每轮重写一次」的注释)替换成:

```python
        _reassert_florr_toggles(want_attack, want_defense)
```

注释文字把「反转攻击键」改成「反转攻击键 / 反转防御键」。

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest test_main_worker.py -v`
然后 `venv/bin/pytest -q`(全量,baseline 487 → 应仍全绿,新增净 +N)。
Expected: PASS,无回归。

- [ ] **Step 5: Commit**

```bash
git add main.py test_main_worker.py
git commit -m "feat(main): _reassert_florr_toggles — write invert-attack/defense bytes per config each round"
```

---

### Task 4: `gui_app.py` — 侧栏两个开关 + `_persist_flag`

**Files:**
- Modify: `gui_app.py`
  - `_build` 里 AFK box 附近 (`gui_app.py:161-172`)
  - AFK 段方法附近 (`gui_app.py:435` 的 `_persist_afk` 旁)
- Test: `test_gui_app.py`

**Interfaces:**
- Consumes: `app_config.load_config()` / `save_config()`;`self._cfg["invert_attack"]` / `["invert_defense"]` (Task 2 保证存在)。
- Produces:
  - `App` 实例属性 `self.invert_attack_switch` / `self.invert_defense_switch`(`ctk.CTkSwitch`)。
  - `App._persist_flag(self, key, value)` —— 读 `app_config.load_config()` → `cfg[key] = bool(value)` → `app_config.save_config(cfg)` → `self._cfg = app_config.load_config()`。
  - 两个开关的 `command` 回调各调 `self._persist_flag("invert_attack", ...)` / `("invert_defense", ...)`。

- [ ] **Step 1: Write the failing test**

先看 `test_gui_app.py` 现有怎么在不起真窗口的情况下测(大概率是 monkeypatch `app_config` + 直接调方法,或用一个 `_HeadlessApp`)。追加:

```python
class TestInvertTogglePersistence:
    def test_persist_flag_writes_key_and_reloads(self, monkeypatch, tmp_path):
        import app_config
        p = tmp_path / "config.json"
        monkeypatch.setattr(app_config, "CONFIG_PATH", str(p))
        app_config.save_config(app_config.load_config())   # 落一份带默认键的

        import gui_app
        # 不起 Tk —— 直接在一个裸对象上绑方法
        obj = type("X", (), {})()
        obj._cfg = app_config.load_config()
        gui_app.App._persist_flag(obj, "invert_defense", True)
        assert app_config.load_config()["invert_defense"] is True
        assert obj._cfg["invert_defense"] is True

        gui_app.App._persist_flag(obj, "invert_attack", False)
        assert app_config.load_config()["invert_attack"] is False
```

> 若 `test_gui_app.py` 已有一个 headless App fixture / helper,优先用那个,别自造裸对象。

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest test_gui_app.py::TestInvertTogglePersistence -v`
Expected: FAIL — `AttributeError: type object 'App' has no attribute '_persist_flag'`。

- [ ] **Step 3: Implement**

**3a.** `gui_app.py` — 在 `_persist_afk` 方法(`gui_app.py:435` 附近)旁边加:

```python
    def _persist_flag(self, key, value):
        """通用: 把一个顶层 bool 开关落盘. 不做 CDP 写 —— 下一轮 worker 生效."""
        cfg = app_config.load_config()
        cfg[key] = bool(value)
        app_config.save_config(cfg)
        self._cfg = app_config.load_config()
```

**3b.** `gui_app.py` — `_build` 里 AFK box 之后(`gui_app.py:172` 附近、`# ---- 主区 ----` 之前)加:

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

> 如果侧栏 `side` 的 `grid` 行号 5 之后已被别的东西占用,把 `row=6` 换成下一个空行;`afk_box` 用的是 `row=5`,spacer 是 `row=4`(见 `side.grid_rowconfigure(4, weight=1)`)。核对 `_build` 里 `side` 的所有 `.grid(row=...)` 后填一个不冲突的值。

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest test_gui_app.py -v` 然后 `venv/bin/pytest -q`。
Expected: PASS,无回归。

> GUI 控件本身(开关渲染 / 点击)靠手动 smoke —— 见 memory `gui-phase2-shipped`,本仓库 GUI smoke 从未真跑。这里只测 `_persist_flag` 纯逻辑。

- [ ] **Step 5: Commit**

```bash
git add gui_app.py test_gui_app.py
git commit -m "feat(gui): sidebar switches for florr invert-attack / invert-defense"
```

---

## Self-Review

**1. Spec coverage:**

| Spec 要点 | Task |
|---|---|
| 顶层 `invert_attack` / `invert_defense` bool,默认 True / False | Task 2 (3a-3d) + tests |
| 不进 `_ACTIVE_KEYS` | Task 2 test `test_not_in_active_keys` |
| `_coerce` 校验(非 bool → 默认) | Task 2 (3c) + `test_non_bool_falls_back_to_default` |
| v1 迁移带这两键 | Task 2 (3d) + `test_v1_migration_adds_toggles` |
| `ensure_flag(eval_js, addr, want)` 泛化,want 0/1 | Task 1 |
| `unchanged` / `changed` / `failed` 三态 | Task 1 tests |
| `before != want` 才写 | Task 1 `test_changed_when_before_differs` / `test_unchanged_when_before_equals_want` |
| `INVERT_DEFENSE_ADDR = 0x534310` | Task 1 `test_addresses_are_calibrated_constants` |
| `addr None` → failed 不调 eval | Task 1 `test_addr_none_does_not_call_eval` |
| 旧 `ensure_invert_attack_on` 删除 | Task 1 (Step 3 全量替换) + Task 3 (1c 更新残留 stub) |
| `_reassert_florr_toggles(want_attack, want_defense)` | Task 3 (3a) |
| `run_worker` 从 `cfg` 顶层读 want | Task 3 (3b) + `test_run_worker_toggle_wants_follow_cfg` |
| 启动探一次 + 每轮一次 | Task 3 (3c/3d) + `test_run_worker_reasserts_florr_toggles_at_startup_and_each_round` |
| failed → warn-only,不中断 + 悬浮窗 | Task 3 `test_run_worker_survives_toggle_failure` |
| GUI 两个 `CTkSwitch` + 落盘不 CDP 写 | Task 4 (3a/3b) + `test_persist_flag_writes_key_and_reloads` |

无缺口。

**2. Placeholder scan:** 无 TBD / 无「add error handling」/ 每个实现步骤都给了确切代码 + 插入锚点。Task 3 (3c) 的移动说明有两个方案,已明确「采用后者」并给了具体位置。

**3. Type consistency:**
- `ensure_flag(eval_js, addr, want)` — Task 1 定义,Task 3 (3a) 调用 `florr_settings.ensure_flag(cdp_bridge.eval_js, addr, want)` 三个位置参数,一致。
- `_reassert_florr_toggles(want_attack, want_defense) -> dict` — Task 3 (3a) 定义,(3c) 调 `.values()` 判 `"failed" in`,(3d) 直接调,`test_reassert_florr_toggles_returns_per_flag_status` 断言 `{"attack":..., "defense":...}`,一致。
- 返回 status 字符串集合 `{"unchanged","changed","failed"}` — Task 1 产出、Task 3 消费,一致(无残留 `"on_already"` / `"turned_on"`)。
- config 键名 `invert_attack` / `invert_defense` — Task 2 / 3 / 4 全一致。
- `_persist_flag(self, key, value)` — Task 4 定义 + 两个 `command` lambda + 测试调用,一致。
