# florr「反转攻击键」自动开启 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `run_worker()` 启动时通过 CDP 往 florr.io 的 WASM 内存写一个字节,确保「反转攻击键」设置为开;做不到就日志+悬浮窗警告,不中断 worker。

**Architecture:** 新 `florr_settings.py` 提供 `ensure_invert_attack_on(eval_js, addr=None)`,拼一段 `JSON.stringify((()=>{...})())` 的 JS,通过传入的 `eval_js`(= `cdp_bridge.eval_js`)在 florr 标签页里跑,解析回来的字符串原始值。`main.run_worker` 在 Chrome 就绪检查 + `create_overlay()` 之后调一次。地址常量 `INVERT_ATTACK_ADDR` 初值 `None`(未标定 → 静默降级成警告),用户跑 `settings_finder.js` 求出后填。

**Tech Stack:** Python 3, pytest, Chrome DevTools Protocol(`cdp_bridge.eval_js` 已有),florr.io 的 Emscripten/WASM 运行时。

## Global Constraints

- **`florr_settings.py` 不 import 任何 GUI 库,也不 import `main`**(避免环)。只通过参数拿 `eval_js`。
- **不碰 canvas 点击、不改 `localStorage.cached_account_data`、不自动重找地址。** 地址失效 = 警告 + 让用户重跑 `settings_finder.js`。
- **失败绝不中断 worker。** `run_worker` 的接入分支任何情况下都不 `sys.exit` / 不 raise,worker 照常进主循环。
- **只保证「开」,不做「关」。** 不动「反转防御键」。
- **只在 `run_worker` 启动时查一次**,不是每轮。
- **`INVERT_ATTACK_ADDR = None` 是有意的初值**,不是占位符 TODO —— 对 `None` 有明确降级路径 + 测试。
- **`settings_finder.js` 不进打包**(`main.spec` 不加它),纯 dev 工具,不被任何代码 import。
- 每个 TDD 任务:写失败测试 → 跑它确认失败 → 最小实现 → 跑测试确认通过 → `pytest -q` 全量 → commit。测试用 `./venv/bin/python -m pytest`(工作 venv 是 `venv/`,不是 `.venv/`)。

---

## File Structure

| 文件 | 职责 | 改动 |
|---|---|---|
| `florr_settings.py` **新** | 拼 JS + 解析 CDP 返回 + 三状态语义 | 全新,~50 行 |
| `test_florr_settings.py` **新** | `ensure_invert_attack_on` 各返回路径 + `_js` 生成 | 全新 |
| `main.py` | `run_worker` 启动阶段接入 | 顶部加 `import florr_settings`;`create_overlay()` 之后加 ~10 行 |
| `test_main_worker.py` | worker 启动时调用一次 + 失败不 abort | 加 2 个 test |
| `settings_finder.js` **新** | dev 工具:控制台粘贴找地址 | 用户提供的脚本 + 头部注释块 |
| `README.md` | 新增「florr『反转攻击键』」一节 | 加 ~8 行 |

---

## Task 1: `florr_settings.py` 模块

**Files:**
- Create: `florr_settings.py`
- Test: `test_florr_settings.py`

**Interfaces:**
- Consumes: 一个 `eval_js(expression) -> dict` 函数(调用方传 `cdp_bridge.eval_js`)。`cdp_bridge.eval_js` 返回 CDP `Runtime.evaluate` 原始 dict:字符串原始值时形如 `{"result": {"result": {"type": "string", "value": "<the string>"}}}`。
- Produces:
  - `INVERT_ATTACK_ADDR: int | None` —— 模块常量,初值 `None`
  - `_js(addr: int) -> str` —— 返回 `JSON.stringify((() => {...})())` 形式的 JS 串,里面 `const A = <addr 十进制>;`
  - `ensure_invert_attack_on(eval_js, addr=None) -> tuple[str, str]` —— `(status, detail)`,`status ∈ {"turned_on", "on_already", "failed"}`。`addr` 覆盖模块常量(单测用);两者都 `None` → `("failed", "addr-not-calibrated")` 且**不调用** `eval_js`。

- [ ] **Step 1: 写失败测试**

`test_florr_settings.py`:

