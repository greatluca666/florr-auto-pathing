"""悬浮状态窗 — 全屏运行main.py时显示寻路/移动进度."""
import time
import tkinter


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


class StatusOverlay:
    _FIELDS = ("state", "pos", "target", "message")
    _LABELS_ZH = {"state": "状态", "pos": "位置", "target": "目标", "message": "消息"}

    def __init__(self):
        self._start = time.time()
        self._state = {"state": "-", "pos": None, "target": None, "message": "-"}

        self._root = tkinter.Tk()
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.attributes("-alpha", 0.85)
        self._root.geometry("260x150+20+20")
        self._root.configure(bg="#1e1e1e")

        tkinter.Label(
            self._root, text="florr auto-pathing", fg="#f8de60", bg="#1e1e1e",
            font=("Menlo", 12, "bold"),
        ).pack(anchor="w", padx=8, pady=(8, 4))

        self._field_labels = {}
        for field in self._FIELDS:
            label = tkinter.Label(
                self._root, text=f"{self._LABELS_ZH[field]}: -", fg="white",
                bg="#1e1e1e", font=("Menlo", 11), anchor="w", justify="left",
            )
            label.pack(anchor="w", padx=8, fill="x")
            self._field_labels[field] = label

        self._elapsed_label = tkinter.Label(
            self._root, text="耗时: 00:00", fg="#aaaaaa", bg="#1e1e1e", font=("Menlo", 10),
        )
        self._elapsed_label.pack(anchor="w", padx=8, pady=(4, 8))

        self._root.update_idletasks()
        self._root.update()

    def update(self, state=None, pos=None, target=None, message=None):
        self._state = _merge_state(
            self._state, state=state, pos=pos, target=target, message=message,
        )
        for field in self._FIELDS:
            value = self._state[field]
            display = _format_pos(value) if field in ("pos", "target") else value
            self._field_labels[field].config(text=f"{self._LABELS_ZH[field]}: {display}")
        self._elapsed_label.config(text=f"耗时: {_format_elapsed(time.time() - self._start)}")
        self._root.update_idletasks()
        self._root.update()

    def close(self):
        try:
            self._root.destroy()
        except tkinter.TclError:
            pass


def create_overlay():
    """建悬浮窗, 建不起来(没tkinter/没display)就退化成空壳, 不炸主程序."""
    try:
        return StatusOverlay()
    except Exception:
        return _NullOverlay()


if __name__ == "__main__":
    # 手动烟雾测试: 开窗, 循环几个假状态, 肉眼确认渲染/位置对不对.
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
