"""悬浮状态窗 — 全屏运行main.py时显示寻路/移动进度.

macOS: 用PyObjC(AppKit)直接建原生窗口, 不用tkinter —— 实测tkinter的-topmost
只能在同一个macOS Space内置顶, florr.io开的是原生全屏(独立Space)时, tkinter
窗口会跳到另一个桌面上, 根本盖不到游戏画面上。AppKit窗口配合
NSWindowCollectionBehaviorCanJoinAllSpaces + FullScreenAuxiliary +
screensaver级别的窗口层级(1000), 才能真正跨Space盖在全屏游戏上面。

Windows: Windows没有macOS那种独立Space, 浏览器F11全屏是普通的无边框窗口
(不是独占全屏), 常规-topmost就能盖上去, 所以用tkinter无边框窗口即可, 不需要
AppKit那套技巧。但仍需win32扩展窗口样式做到点击穿透 + 不抢键盘焦点, 见
_WindowsOverlay。
"""
import sys
import time

_IS_MACOS = sys.platform == "darwin"
_IS_WINDOWS = sys.platform == "win32"

try:
    import AppKit
    import Foundation
except ImportError:
    AppKit = None
    Foundation = None

try:
    import tkinter as tk
except ImportError:
    tk = None

try:
    import ctypes
except ImportError:
    ctypes = None


def _format_elapsed(seconds):
    """把秒数格式化成 mm:ss, 负数按0算."""
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _format_pos(pos):
    """把 (x, y) 或 None 格式化成显示用字符串."""
    if pos is None:
        return "-"
    return f"({pos[0]}, {pos[1]})"


def _merge_state(current, **fields):
    """把非None的字段合并进当前状态, 不修改传入的dict."""
    updated = dict(current)
    for key, value in fields.items():
        if value is not None:
            updated[key] = value
    return updated


class _NullOverlay:
    """悬浮窗建不起来时的空壳替代品, 调用什么都不做, 绝不炸主程序."""

    def update(self, state=None, pos=None, target=None, message=None):
        return None

    def close(self):
        return None


# screensaver窗口层级(CGWindowLevel), 比普通置顶(status级别~25)高得多 ——
# 实测普通status级别 + canJoinAllSpaces 依然会被系统分到另一个Space,
# 只有这个级别才能真正跨越florr.io的全屏Space显示。
_SCREENSAVER_LEVEL = 1000

# 窗口位置需避开 utils.py 的屏幕探测区域:
#   check_stage() 探测像素 (316,32) 和 (156,35)
#   get_map() 截取小地图区域 [1600,20,1900,320] (右上角)
#   abandon_game() 点击 (307,32)
# 左上角、顶部往下200px, 落在这些区域下方, 留出安全边距.
_WIDTH, _HEIGHT = 260, 150
_LEFT, _TOP_OFFSET = 20, 200

# 亮橙底+黑字: 实测过暗色半透明底(#1e1e1e)会跟游戏画面糊成一片肉眼看不见,
# 亮色才能在任意游戏背景上都保证看得见.
_BG_COLOR = (1.0, 0.6, 0.0, 0.92)  # R, G, B, alpha


