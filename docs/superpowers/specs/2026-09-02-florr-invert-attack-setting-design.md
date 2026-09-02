# worker 启动时确保 florr「反转攻击键」为开 — design

## Problem

刷怪 worker([main.py](../../../main.py) 的 `run_worker`)自己**不按攻击键**,靠 florr.io 游戏内的持续攻击出伤害。florr 默认:按住左键 = 攻击(花瓣张开),不按 = 不攻击。设置 → 控制(Controls)标签页里有个复选框 **「反转攻击键」/ "Invert attack button"**(`UI/Settings/InvertAttack`):打开后攻击变成**默认状态**,不按键就一直攻击 —— 这正是 bot 需要的。

现在靠用户每个账号手动去勾。忘了勾 → bot 到位、绕圈、寻路全正常,但一点伤害都不出,一轮下来啥也没刷到,还得连续短局才触发换服。

阶段2 的调度器会按时块自动切换 florr 账号(每账号一个 Chrome profile),更容易碰到「这个号在这台机器上还没勾过」。

## Goal

`run_worker()` 启动时(专用 Chrome 就绪检查之后、主循环之前)检查 florr 的这个 flag,关着就打开,全程走 CDP,不碰 canvas 点击。做不到就在日志 + 悬浮窗大声警告,**不中断 worker**(bot 照常跑,只是不出伤害,用户看到警告去手动勾)。

## Non-goals

- **不改 localStorage `cached_account_data`。** 那份 blob 是账号级、服务端同步的缓存;patch 本地值大概率被开局的服务端拉取覆盖,且格式是自定义二进制序列化,偏移不定。运行时内存写(见下)绕开这两个问题。
- **不点 canvas。** [[switch-server-cdp-not-clicks]]:pyautogui 合成点击在 florr canvas 控件上不注册,烧过很多调试时间。
- **不自动重找地址。** florr 大版本重构建 → WASM 里静态数据段偏移变 → 硬编码地址失效。失效时就是一条警告 + 让用户重跑 `settings_finder.js`,不做启发式扫描。
- **不做「关掉」路径。** 只保证「开」。用户想关自己去设置里点。
- **不覆盖「反转防御键」。** 同一页紧跟着的另一个复选框,bot 用不上,不动。
- **worker 每轮不重复检查。** 只在 `run_worker` 启动时一次。设置是账号级同步、进程内不会自己变;真被外部改了,下次 worker 重启(调度器换时块 / 崩溃自愈)会再查。

## 机制

florr.io 是 Emscripten/WASM。这个设置在运行时是 florr 静态数据段里的**一个 bool 字节**,游戏每帧读它决定攻击键语义。同一个 florr build 里,这个字节的线性内存地址在多次页面加载间**稳定**(静态 `.data`,不是堆分配);florr 发新 build 才会漂移。

地址靠 [settings_finder.js](../../../settings_finder.js)(用户提供)找:开发者控制台里 `set.begin()` → 在设置里反复勾/取消 4~6 次、每次 `set.mark()` → `set.solve()`。多次 toggle 的严格交替求交集,滤掉 florr 每帧约 300 字节的背景 bit 抖动,一般收敛到 1 个地址。

写:`new Uint8Array(<florr 的 wasm memory buffer>)[ADDR] = 1`。

**已接受的风险(spec 内不解决,用户首次接入时验证):**
1. florr 重构建 → 地址漂移 → JS 读到的字节 `> 1` 或越界 → 归为 `failed` + 警告,用户重跑 finder。
2. 裸写字节 florr 可能不认(若它把设置值缓存进别的结构、只在设置菜单关闭时读一次)—— 用户接入时勾掉设置、启动 worker、看游戏里花瓣是不是张开,确认一次。
3. 服务端同步:用户已确认只在「账号在别处被改」时才回滚,worker 运行期间不会自己变,不管。

## `florr_settings.py`(新)

不 import 任何 GUI 库,也不 import `main`(避免环)。只依赖 `cdp_bridge.eval_js`(通过参数注入,方便单测)。

```python
# settings_finder.js 找出来的地址. florr 大版本更新后可能失效 —— 重跑
# settings_finder.js 求新值填这里. None = 没标定过, 功能静默降级成"只警告".
INVERT_ATTACK_ADDR = None

_JS_TEMPLATE = r"""JSON.stringify((() => {{
  const M = window.Module;
  const mem = M && (
    (M.asm && M.asm.memory && M.asm.memory.buffer) ||
    (M.wasmMemory && M.wasmMemory.buffer) ||
    (M.HEAPU8 && M.HEAPU8.buffer) ||
    (M.asm && M.asm.Mf && M.asm.Mf.buffer)   // Mf: 观察到的导出名, 会随 build 变
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
    """florr 的「反转攻击键」关着就打开. 走 CDP 在 florr.io 标签页里改一个 WASM
    内存字节. 返回 (status, detail):
      "turned_on"   —— 本来是关的, 已写成开
      "on_already"  —— 本来就是开
      "failed"      —— 没能确认(detail 是原因), 调用方该警告但别中断
    eval_js: cdp_bridge.eval_js 那种签名的函数(expression -> CDP Runtime.evaluate
             原始返回 dict). addr: 覆盖 INVERT_ATTACK_ADDR, 单测用.
    """
    a = addr if addr is not None else INVERT_ATTACK_ADDR
    if a is None:
        return ("failed", "addr-not-calibrated")
    try:
        resp = eval_js(_js(a))
    except Exception as e:                       # RuntimeError: 找不到 florr 标签页 等
        return ("failed", f"cdp-error:{e}")
    inner = (resp or {}).get("result", {}).get("result", {})
    if "value" not in inner:                     # 表达式抛异常 / 返回非字符串
        return ("failed", "no-value")
    try:
        data = json.loads(inner["value"])
    except (ValueError, TypeError):
        return ("failed", "bad-json")
    if not data.get("ok"):
        return ("failed", data.get("reason", "unknown"))
    return ("on_already" if data.get("before") == 1 else "turned_on", "")
```