```python
import json

import pytest

import florr_settings as fs


def _resp(payload):
    """把一个 dict 包成 cdp_bridge.eval_js 那种返回结构(payload 会被 JSON.stringify)."""
    return {"result": {"result": {"type": "string", "value": json.dumps(payload)}}}


def test_addr_not_calibrated_does_not_call_eval(monkeypatch):
    monkeypatch.setattr(fs, "INVERT_ATTACK_ADDR", None)
    calls = []
    status, detail = fs.ensure_invert_attack_on(lambda e: calls.append(e))
    assert (status, detail) == ("failed", "addr-not-calibrated")
    assert calls == []


def test_turned_on_when_before_zero():
    resp = _resp({"ok": True, "before": 0, "after": 1})
    assert fs.ensure_invert_attack_on(lambda e: resp, addr=0xAD1234) == ("turned_on", "")


def test_on_already_when_before_one():
    resp = _resp({"ok": True, "before": 1, "after": 1})
    assert fs.ensure_invert_attack_on(lambda e: resp, addr=0xAD1234) == ("on_already", "")


@pytest.mark.parametrize("reason", ["no-wasm-memory", "addr-out-of-range", "not-bool:7"])
def test_failed_passes_through_js_reason(reason):
    resp = _resp({"ok": False, "reason": reason})
    assert fs.ensure_invert_attack_on(lambda e: resp, addr=1) == ("failed", reason)


def test_failed_on_eval_exception():
    def boom(_e):
        raise RuntimeError("no florr tab")
    assert fs.ensure_invert_attack_on(boom, addr=1) == ("failed", "cdp-error:no florr tab")


def test_failed_when_no_value_in_response():
    # 表达式在页面里抛异常时 Runtime.evaluate 不带 result.result.value
    resp = {"result": {"result": {"type": "object", "className": "Object"}}}
    assert fs.ensure_invert_attack_on(lambda e: resp, addr=1) == ("failed", "no-value")


def test_failed_on_bad_json():
    resp = {"result": {"result": {"type": "string", "value": "not json{"}}}
    assert fs.ensure_invert_attack_on(lambda e: resp, addr=1) == ("failed", "bad-json")


def test_failed_on_none_response():
    assert fs.ensure_invert_attack_on(lambda e: None, addr=1) == ("failed", "no-value")


def test_js_has_decimal_addr_and_is_stringify_iife():
    js = fs._js(0x1234)
    assert "const A = 4660;" in js
    assert js.startswith("JSON.stringify((() => {")
    assert js.rstrip().endswith("})())")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest test_florr_settings.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'florr_settings'`

- [ ] **Step 3: 最小实现**

`florr_settings.py`:

```python
"""worker 启动时确保 florr.io 的「反转攻击键」(设置→控制→Invert attack button)
为开. bot 自己不按攻击键, 靠这个设置让 florr 持续攻击 —— 关着的话 bot 到位、
绕圈、寻路全正常, 但一点伤害都不出.

机制: florr 是 Emscripten/WASM, 这个设置在运行时是静态数据段里一个 bool 字节,
游戏每帧读它. 同一个 florr build 里地址稳定; florr 发新 build 才漂移. 地址靠
仓库根的 settings_finder.js 找出来填到下面 INVERT_ATTACK_ADDR.

不 import 任何 GUI 库、不 import main. 只通过参数拿 eval_js (= cdp_bridge.eval_js).
"""
import json

# settings_finder.js 找出来的地址. florr 大版本更新后可能失效 —— 重跑
# settings_finder.js 求新值填这里. None = 没标定过, 功能静默降级成"只警告".
INVERT_ATTACK_ADDR = None

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
  if (A < 0 || A >= u8.length) return {{ok: false, reason: "addr-out-of-range"}};
  const before = u8[A];
  if (before > 1) return {{ok: false, reason: "not-bool:" + before}};
  if (before === 0) u8[A] = 1;
  return {{ok: true, before: before, after: u8[A]}};
}})())"""


def _js(addr):
    return _JS_TEMPLATE.format(addr=addr)


def ensure_invert_attack_on(eval_js, addr=None):
    """florr 的「反转攻击键」关着就打开. 返回 (status, detail):
      "turned_on"  —— 本来关的, 已写成开
      "on_already" —— 本来就是开
      "failed"     —— 没能确认(detail = 原因), 调用方该警告但别中断 worker
    """
    a = addr if addr is not None else INVERT_ATTACK_ADDR
    if a is None:
        return ("failed", "addr-not-calibrated")
    try:
        resp = eval_js(_js(a))
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
    return ("on_already" if data.get("before") == 1 else "turned_on", "")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/python -m pytest test_florr_settings.py -q`