class StatusOverlay:
    _FIELDS = ("state", "pos", "target", "message")
    _LABELS_ZH = {"state": "状态", "pos": "位置", "target": "目标", "message": "消息"}

    def __init__(self):
        self._start = time.time()
        self._state = {"state": "-", "pos": None, "target": None, "message": "-"}
        # 一旦update/close在窗口没了之后抛异常, 就锁死后续调用为空操作, 绝不再炸主程序.
        self._dead = False

        app = AppKit.NSApplication.sharedApplication()
        # Accessory: 不占Dock图标、不抢应用切换的焦点, 纯后台悬浮窗.
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self._app = app

        screen_frame = AppKit.NSScreen.mainScreen().frame()
        screen_height = screen_frame.size.height
        y_origin = screen_height - _TOP_OFFSET - _HEIGHT
        rect = Foundation.NSMakeRect(_LEFT, y_origin, _WIDTH, _HEIGHT)

        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False,
        )
        window.setLevel_(_SCREENSAVER_LEVEL)
        window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorStationary
        )
        window.setOpaque_(False)
        window.setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(*_BG_COLOR)
        )
        # 悬浮窗绝不能挡鼠标点击/抢键盘焦点 —— main.py靠鼠标位置和空格键控制游戏,
        # 焦点被抢走这些输入就发不到游戏里了. 只用orderFrontRegardless()显示,
        # 不调用makeKeyAndOrderFront_/activateIgnoringOtherApps_.
        window.setIgnoresMouseEvents_(True)
        self._window = window

        content = window.contentView()
        title = self._make_label(0, "florr auto-pathing", bold=True)
        content.addSubview_(title)

        self._field_labels = {}
        for i, field in enumerate(self._FIELDS):
            label = self._make_label(i + 1, f"{self._LABELS_ZH[field]}: -")
            content.addSubview_(label)
            self._field_labels[field] = label

        self._elapsed_label = self._make_label(len(self._FIELDS) + 1, "耗时: 00:00", small=True)
        content.addSubview_(self._elapsed_label)

        window.orderFrontRegardless()
        self._pump_events()

    def _make_label(self, row, text, bold=False, small=False):
        """按行号从上往下建一个不可编辑的文本标签(Cocoa坐标原点在左下角)."""
        row_height = 22
        y = _HEIGHT - 12 - (row + 1) * row_height
        frame = Foundation.NSMakeRect(8, y, _WIDTH - 16, row_height)
        label = AppKit.NSTextField.alloc().initWithFrame_(frame)
        label.setStringValue_(text)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        size = 10 if small else 12
        weight = AppKit.NSFont.boldSystemFontOfSize_ if bold else AppKit.NSFont.systemFontOfSize_
        label.setFont_(weight(size))
        label.setTextColor_(
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.15, 1.0) if not small
            else AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.3, 1.0)
        )
        return label

    def _pump_events(self):
        """非阻塞地把Cocoa事件循环转几圈, 让刚设置的内容真正画到屏幕上.

        没有跑常规的NSApp.run()主循环(跟main.py现有的同步while循环轮询模型
        保持一致, 不引入线程), 所以每次update()都要手动拉一下事件循环。
        """
        while True:
            event = self._app.nextEventMatchingMask_untilDate_inMode_dequeue_(
                AppKit.NSEventMaskAny,
                Foundation.NSDate.dateWithTimeIntervalSinceNow_(0),
                AppKit.NSDefaultRunLoopMode,
                True,
            )
            if event is None:
                break
            self._app.sendEvent_(event)

    def update(self, state=None, pos=None, target=None, message=None):
        if self._dead:
            return None
        try:
            self._state = _merge_state(
                self._state, state=state, pos=pos, target=target, message=message,
            )
            for field in self._FIELDS:
                value = self._state[field]
                display = _format_pos(value) if field in ("pos", "target") else value
                self._field_labels[field].setStringValue_(f"{self._LABELS_ZH[field]}: {display}")
            self._elapsed_label.setStringValue_(
                f"耗时: {_format_elapsed(time.time() - self._start)}"
            )
            self._pump_events()
        except Exception:
            self._dead = True
        return None

    def close(self):
        if self._dead:
            return None
        try:
            self._window.close()
            self._dead = True
        except Exception:
            self._dead = True
        return None


# --- Win32扩展窗口样式(GWL_EXSTYLE)常量, 用于点击穿透+不抢焦点+不进任务栏 ---
_WIN_GWL_EXSTYLE = -20
_WIN_WS_EX_LAYERED = 0x00080000
_WIN_WS_EX_TRANSPARENT = 0x00000020
_WIN_WS_EX_NOACTIVATE = 0x08000000
_WIN_WS_EX_TOOLWINDOW = 0x00000080

_BG_HEX = "#ff9900"  # 与_BG_COLOR同一个亮橙色, 供tkinter使用
_FG_HEX = "#262626"  # 与mac版白值0.15对应的深灰(近黑)字色
_FG_DIM_HEX = "#4d4d4d"  # 与mac版白值0.3对应, 给耗时这行用的浅一点的字色


