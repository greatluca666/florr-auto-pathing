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
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

import websocket

CDP_HOST = "127.0.0.1"
CDP_PORT = 9222

_WINDOWS_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


def _find_windows_chrome():
    """按几个常见安装路径挨个试, 都没有就返回None(调用方负责报错文案)."""
    for path in _WINDOWS_CHROME_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def _quit_all_chrome():
    """强制退出所有Chrome进程 —— 专用CDP实例要求Chrome完全重启后新参数才生效
    (见模块文档). 本来就没在跑不算失败, 静默吞掉非零退出码."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/IM", "chrome.exe", "/F"], capture_output=True)
    elif sys.platform == "darwin":
        subprocess.run(["osascript", "-e", 'quit app "Google Chrome"'], capture_output=True)
    time.sleep(1)  # 给进程真正退出、释放profile锁一点时间, 避免新实例抢锁失败


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


def _send_cdp_command(method, params=None, timeout=5):
    """开一条WebSocket连接, 发一条CDP命令, 等到id对上号的响应, 关连接, 返回
    结果. eval_js()/capture_screenshot()共用这一套收发逻辑, 不重复写.

    找不到标签页时抛RuntimeError, 带清楚的中文原因, 不静默失败 —— 调用方
    (switch_server()等)必须知道到底有没有真的执行成功."""
    tab = find_florr_tab()
    if tab is None:
        raise RuntimeError(
            "找不到florr.io标签页(CDP). 确认: 1) Chrome是不是用本模块文档里"
            "那三个参数(--remote-debugging-port=9222 + --remote-allow-origins=* "
            "+ --user-data-dir=...)一起启动的(正常双击图标打开的不算, 少一个"
            "参数也不行); 2) florr.io标签页是不是在这个新窗口里打开的."
        )
    ws_url = tab["webSocketDebuggerUrl"]
    if not hasattr(websocket, "create_connection"):
        # PyPI上"websocket"和"websocket-client"是两个不相关的包, 都导入成
        # `websocket`这个模块名, 装错/装重会互相覆盖 —— 实机在Windows上复现过
        # (报错是"module 'websocket' has no attribute 'create_connection'",
        # 不容易联想到"装错包了", 这里把原因和修法直接说清楚). 放在实际调用这里
        # 检查, 不放在模块导入时 —— 导入时就抛的话main.py会直接启动不了, 换服务
        # 器这个附加功能坏了不该连累寻路/刷怪这些正常功能, 让这条错误照样走
        # switch_server()调用方那边已有的try/except, 不阻塞主循环.
        raise RuntimeError(
            "装的是错的'websocket'包 —— PyPI上有两个不同的包都叫这个模块名, "
            "这个项目要的是'websocket-client', 不是单独的'websocket'. 修法:\n"
            "    pip uninstall -y websocket websocket-client\n"
            "    pip install websocket-client"
        )
    ws = websocket.create_connection(ws_url, timeout=timeout)
    try:
        request_id = 1
        ws.send(json.dumps({
            "id": request_id,
            "method": method,
            "params": params or {},
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


def eval_js(expression, timeout=5):
    """在florr.io标签页里执行一段JS表达式, 返回CDP Runtime.evaluate的原始
    返回字典."""
    return _send_cdp_command(
        "Runtime.evaluate", {"expression": expression}, timeout=timeout)


def capture_screenshot(timeout=5):
    """截florr.io标签页当前内容, 返回PNG原始字节. 走CDP, 不是pyautogui —— 不
    依赖那个标签页是不是在前台/有没有窗口焦点(main.py跑着的时候用户很可能在看
    别的窗口, pyautogui.screenshot()这时候截到的是别的东西, 不是游戏画面)."""
    result = _send_cdp_command(
        "Page.captureScreenshot", {"format": "png"}, timeout=timeout)
    return base64.b64decode(result["result"]["data"])