Expected: PASS(全部)

- [ ] **Step 5: 全量回归**

Run: `./venv/bin/python -m pytest -q`
Expected: 之前的绿 + 新增全绿

- [ ] **Step 6: Commit**

```bash
git add florr_settings.py test_florr_settings.py
git commit -m "feat(florr_settings): ensure_invert_attack_on — CDP WASM byte read/write, 3-state result"
```

---

## Task 2: `main.run_worker` 接入

**Files:**
- Modify: `main.py`(顶部 import 区 + `run_worker` 函数体,`create_overlay()` 之后)
- Test: `test_main_worker.py`

**Interfaces:**
- Consumes(Task 1): `florr_settings.ensure_invert_attack_on(eval_js) -> (status, detail)`,`cdp_bridge.eval_js`
- Produces: `run_worker` 启动阶段调用一次 `florr_settings.ensure_invert_attack_on(cdp_bridge.eval_js)`;`turned_on`/`on_already` 只 `print`,`failed` 额外 `overlay.update(message=...)`;任何分支都不 `sys.exit`/不 raise。

- [ ] **Step 1: 写失败测试**

`test_main_worker.py` 追加(沿用现有 `_StubOverlay` + 「主循环第一个调用抛 KeyboardInterrupt 掐断」的模式):

```python
def test_run_worker_calls_ensure_invert_attack_once(monkeypatch):
    monkeypatch.setattr(main.cdp_bridge, "is_dedicated_chrome_ready", lambda: True)
    monkeypatch.setattr(main, "create_overlay", lambda *a, **k: _StubOverlay())
    monkeypatch.setattr(main, "overlay", None, raising=False)
    monkeypatch.setattr(main, "_apply_worker_config", lambda cfg: {
        "location": (1, 2), "farming_area": [(0, 0), (9, 9)], "farming_duration": 300,
        "short_round_limit": 2, "enemy_ai_enabled": False, "auto_switch_server": False,
    })
    calls = []
    monkeypatch.setattr(main.florr_settings, "ensure_invert_attack_on",
                        lambda ej, *a, **k: calls.append(ej) or ("turned_on", ""))
    monkeypatch.setattr(main, "on_death_screen",
                        lambda: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    assert len(calls) == 1
    assert calls[0] is main.cdp_bridge.eval_js


def test_run_worker_survives_invert_attack_failure(monkeypatch):
    """ensure_invert_attack_on 返回 failed 时 worker 照常进主循环, 不 SystemExit."""
    monkeypatch.setattr(main.cdp_bridge, "is_dedicated_chrome_ready", lambda: True)
    ov = _StubOverlay()
    warned = []
    ov.update = lambda **kw: warned.append(kw.get("message"))
    monkeypatch.setattr(main, "create_overlay", lambda *a, **k: ov)
    monkeypatch.setattr(main, "overlay", None, raising=False)
    monkeypatch.setattr(main, "_apply_worker_config", lambda cfg: {
        "location": (1, 2), "farming_area": [(0, 0), (9, 9)], "farming_duration": 300,
        "short_round_limit": 2, "enemy_ai_enabled": False, "auto_switch_server": False,
    })
    monkeypatch.setattr(main.florr_settings, "ensure_invert_attack_on",
                        lambda ej, *a, **k: ("failed", "addr-out-of-range"))
    monkeypatch.setattr(main, "on_death_screen",
                        lambda: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):   # 到了主循环 = 没被 failed 掐死
        main.run_worker({})
    assert any(m and "反转攻击键" in m for m in warned)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest test_main_worker.py::test_run_worker_calls_ensure_invert_attack_once test_main_worker.py::test_run_worker_survives_invert_attack_failure -q`
Expected: FAIL —— `AttributeError: module 'main' has no attribute 'florr_settings'`

- [ ] **Step 3: 最小实现**

`main.py` 顶部 import 区(`import app_config` 那行附近)加:

```python
import florr_settings
```

