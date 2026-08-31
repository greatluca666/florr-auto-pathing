"""florr-auto-pathing 的控制面板 GUI. 无参跑 `python main.py` / 双击 exe 就进这里.

进程模型: 这个窗口不跑寻路逻辑 —— 点"开始"时它 (1) 用模态框引导好专用 Chrome
和 florr-auto-afk, (2) 把界面上的配置写进 config.json, (3) subprocess.Popen 一个
`main.py --worker` 子进程, 把它的 stdout 逐行灌进日志框. "停止"给子进程发信号让
它先 reset_keyboard() 再退出.
"""
import os
import signal
import subprocess
import sys
import threading

import customtkinter as ctk

import app_config
import afk_watch

_IS_WINDOWS = sys.platform == "win32"


def worker_command():
    """拉起 worker 子进程的命令行. frozen(PyInstaller)时 sys.executable 就是我们
    自己的 exe, 直接带 --worker; 脚本模式下要显式 python + main.py 路径, 加 -u 让
    子进程 stdout 行缓冲(日志实时进面板)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker"]
    main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return [sys.executable, "-u", main_py, "--worker"]


def build_worker_config(*, map_name, location, area, duration, short_limit,
                        enemy_ai, auto_switch, afk):
    """界面上的值 -> app_config schema 的 dict. 坐标统一转成 list(JSON 里没有 tuple)."""
    return {
        "map": map_name,
        "location": [int(location[0]), int(location[1])],
        "farming_area": [[int(area[0][0]), int(area[0][1])],
                         [int(area[1][0]), int(area[1][1])]],
        "farming_duration": int(duration),
        "consecutive_short_round_limit": int(short_limit),
        "enemy_ai_enabled": bool(enemy_ai),
        "auto_switch_server": bool(auto_switch),
        "afk_enabled": bool(afk),
    }


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("florr-auto-pathing")
        self.geometry("880x560")
        ctk.set_appearance_mode("dark")

        self._cfg = app_config.load_config()
        self.proc = None
        self._reader = None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ---- 侧栏 ----
        side = ctk.CTkFrame(self, width=120, corner_radius=0)
        side.grid(row=0, column=0, sticky="nsew")
        side.grid_rowconfigure(4, weight=1)  # spacer 行
        self._pages = {}
        for i, name in enumerate(("控制台", "账号", "时间表")):
            btn = ctk.CTkButton(side, text=name, anchor="w",
                                command=lambda n=name: self._show_page(n))
            btn.grid(row=i, column=0, padx=10, pady=(10 if i == 0 else 4, 4), sticky="ew")
            if name != "控制台":
                btn.configure(state="disabled")  # 阶段2

        # 底部大 AFK 开关
        afk_box = ctk.CTkFrame(side, fg_color="transparent")
        afk_box.grid(row=5, column=0, padx=10, pady=14, sticky="ew")
        ctk.CTkLabel(afk_box, text="自动检测 AFK", font=("", 12)).pack()
        self.afk_switch = ctk.CTkSwitch(afk_box, text="", command=self._on_afk_toggle)
        self.afk_switch.pack(pady=4)
        if self._cfg["afk_enabled"]:
            self.afk_switch.select()
        if not _IS_WINDOWS:
            self.afk_switch.configure(state="disabled")
            ctk.CTkLabel(afk_box, text="(仅 Windows)", font=("", 9),
                         text_color="gray").pack()

        # ---- 内容区: 占位, Task 8 填控制台页 ----
        self.content = ctk.CTkFrame(self)
        self.content.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        self._placeholder = ctk.CTkLabel(self.content, text="控制台(Task 8 填充)")
        self._placeholder.pack(expand=True)

    def _show_page(self, name):
        pass  # Task 8: 控制台是唯一可用页, 其余灰置

    def _on_afk_toggle(self):
        pass  # Task 10

    def on_closing(self):
        if self.proc and self.proc.poll() is None:
            self._stop_worker()
        self.destroy()


def main():
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
