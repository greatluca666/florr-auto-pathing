"""worker 启动时确保 florr.io 的「反转攻击键」(设置→控制→Invert attack button)
为开. bot 自己不按攻击键, 靠这个设置让 florr 持续攻击 —— 关着的话 bot 到位、
绕圈、寻路全正常, 但一点伤害都不出.

机制: florr 是 Emscripten/WASM, 这个设置在运行时是静态数据段里一个 bool 字节,
游戏每帧读它. 同一个 florr build 里地址稳定; florr 发新 build 才漂移. 地址靠
仓库根的 settings_finder.js 找出来填到下面 INVERT_ATTACK_ADDR.

不 import 任何 GUI 库、不 import main. 只通过参数拿 eval_js (= cdp_bridge.eval_js).
"""
import json

# settings_finder.js 找出来的地址(2026-09-02, 中文客户端上标定). florr 大版本
# 更新后可能失效 —— worker 日志报 addr-out-of-range / not-bool 时重跑
# settings_finder.js 求新值填这里. 设回 None = 功能静默降级成"只警告".
INVERT_ATTACK_ADDR = 0x53430E

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
    eval_js: cdp_bridge.eval_js 那种签名的函数(expression -> CDP Runtime.evaluate
             原始返回 dict). addr: 覆盖 INVERT_ATTACK_ADDR, 单测用.
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