`run_worker` 里,`overlay = create_overlay()` 那行之后、`w = _apply_worker_config(cfg)` 之前,插入:

```python
    # bot 自己不按攻击键, 靠 florr 的「反转攻击键」设置持续输出. 关着的话到位也
    # 不出伤害. 启动时通过 CDP 往 florr 的 WASM 内存写一个字节确保它开着 ——
    # 做不到就大声警告, 但不中断(bot 照常跑, 用户看到警告去手动勾).
    _ia_status, _ia_detail = florr_settings.ensure_invert_attack_on(cdp_bridge.eval_js)
    if _ia_status == "turned_on":
        print("✅ 已开启 florr「反转攻击键」")
    elif _ia_status == "on_already":
        print("florr「反转攻击键」已是开")
    else:
        print(f"⚠️ 没能确认 florr「反转攻击键」({_ia_detail}) —— 请手动到 "
              f"设置→控制→反转攻击键 打勾, 否则 bot 到位也不出伤害")
        overlay.update(message="⚠️ 反转攻击键未确认, 见日志")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/python -m pytest test_main_worker.py -q`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `./venv/bin/python -m pytest -q`
Expected: 全绿

- [ ] **Step 6: Commit**

```bash
git add main.py test_main_worker.py
git commit -m "feat(main): run_worker ensures florr Invert Attack is on at startup (warn-only on failure)"
```

---

## Task 3: `settings_finder.js` + README 一节

**Files:**
- Create: `settings_finder.js`
- Modify: `README.md`

**Interfaces:** 无代码;不被任何模块 import;`main.spec` 不动(不打包)。

- [ ] **Step 1: 建 `settings_finder.js`**

写入仓库根 `settings_finder.js` —— 顶部加下面这段注释块,后面接用户提供的脚本原文:

```javascript
// ─────────────────────────────────────────────────────────────────────────────
// DEV TOOL — not imported by any code, not bundled by PyInstaller.
//
// Pins the WASM memory byte for florr's "Invert attack button" checkbox
// (Settings → Controls). Paste this whole file into the florr.io devtools
// console, then follow USAGE below. Put the address set.solve() returns into
// florr_settings.INVERT_ATTACK_ADDR.
//
// Re-run this whenever the worker log says the Invert-Attack check FAILED with
// "addr-out-of-range" or "not-bool:<n>" — that means florr shipped a new build
// and the old address no longer points at the flag.
// ─────────────────────────────────────────────────────────────────────────────

// florr.io settings-flag finder — paste in devtools console.
// Pins the memory byte for a boolean setting (e.g. "Invert attack button")
// by correlating multiple manual toggles. Background bit-churn (~300 bytes/frame)
// is rejected because only the real flag alternates in lockstep every time.
//
// USAGE
//   set.begin()                       // call with the checkbox in its CURRENT state
//   ... tick the checkbox in Settings, then:   set.mark()
//   ... untick it, then:                       set.mark()
//   ... repeat 4-6 marks, alternating on/off each time
//   set.solve()                       // -> the address(es) that flipped every time
//
// Then: set.read(addr) / set.write(addr, 0|1) to inspect or force it.

(() => {
  const M = window.Module, buf = M.asm.Mf.buffer;
  const LO = 0x400000, HI = 0x1800000;              // 4MB..24MB (covers florr's settings block ~0xAD_xxxx)
  const snap = () => new Uint8Array(buf, LO, HI - LO).slice();
  let caps = [];

  const begin = () => { caps = [snap()]; console.log('begin: 1 capture'); };
  const mark  = () => { caps.push(snap()); console.log(`mark: ${caps.length} captures`); };

  function solve() {
    if (caps.length < 4) return console.warn('need >=4 captures (begin + 3 marks)');
    const K = caps.length, n = caps[0].length, out = [];
    for (let i = 0; i < n; i++) {
      let ok = true;
      const v0 = caps[0][i];
      if (v0 > 1) continue;
      for (let k = 1; k < K; k++) {
        const v = caps[k][i];
        if (v > 1) { ok = false; break; }
        if (v === caps[k - 1][i]) { ok = false; break; }   // must change every mark
        if (v !== (k % 2 === 0 ? v0 : 1 - v0)) { ok = false; break; } // strict alternation
      }
      if (ok) out.push('0x' + (LO + i).toString(16));
    }
    console.log(`${out.length} address(es) alternate perfectly across all ${K} captures:`, out);
    return out;
  }

  const read  = (a) => new Uint8Array(buf)[typeof a === 'string' ? parseInt(a, 16) : a];
  const write = (a, v) => { new Uint8Array(buf)[typeof a === 'string' ? parseInt(a, 16) : a] = v ? 1 : 0; };

  window.set = { begin, mark, solve, read, write };
  console.log('set.* ready. set.begin() -> toggle+set.mark() x4-6 -> set.solve()');
})();
```