class _WindowsOverlay:
    """Windows版悬浮窗 — tkinter无边框窗口 + Win32扩展窗口样式.

    florr.io在Windows上一般是浏览器"F11"式无边框全屏(不是独占全屏的游戏),
    常规-topmost窗口就能盖上去, 不需要macOS那套跨Space的screensaver级别技巧。
    但仍需两件事保证main.py的输入不被打断:
      - WS_EX_TRANSPARENT: 悬浮窗完全不吃鼠标点击, 点击穿透到游戏画面.
      - WS_EX_NOACTIVATE:  悬浮窗永不抢键盘焦点(main.py靠空格键控制游戏).
    """

    _FIELDS = ("state", "pos", "target", "message")
    _LABELS_ZH = {"state": "状态", "pos": "位置", "target": "目标", "message": "消息"}

    def __init__(self):
        self._start = time.time()
        self._state = {"state": "-", "pos": None, "target": None, "message": "-"}
        # 一旦update/close在窗口没了之后抛异常, 就锁死后续调用为空操作, 绝不再炸主程序.
        self._dead = False

        root = tk.Tk()
        root.overrideredirect(True)  # 无标题栏/边框, 也不进Alt+Tab切换
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.92)
        root.configure(bg=_BG_HEX)
        root.geometry(f"{_WIDTH}x{_HEIGHT}+{_LEFT}+{_TOP_OFFSET}")
        root.resizable(False, False)
        self._root = root

        self._apply_clickthrough_noactivate(root)

        title = tk.Label(
            root, text="florr auto-pathing", bg=_BG_HEX, fg=_FG_HEX,
            font=("Segoe UI", 11, "bold"), anchor="w",
        )
        title.place(x=8, y=6, width=_WIDTH - 16, height=20)

        self._field_labels = {}
        for i, field in enumerate(self._FIELDS):
            label = tk.Label(
                root, text=f"{self._LABELS_ZH[field]}: -", bg=_BG_HEX, fg=_FG_HEX,
                font=("Segoe UI", 10), anchor="w",
            )
            label.place(x=8, y=6 + (i + 1) * 22, width=_WIDTH - 16, height=20)
            self._field_labels[field] = label

        self._elapsed_label = tk.Label(
            root, text="耗时: 00:00", bg=_BG_HEX, fg=_FG_DIM_HEX,
            font=("Segoe UI", 8), anchor="w",
        )
        self._elapsed_label.place(
            x=8, y=6 + (len(self._FIELDS) + 1) * 22, width=_WIDTH - 16, height=18,
        )

        root.update_idletasks()
        root.update()

    def _apply_clickthrough_noactivate(self, root):
        """叠加WS_EX_TRANSPARENT/NOACTIVATE/TOOLWINDOW扩展样式.

        root.winfo_id()拿到的是绘图表面的句柄, 其父窗口才是真正的顶层HWND
        (overrideredirect窗口下两者常常是同一个, 但GetParent失败时兜底用
        winfo_id()本身, 双保险不炸)。
        """
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        if not hwnd:
            hwnd = root.winfo_id()
        get_style = ctypes.windll.user32.GetWindowLongW
        set_style = ctypes.windll.user32.SetWindowLongW
        style = get_style(hwnd, _WIN_GWL_EXSTYLE)
        style |= (
            _WIN_WS_EX_LAYERED | _WIN_WS_EX_TRANSPARENT
            | _WIN_WS_EX_NOACTIVATE | _WIN_WS_EX_TOOLWINDOW
        )
        set_style(hwnd, _WIN_GWL_EXSTYLE, style)

    def update(self, state=None, pos=None, target=None, message=None):
        if self._dead:
            return None
        try:
            self._state = _merge_state(
                self._state, state=state, pos=pos, target=target, message=message,
            )
            for field in self._FIELDS:
                value = self._state[field]
                display = _format_pos(value) if field in ("pos", "target") else value
                self._field_labels[field].configure(text=f"{self._LABELS_ZH[field]}: {display}")
            self._elapsed_label.configure(
                text=f"耗时: {_format_elapsed(time.time() - self._start)}"
            )
            # 部分游戏/浏览器切换全屏时会把自己重新拉到最顶层, 每次update都
            # 重新断言一次-topmost, 防止悬浮窗被压到游戏画面下面.
            self._root.attributes("-topmost", True)
            self._root.update_idletasks()
            self._root.update()
        except Exception:
            self._dead = True
        return None

    def close(self):
        if self._dead:
            return None
        try:
            self._root.destroy()
            self._dead = True
        except Exception:
            self._dead = True
        return None


def create_overlay():
    """建悬浮窗, 建不起来(缺依赖/没display)就退化成空壳, 不炸主程序."""
    if _IS_MACOS:
        if AppKit is None:
            print("⚠️ 悬浮窗启动失败: pyobjc(AppKit) 不可用", file=sys.stderr)
            return _NullOverlay()
        try:
            return StatusOverlay()
        except Exception as e:
            print(f"⚠️ 悬浮窗启动失败: {e}", file=sys.stderr)
            return _NullOverlay()

    if _IS_WINDOWS:
        if tk is None or ctypes is None:
            print("⚠️ 悬浮窗启动失败: tkinter/ctypes 不可用", file=sys.stderr)
            return _NullOverlay()
        try:
            return _WindowsOverlay()
        except Exception as e:
            print(f"⚠️ 悬浮窗启动失败: {e}", file=sys.stderr)
            return _NullOverlay()

    print(f"⚠️ 悬浮窗启动失败: 不支持的平台 {sys.platform}", file=sys.stderr)
    return _NullOverlay()


if __name__ == "__main__":
    # 手动烟雾测试: 开窗, 循环几个假状态, 肉眼确认渲染/位置/跨Space显示对不对.
    demo = create_overlay()
    fake_states = [
        {"state": "寻路中", "pos": (53, 144), "target": (14, 45), "message": "规划路径..."},
        {"state": "移动中", "pos": (30, 90), "target": (14, 45), "message": "移动到 (30, 90)"},
        {"state": "卡住", "pos": (30, 90), "target": (14, 45), "message": "移动受阻"},
        {"state": "完成", "pos": (14, 45), "target": (14, 45), "message": "已到达目标区域"},
    ]
    for fake in fake_states:
        demo.update(**fake)
        time.sleep(2)
    demo.close()
