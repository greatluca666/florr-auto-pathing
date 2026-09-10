"""读 florr 当前连的游戏服务器码, 用来判断/确认当前生态区.

florr 的生态区 = 你连的那台游戏服务器. 服务器码属于哪个 `florrio-map-N` 池就是
哪个生态区(server_lookup.BIOME_INDEX). florr 没有 JS API 暴露"当前服务器" ——
抄 greasyfork「florr.io | Server Switcher」(脚本 461100)的办法: 钩住
`window.WebSocket`, 记下最后一个 florr 游戏 WS 的 url(`wss://<码>.s.m28n.net/`),
从子域名取码.

切生态区靠 `cp6.forceServerID(码)`(utils.switch_server 已封装): florr 断线重连到
那台服务器. 实测**不是无缝** —— 会落回开局菜单, 调用方切完还得点「开始」.

不 import GUI / main. eval_js 由参数注入(= cdp_bridge.eval_js 那种签名: 表达式 ->
CDP Runtime.evaluate 原始返回 dict), 方便单测.
"""
import re

# greasyfork 脚本用的同一个模式. florr 游戏 WS: wss://<码>.s.m28n.net/
_WS_RE = re.compile(r"wss://([a-z0-9]+)\.s\.m28n\.net")

# 幂等装 WS url 钩子 + 返回当前记到的 url. 装钩子这刻之前 florr 已建的 WS 抓不到
# (返回 ""), 但之后每次 forceServerID 重连建的新 WS 都会更新 __florrWsUrl.
_HOOK_JS = r"""(() => {
  if (!window.__florrWsHook) {
    window.__florrWsHook = 1;
    window.__florrWsUrl = "";
    var NW = window.WebSocket;
    var W = function () {
      var s = Reflect.construct(NW, arguments);
      try {
        if (/s\.m28n\.net/.test(String(arguments[0]))) window.__florrWsUrl = String(arguments[0]);
      } catch (e) {}
      return s;
    };
    W.prototype = NW.prototype;
    try {
      Object.getOwnPropertyNames(NW).forEach(function (k) { try { W[k] = NW[k]; } catch (e) {} });
    } catch (e) {}
    window.WebSocket = W;
  }
  return window.__florrWsUrl || "";
})()"""


def _eval_str(eval_js, expr):
    """eval_js(expr) -> 里面那个字符串. Runtime.evaluate 不带 returnByValue 时原始
    类型直接在 result.result.value. 拿不到 -> ''."""
    try:
        resp = eval_js(expr)
    except Exception:
        return ""
    v = (resp or {}).get("result", {}).get("result", {}).get("value")
    return v if isinstance(v, str) else ""


def current_server_code(eval_js):
    """florr 当前连的游戏服务器码(WS 子域名). 顺带幂等装 WS url 钩子.
    没连上 / 钩子刚装还没抓到 / 读不到 -> ''."""
    m = _WS_RE.search(_eval_str(eval_js, _HOOK_JS))
    return m.group(1) if m else ""


def on_biome(eval_js, server_ids):
    """当前连的服务器码在不在 server_ids 里(某个生态区的服务器池). 读不到码 -> False."""
    code = current_server_code(eval_js)
    return bool(code) and code in server_ids