- [ ] **Step 2: README 一节**

`README.md` 的「Enemy Detection (Sandstorm Zone)」小节之后,插入:

```markdown
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
```

- [ ] **Step 3: 检查**

Run: `./venv/bin/python -c "import ast; ast.parse(open('florr_settings.py').read()); print('py ok')" && node -c settings_finder.js 2>/dev/null && echo "js ok" || echo "js check skipped (no node)"`
Expected: `py ok`;js 检查有 node 就 `js ok`,没有就跳过(不阻塞)。

Run: `./venv/bin/python -m pytest -q`
Expected: 全绿(本任务没动 Python 逻辑)

- [ ] **Step 4: Commit**

```bash
git add settings_finder.js README.md
git commit -m "docs: ship settings_finder.js dev tool + README section for Invert Attack calibration"
```

---

## Self-Review

**1. Spec coverage:**

| Spec 要求 | 对应 Task |
|---|---|
| `florr_settings.py` 新模块,不 import GUI/main | Task 1 |
| `INVERT_ATTACK_ADDR = None` 初值 + 降级 | Task 1(`test_addr_not_calibrated_does_not_call_eval`) |
| `_js(addr)` 十进制展开 + `JSON.stringify` IIFE 结构 | Task 1(`test_js_has_decimal_addr_and_is_stringify_iife`) |
| `ensure_invert_attack_on` 三状态 `turned_on`/`on_already`/`failed` | Task 1 |
| `eval_js` 不带 returnByValue,靠字符串原始值 `result.result.value` | Task 1(`_js` 用 `JSON.stringify`,解析 `inner["value"]`) |
| 各失败路径:no-wasm-memory / addr-out-of-range / not-bool / cdp 异常 / no-value / bad-json | Task 1(参数化 + 独立 test) |
| `run_worker` 接入点(Chrome 就绪 + `create_overlay()` 之后、主循环之前) | Task 2 |
| `turned_on`/`on_already` 只 print;`failed` 加 `overlay.update` | Task 2 |
| 失败不 `sys.exit`/不 raise | Task 2(`test_run_worker_survives_invert_attack_failure`) |
| 只调一次 | Task 2(`test_run_worker_calls_ensure_invert_attack_once`) |
| `settings_finder.js` 进 repo 根 + 头部注释块 | Task 3 |
| `main.spec` 不打包 settings_finder.js | Task 3(明确「不动 main.spec」) |
| README 新增一节 | Task 3 |
| 现有 test 全绿 | 每个 Task 的 Step 5/3 |

**2. Placeholder scan:** `INVERT_ATTACK_ADDR = None` 是 spec 明确的有意初值(带降级路径 + 测试 + README 说明),非 TODO。无其它 "TBD"/"稍后填"。所有 code step 都有完整可粘贴代码。

**3. Type consistency:**
- `ensure_invert_attack_on(eval_js, addr=None) -> (str, str)` —— Task 1 定义,Task 2 按 `(_ia_status, _ia_detail)` 解包,`status` 值域 `turned_on|on_already|failed` 一致。
- `_js(addr)` 返回 str —— Task 1 内部用,测试断言 `const A = 4660;`(`{addr}` 被 `.format` 成十进制)。
- Task 2 monkeypatch `main.florr_settings.ensure_invert_attack_on`,签名 `lambda ej, *a, **k` 兼容 `(eval_js, addr=None)`。
- `main` 顶部 `import florr_settings` → `main.florr_settings` 属性存在(Task 2 test 依赖)。
- `overlay.update(message=...)` —— `overlay.py` 三个实现都有 `def update(self, state=None, pos=None, target=None, message=None)`,一致。