`eval_js` 不带 `returnByValue`,但 `JSON.stringify(...)` 求值成**字符串原始值**,`Runtime.evaluate` 对原始值直接回 `{"result":{"result":{"type":"string","value":"..."}}}` —— 拿 `value` 再 `json.loads` 就行(对象才需要 `returnByValue`)。

## `main.run_worker` 接入

`is_dedicated_chrome_ready()` 检查之后、`create_overlay()` 之后(要用 overlay 警告)、主循环之前:

```python
_ia_status, _ia_detail = florr_settings.ensure_invert_attack_on(cdp_bridge.eval_js)
if _ia_status == "turned_on":
    print("✅ 已开启 florr「反转攻击键」")
elif _ia_status == "on_already":
    print("florr「反转攻击键」已是开")
else:
    msg = (f"没能确认 florr「反转攻击键」({_ia_detail}) —— "
           f"请手动到 设置→控制→反转攻击键 打勾, 否则 bot 到位也不出伤害")
    print(f"⚠️ {msg}")
    overlay.update(message="⚠️ 反转攻击键未确认, 见日志")
```

`import florr_settings` 加到 main.py 顶部现有 import 区。失败分支绝不 `sys.exit` —— worker 照常进主循环。

## `settings_finder.js` 进仓库

用户提供的脚本原样存到 repo 根 `settings_finder.js`,顶部加一段注释块:这是**开发工具**,不被任何代码 import;florr 更新导致 `florr_settings.INVERT_ATTACK_ADDR` 失效(worker 日志报 `addr-out-of-range` / `not-bool`)时,在 florr.io 开发者控制台粘这个,按 USAGE 跑一遍,把 `set.solve()` 的地址填进 `florr_settings.py`。

`main.spec` **不打包** `settings_finder.js`(纯 dev 工具,发布包不需要)。

## README

「Enemy Detection」之后加一小节「florr『反转攻击键』」:说明 worker 依赖这个设置、启动时会尝试自动开、`florr_settings.INVERT_ATTACK_ADDR` 未标定或 florr 更新后会退化成警告、怎么用 `settings_finder.js` 重找。

## 测试(`test_florr_settings.py` 新)

`ensure_invert_attack_on` 全程 monkeypatch `eval_js`,不碰真 CDP:

- `addr=None`(且模块常量也 None)→ `("failed", "addr-not-calibrated")`,`eval_js` 不被调用
- `eval_js` 返回 `{"result":{"result":{"value": '{"ok":true,"before":0,"after":1}'}}}` → `("turned_on", "")`
- `before:1` → `("on_already", "")`
- `{"ok":false,"reason":"no-wasm-memory"}` → `("failed", "no-wasm-memory")`
- `{"ok":false,"reason":"addr-out-of-range"}` → `("failed", "addr-out-of-range")`
- `{"ok":false,"reason":"not-bool:7"}` → `("failed", "not-bool:7")`
- `eval_js` 抛 `RuntimeError("no florr tab")` → `("failed", "cdp-error:no florr tab")`
- 返回里没有 `value`(表达式异常)→ `("failed", "no-value")`
- `value` 不是合法 JSON → `("failed", "bad-json")`
- `_js(0x1234)` 生成的串里含 `const A = 4660;`(`.format` 十进制展开)且是合法 `JSON.stringify((() => {...})())` 结构

`test_main_worker.py` **扩**:

- `run_worker` 启动阶段调用 `florr_settings.ensure_invert_attack_on` 恰好一次(monkeypatch 它返回 `("failed", "x")`,断言 worker 没有因此 `SystemExit` —— 用现有那套「主循环第一个调用抛 KeyboardInterrupt 掐断」的模式)
- monkeypatch 返回 `("turned_on", "")` 时不打警告路径(可选:capsys 断言日志文案)

现有 test 全绿。

## Self-review

- 占位符:`INVERT_ATTACK_ADDR = None` 是**有意**的初始值(用户还没跑 finder),不是 TODO —— 代码对 `None` 有明确降级路径 + 测试覆盖。README / 注释都说明了怎么填。
- 一致性:`ensure_invert_attack_on` 三个返回状态 `turned_on|on_already|failed` 在 `run_worker` 接入处一一对应处理。
- 歧义:「启动时检查一次」明确 = `run_worker` 函数体开头一次,不是每轮。
- 范围:单文件新增 + 一处接入 + 一个 dev 脚本 + README 一节 + 一个测试文件。单一 plan 够。
