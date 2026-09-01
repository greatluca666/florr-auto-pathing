"""florr-auto-pathing 的控制面板 GUI. 无参跑 `python main.py` / 双击 exe 就进这里.

进程模型: 这个窗口不跑寻路逻辑 —— 点"开始"时它 (1) 用模态框引导好专用 Chrome
和 florr-auto-afk, (2) 把界面上的配置写进 config.json, (3) subprocess.Popen 一个
`main.py --worker` 子进程, 把它的 stdout 逐行灌进日志框. "停止"关掉子进程的 stdin
管道(EOF), worker 那边的看门线程读到 EOF 就先 reset_keyboard() 再退出.
"""
import os
import subprocess
import sys
import threading
import traceback
from tkinter import messagebox

import customtkinter as ctk

import app_config
import afk_watch
import gui_chrome_flow

_IS_WINDOWS = sys.platform == "win32"
_LOG_MAX_LINES = 2000  # 日志框最多留这么多行, 再多就从头截掉


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


def parse_positive_ints(*strs):
    """把界面上的数字框字符串批量转成正整数. 任一非法(非整数 / <=0)返回 None.
    不用 assert —— python -O 会把 assert 整个剥掉."""
    out = []
    for s in strs:
        try:
            n = int(s)
        except (TypeError, ValueError):
            return None
        if n <= 0:
            return None
        out.append(n)
    return out


