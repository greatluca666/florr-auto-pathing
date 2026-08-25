"""通过Chrome DevTools Protocol(CDP)直接在florr.io标签页里跑JS —— 不用装
Tampermonkey之类的浏览器扩展, 也不用靠pyautogui点游戏画布上的像素(那条路在
换服务器这个功能上反复卡住, 见switch_server()的历史).

前提: Chrome得用三个参数一起启动(实机踩过坑确认的, 少一个都不行, 默认全部
不开是出于安全考虑). 正常双击图标打开的Chrome不满足这些条件, 得完全退出
Chrome后从命令行重新起:

    macOS:
        osascript -e 'quit app "Google Chrome"'   # 先完全退出, 不然新参数不生效
                                                     # (复用已有进程, 忽略--args)
        open -a "Google Chrome" --args --remote-debugging-port=9222 \
            "--remote-allow-origins=*" --user-data-dir="/tmp/chrome-debug-profile"
    Windows:
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" ^
            --remote-debugging-port=9222 --remote-allow-origins=* ^
            --user-data-dir="C:\\chrome-debug-profile"

三个参数各自的坑:
  --remote-debugging-port=9222   光加这个不够, Chrome不会真的监听这个端口 ——
                                   必须配合下面的--user-data-dir一起给(单独给会
                                   静默失败, 端口测起来是Connection refused,
                                   没有任何报错提示为什么).
  --user-data-dir=...            指向一个独立的用户目录(不是默认Profile) ——
                                   这意味着开出来的是全新的Chrome窗口, 没有你
                                   原来的标签页/登录状态, 得在这个新窗口里重新
                                   打开florr.io.
  --remote-allow-origins=*       没有这个, CDP的WebSocket握手会被403拒绝
                                   (Chrome自己的跨源保护, 报错信息里会直接把
                                   这个参数名写出来).
Shell里`*`记得加引号, 不然会被当成通配符展开(zsh下`--remote-allow-origins=*`
不加引号会报"no matches found").

这个模块只负责"找到florr.io那个标签页 + 往里面扔一段JS执行", 不掺杂具体要跑
哪段JS(那是调用方, 比如switch_server(), 该关心的事).
"""
import json
import urllib.request
import urllib.error

import websocket

CDP_HOST = "127.0.0.1"
CDP_PORT = 9222


def find_florr_tab():
    """在CDP暴露的标签页列表里找florr.io那个, 返回它的调试信息字典(含
    webSocketDebuggerUrl). 找不到(Chrome没开调试端口/florr.io标签页没开着)
    返回None, 不抛异常 —— 让调用方自己决定报错文案, 别在这里印一堆没上下文
    的traceback."""
    try:
        resp = urllib.request.urlopen(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=3)
    except (urllib.error.URLError, ConnectionRefusedError, OSError):
        return None
    tabs = json.loads(resp.read())
    for tab in tabs:
        if "florr.io" in tab.get("url", ""):
            return tab
    return None


def eval_js(expression, timeout=5):
    """在florr.io标签页里执行一段JS表达式, 返回CDP Runtime.evaluate的原始返回
    字典. 找不到标签页/连不上时抛RuntimeError, 带清楚的中文原因, 不静默失败——
    换服务器这种操作, 调用方(switch_server())必须知道到底有没有真的执行."""
    tab = find_florr_tab()
    if tab is None:
        raise RuntimeError(
            "找不到florr.io标签页(CDP). 确认: 1) Chrome是不是用本模块文档里"
            "那三个参数(--remote-debugging-port=9222 + --remote-allow-origins=* "
            "+ --user-data-dir=...)一起启动的(正常双击图标打开的不算, 少一个"
            "参数也不行); 2) florr.io标签页是不是在这个新窗口里打开的."
        )
    ws_url = tab["webSocketDebuggerUrl"]
    ws = websocket.create_connection(ws_url, timeout=timeout)
    try:
        request_id = 1
        ws.send(json.dumps({
            "id": request_id,
            "method": "Runtime.evaluate",
            "params": {"expression": expression},
        }))
        # CDP这条连接上可能同时有别的事件消息(跟我们这次调用无关的通知)混进来,
        # 不能假设recv()第一条就是我们要的响应 —— 按id对上号才是我们的.
        while True:
            result = json.loads(ws.recv())
            if result.get("id") == request_id:
                break
    finally:
        ws.close()
    if "error" in result:
        raise RuntimeError(f"CDP执行出错: {result['error']}")
    return result