def start_afk(*, exe_exists, running, confirm_download):
    """AFK 开关打开时的决策(纯函数, 副作用由调用方按返回值执行).
    already: 已在跑, 什么都不用做
    declined: exe 缺, 用户拒绝下载
    downloaded / download_failed: exe 缺, 下过了(成/败)
    started: exe 在, 需要调用方去 ensure_florr_auto_afk_running()
    """
    if running:
        return "already"
    if not exe_exists:
        if not confirm_download():
            return "declined"
        return "downloaded" if afk_watch.download_florr_auto_afk() else "download_failed"
    return "started"


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("florr-auto-pathing")
        self.geometry("880x560")
        ctk.set_appearance_mode("dark")

        self._cfg = app_config.load_config()
        self.proc = None
        self._reader = None
        self._closing = False

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

        # ---- 控制台页 ----
        self.content = ctk.CTkFrame(self)
        self.content.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        self.content.grid_columnconfigure(0, weight=3)
        self.content.grid_columnconfigure(1, weight=2)
        self.content.grid_rowconfigure(1, weight=1)

        self.map_menu = ctk.CTkOptionMenu(
            self.content, values=list(app_config._VALID_MAPS),
            command=self._on_map_change)
        self.map_menu.set(self._cfg["map"])
        self.map_menu.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        from gui_map_picker import MapPicker
        self.picker = MapPicker(
            self.content,
            on_point_change=self._on_picker_point,
            on_area_change=self._on_picker_area)
        self.picker.grid(row=1, column=0, sticky="nsew", padx=(0, 10))

        right = ctk.CTkFrame(self.content, fg_color="transparent")
        right.grid(row=1, column=1, sticky="nsew")

        self.duration_entry = self._labeled_entry(right, "刷怪时长 (秒)",
                                                  str(self._cfg["farming_duration"]))
        self.enemy_switch = ctk.CTkSwitch(right, text="索敌 AI (YOLO 追击/规避)")
        self.enemy_switch.pack(anchor="w", pady=6)
        if self._cfg["enemy_ai_enabled"]:
            self.enemy_switch.select()
        self.short_entry = self._labeled_entry(
            right, "连续短局阈值", str(self._cfg["consecutive_short_round_limit"]))
        self.autoswitch_check = ctk.CTkCheckBox(right, text="连续没刷满自动换服务器")
        self.autoswitch_check.pack(anchor="w", pady=6)
        if self._cfg["auto_switch_server"]:
            self.autoswitch_check.select()

        self.log_box = ctk.CTkTextbox(right, font=("Menlo", 11), state="disabled")
        self.log_box.pack(fill="both", expand=True, pady=(8, 0))

        self.status_label = ctk.CTkLabel(self.content, text="状态：未运行", anchor="w")
        self.status_label.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 4))

        self.start_btn = ctk.CTkButton(self.content, text="▶ 开始", height=40,
                                       command=self._on_start_stop)
        self.start_btn.grid(row=3, column=0, columnspan=2, sticky="ew")

        # 初值灌进选择器
        self.picker.load_map(self._cfg["map"])
        self.picker.set_point(tuple(self._cfg["location"]))
        self.picker.set_area([tuple(p) for p in self._cfg["farming_area"]])
        self._point = tuple(self._cfg["location"])
        self._area = [tuple(p) for p in self._cfg["farming_area"]]

        # 放在最后: 这个钩子会往 self.log_box 里写, 得等控件都建好.
        self.report_callback_exception = self._report_exception

    def _report_exception(self, exc_type, exc_value, exc_tb):
        # console=False 后 Tk 回调里未捕获的异常本来会写进一个丢弃一切的 stderr ——
        # 这是这个窗口唯一能把"出错了"告诉用户的地方.
        self._log_line(f"❌ {exc_type.__name__}: {exc_value}\n")
        traceback.print_exception(exc_type, exc_value, exc_tb)

    def _labeled_entry(self, parent, label, initial):
        ctk.CTkLabel(parent, text=label, anchor="w").pack(anchor="w")
        e = ctk.CTkEntry(parent)
        e.insert(0, initial)
        e.pack(anchor="w", fill="x", pady=(0, 6))
        return e

    def _on_map_change(self, name):
        self.picker.load_map(name)
        self.picker.set_point(None)
        self.picker.set_area(None)
        self._point = None
        self._area = None
        self._log_line(f"已切到 {name}，请重新点目标点 / 框刷怪区\n")

    def _on_picker_point(self, pt):
        self._point = pt

    def _on_picker_area(self, area):
        self._area = [tuple(area[0]), tuple(area[1])]

    def _current_values(self):
        return dict(
            map_name=self.map_menu.get(),
            location=self._point,
            area=self._area,
            duration=self.duration_entry.get(),
            short_limit=self.short_entry.get(),
            enemy_ai=bool(self.enemy_switch.get()),
            auto_switch=bool(self.autoswitch_check.get()),
            afk=bool(self.afk_switch.get()),
        )

    def _log_line(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text)
        # bot 是按天跑的, 日志框不能无限长: 超过上限就把最老的那些行整段删掉,
        # 只留最近 _LOG_MAX_LINES 行.
        lines = int(self.log_box.index("end-1c").split(".")[0])
        if lines > _LOG_MAX_LINES:
            self.log_box.delete("1.0", f"end-{_LOG_MAX_LINES}l")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _on_start_stop(self):
        if self.proc and self.proc.poll() is None:
            self._stop_worker()
        else:
            self._start_worker()

    def _start_worker(self):
        # fail-fast: 先跑本地那几个便宜的校验, 再走重活儿 —— Chrome 重启 / AFK 下载
        # (最多卡 90 秒) / 切全屏都在校验之后. 点了开始却没框刷怪区 / 数字填错的用户
        # 立刻收到提示, 不会先被一串对话框拖一遍. 顺序:
        # 校验 location/area → 校验数字 → 专用 Chrome 就绪 → (开关开着才)确保
        # florr-auto-afk → 切全屏确认(紧贴 Popen 之前). 任一步取消就静默中止.
        vals = self._current_values()
        if vals["location"] is None or vals["area"] is None:
            self._log_line("⚠️ 请先在地图上点目标点并框出刷怪区\n")
            return
        # 只要校验结果, 转好的数字用不上 —— build_worker_config() 自己会
        # int() 一遍 vals 里的原始字符串.
        if parse_positive_ints(vals["duration"], vals["short_limit"]) is None:
            self._log_line("⚠️ 时长 / 短局阈值必须是正整数\n")
            return

        try:
            gui_chrome_flow.ensure_chrome_ready(self)
        except gui_chrome_flow.ChromeSetupCancelled:
            self._log_line("已取消(专用 Chrome 未就绪)\n")
            return
        if bool(self.afk_switch.get()):
            self._ensure_afk()

        # 这一步之后 florr.io 多半已经在 F11 全屏了; Windows 的前台锁会让一个
        # 普通窗口弹出来的对话框排在全屏游戏后面(用户只看到画面卡住, 找不到框).
        # 临时把主窗口置顶, 让对话框跟着浮到全屏之上, 问完立刻取消置顶.
        self.attributes("-topmost", True)
        try:
            ok = messagebox.askokcancel(
                "把 florr.io 切到全屏",
                "开始前请把 florr.io 切到全屏(任意分辨率), 然后点确定。",
                parent=self)
        finally:
            self.attributes("-topmost", False)
        if not ok:
            self._log_line("已取消(未确认全屏)\n")
            return

        cfg = build_worker_config(**vals)
        app_config.save_config(cfg)
        self._cfg = cfg

        # 两个平台都给子进程 PYTHONUNBUFFERED=1: frozen(PyInstaller)build 没走
        # worker_command() 里的 -u, 不设这个 Windows 下子进程 stdout 会块缓冲,
        # 日志框只能几 KB 一跳.
        # PYTHONIOENCODING=utf-8: 中文 Windows 上子进程 stdout 默认按 locale(GBK)
        # 编码, worker 打的 emoji/中文里带的字节 GBK 解不了, _pump_log 那边整个
        # 线程会 UnicodeDecodeError 崩掉. 两端都钉 utf-8, 再在读取侧 errors=replace
        # 兜底任何漏网的坏字节.
        kwargs = {"env": {**os.environ, "PYTHONUNBUFFERED": "1",
                          "PYTHONIOENCODING": "utf-8"}}

        # stdin=PIPE 不是为了往里写东西, 而是为了能"关"它: 关掉管道 = 给 worker
        # 发停止信号(见 _stop_worker). 不给 PIPE 的话子进程会继承 GUI 的 stdin,
        # 打包成 console=False 的 exe 后那是个死句柄, 永远读不到 EOF.
        self.proc = subprocess.Popen(
            worker_command(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            bufsize=1, **kwargs)
        self._log_line("—— worker 已启动 ——\n")
        self.start_btn.configure(text="■ 停止")
        self._reader = threading.Thread(target=self._pump_log, args=(self.proc,),
                                        daemon=True)
        self._reader.start()

    def _pump_log(self, proc):
        # 这是后台线程: 窗口一旦 destroy 掉, self.after() 会抛 TclError(而
        # console=False 下那个 traceback 谁也看不到). _closing 一置起就安静收摊.
        for line in proc.stdout:
            if self._closing:
                return
            self.after(0, self._log_line, line)
            self.after(0, lambda l=line: self.status_label.configure(
                text="状态：" + l.strip()[:60]) if l.strip() else None)
        code = proc.wait()
        if self._closing:
            return
        # 把 proc 绑进回调: 这条 pump 属于哪个进程, 回调就只对那个进程生效 ——
        # 否则一个慢半拍的旧 pump 会把刚启动的新 worker 的状态给清了.
        self.after(0, self._on_worker_exit, proc, code)

    def _stop_worker(self):
        if not self.proc:
            return
        try:
            if self.proc.stdin:
                # 关掉管道 = worker 那边 stdin 读到 EOF, 它自己 reset_keyboard()
                # 再退出. 之前用的 CTRL_BREAK_EVENT 只能发给"调用方所在的控制台
                # 进程组" —— 打包成 console=False 的 exe 后 GUI 根本没有控制台,
                # 那一发必然失败, 3 秒后直接 kill, 按住的 space+WASD 全留在游戏里.
                self.proc.stdin.close()
        except Exception as e:
            self._log_line(f"发送停止信号失败: {e}\n")
        if not _IS_WINDOWS:
            try:
                self.proc.terminate()   # POSIX 双保险: SIGTERM 处理器也会 reset_keyboard()
            except Exception:
                pass
        self.after(3000, lambda p=self.proc: self._force_kill_if_alive(p))

    def _force_kill_if_alive(self, proc):
        # 3 秒前排这个兜底时对的是 proc 那个进程; 期间它可能已经退干净、用户还
        # 又点了一次开始 —— 那 self.proc 就是另一个进程了, 这一发不能打到它身上.
        if proc is None or proc is not self.proc:
            return
        if proc.poll() is None:
            proc.kill()
            self._log_line("—— worker 未响应, 已强制结束 ——\n")

    def _on_worker_exit(self, proc, code):
        if proc is not self.proc:
            return
        self._log_line(f"—— worker 结束 (退出码 {code}) ——\n")
        self.status_label.configure(text="状态：未运行")
        self.start_btn.configure(text="▶ 开始")
        self.proc = None

    def _show_page(self, name):
        pass  # Task 8: 控制台是唯一可用页, 其余灰置

    def _persist_afk(self, enabled):
        cfg = app_config.load_config()
        cfg["afk_enabled"] = bool(enabled)
        app_config.save_config(cfg)
        self._cfg = cfg

    def _busy_modal(self, text):
        """一个没有关闭按钮的小模态框, 用来盖住"后台正在干重活儿"的那段时间.
        返回 toplevel, 调用方负责 destroy()."""
        top = ctk.CTkToplevel(self)
        top.title("")
        top.geometry("320x90")
        top.resizable(False, False)
        top.transient(self)
        top.protocol("WM_DELETE_WINDOW", lambda: None)   # 不给关
        ctk.CTkLabel(top, text=text).pack(expand=True, padx=20, pady=20)
        top.grab_set()
        return top

    def _ensure_afk(self):
        """AFK 开关打开时确保 florr-auto-afk 在跑. 决策(要不要下载)留在主线程 ——
        它得弹模态框; 真正的重活儿(350MB 下载 / 最长 90 秒的启动等待)扔进后台线程,
        否则 Tk 主循环一卡好几分钟, Windows 会把窗口画成"未响应"让用户去强杀它.
        本函数起完线程就返回, 不等结果(AFK 助手晚几秒起来没关系, worker 那边
        poll_afk_pause() 只是在 tail 它的日志)."""
        if not _IS_WINDOWS:
            return

        # download_florr_auto_afk() 本身也是重活儿, 但它藏在 start_afk() 里 ——
        # 把"确认下载"这一步之后的全部动作(下载 + 启动)一起放进后台线程, 只留
        # askyesno 在主线程上问.
        exe_exists = os.path.isfile(afk_watch._EXE_PATH)
        running = afk_watch.is_florr_auto_afk_running()
        if running:
            self._log_line("AFK: already\n")
            return
        if not exe_exists:
            if not messagebox.askyesno(
                    "下载 florr-auto-afk?",
                    "没检测到 florr-auto-afk(处理 AFK 弹窗用). 现在下载? 约 350MB.",
                    parent=self):
                self._log_line("AFK: declined\n")
                return

        modal = self._busy_modal("AFK 助手准备中，请稍候…")

        def _work():
            try:
                outcome = start_afk(exe_exists=exe_exists, running=False,
                                    confirm_download=lambda: True)
                if outcome in ("started", "downloaded"):
                    afk_watch.ensure_florr_auto_afk_running()
            except Exception as e:                       # 后台线程里没人接异常
                outcome = f"出错: {e}"
            self.after(0, self._finish_ensure_afk, modal, outcome)

        threading.Thread(target=_work, daemon=True).start()

    def _finish_ensure_afk(self, modal, outcome):
        try:
            modal.grab_release()
            modal.destroy()
        except Exception:
            pass
        self._log_line(f"AFK: {outcome}\n")

    def _on_afk_toggle(self):
        enabled = bool(self.afk_switch.get())
        if enabled:
            self._ensure_afk()
        else:
            afk_watch.stop_florr_auto_afk()
            self._log_line("AFK: 已停止 florr-auto-afk\n")
        self._persist_afk(enabled)

    def on_closing(self):
        # _stop_worker 只 self.after(3000, ...) 排一个兜底 kill —— 关窗时 mainloop
        # 马上就结束了, 那个回调根本不会跑. 所以这里同步、有上限地收干净子进程,
        # 否则慢 / 卡死 / 收不到停止信号的 worker(连同它的 segment.exe 孙进程)会被
        # 甩给 init 继续跑, 没有 UI 能再停它.
        self._closing = True        # 让还在跑的 _pump_log 线程别再往已死的窗口排回调
        proc = self.proc
        if proc and proc.poll() is None:
            self._stop_worker()     # 关 stdin(EOF) + POSIX 上补一发 SIGTERM
            try:
                proc.wait(timeout=3)
            except Exception:
                pass
            if proc.poll() is None:
                proc.kill()
        self.destroy()


def main():
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
