"""florr-auto-pathing 的控制面板 GUI. 无参跑 `python main.py` / 双击 exe 就进这里.

阶段2: 中间是"时块列表" —— 按星期几 + 时间段配 账号/地图/目标点/刷怪区.
点"开始调度"后, GUI 内的调度器(self.after 循环)每 30s 查一次此刻命中哪个时块,
按需 关掉 Chrome 换 profile 重开(--start-fullscreen + florr.io)、把该时块的刷怪
参数刷进 config['active']、(重)起一个 `main.py --worker` 子进程. 空档期停 worker.
调度驱动的运行全程零人工; 只有用户主动"新建账号 / 重新登录"才弹非模态登录引导.
"""
import os
import subprocess
import sys
import threading
import time
import traceback
from tkinter import messagebox

import customtkinter as ctk

import app_config
import afk_watch
import cdp_bridge
import gui_accounts
import gui_chrome_flow
import gui_schedule

_IS_WINDOWS = sys.platform == "win32"
_LOG_MAX_LINES = 2000  # 日志框最多留这么多行, 再多就从头截掉
_TICK_MS = 30_000      # 调度器 tick 间隔


def worker_command():
    """拉起 worker 子进程的命令行. frozen(PyInstaller)时 sys.executable 就是我们
    自己的 exe, 直接带 --worker; 脚本模式下要显式 python + main.py 路径, 加 -u 让
    子进程 stdout 行缓冲(日志实时进面板)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker"]
    main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return [sys.executable, "-u", main_py, "--worker"]


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


_MAP_PX = 300           # maps/*.png 都是 300x300; 派生坐标 clamp 到 [0, _MAP_PX-1]
_DERIVED_AREA_HALF = 12  # 只点了目标点没框区域时, 以点为中心生成的方块半边长(图像像素)


def _clamp_px(v):
    return max(0, min(_MAP_PX - 1, int(v)))


def resolve_point_and_area(point, area):
    """目标点和刷怪区域二选一即可(都给也行). 返回补全后的 (point, area);
    两个都没有则返回 (None, None), 让调用方报错.
      - 只有区域: 目标点 = 区域中心
      - 只有点: 刷怪区域 = 以点为中心的小方块(clamp 进地图范围)
    """
    if point is None and area is None:
        return None, None
    if area is None:
        x, y = int(point[0]), int(point[1])
        h = _DERIVED_AREA_HALF
        area = [(_clamp_px(x - h), _clamp_px(y - h)),
                (_clamp_px(x + h), _clamp_px(y + h))]
    if point is None:
        (x1, y1), (x2, y2) = area
        point = ((int(x1) + int(x2)) // 2, (int(y1) + int(y2)) // 2)
    return point, area


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


def plan_transition(running_id, new_block, chrome_profile):
    """给定当前在跑的时块 id + 此刻命中的时块 + 当前 Chrome 用的 profile,
    算出调度该干什么。纯函数, 无 I/O。
      noop  —— 什么都不用做(同一个时块, 或本来就空档)
      idle  —— 停 worker(从跑着变成空档)
      run   —— 停 worker + (可能)换 Chrome + 起 worker
    """
    if new_block is None:
        return {"action": "idle"} if running_id is not None else {"action": "noop"}
    if new_block["id"] == running_id:
        return {"action": "noop"}
    return {
        "action": "run",
        "relaunch_chrome": new_block["profile"] != chrome_profile,
        "profile": new_block["profile"],
    }


class _GuideHost:
    """把主窗口里一块 CTkFrame 包成 LoginGuide 要的 show()/hide() 接口。"""

    def __init__(self, frame, grid_kw):
        self._frame = frame
        self._grid_kw = grid_kw

    def show(self):
        self._frame.grid(**self._grid_kw)

    def hide(self):
        self._frame.grid_remove()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("florr-auto-pathing")
        self.geometry("900x620")
        ctk.set_appearance_mode("dark")

        self._cfg = app_config.load_config()
        self.proc = None
        self._reader = None
        self._closing = False

        # 调度器状态
        self._sched_running = False
        self._running_block_id = None
        self._chrome_profile = None
        self._tick_job = None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ---- 侧栏 ----
        side = ctk.CTkFrame(self, width=120, corner_radius=0)
        side.grid(row=0, column=0, sticky="nsew")
        side.grid_rowconfigure(4, weight=1)  # spacer 行
        for i, name in enumerate(("时间表", "账号")):
            ctk.CTkButton(side, text=name, anchor="w",
                          command=lambda n=name: self._show_page(n)).grid(
                row=i, column=0, padx=10, pady=(10 if i == 0 else 4, 4), sticky="ew")

        afk_box = ctk.CTkFrame(side, fg_color="transparent")
        afk_box.grid(row=5, column=0, padx=10, pady=14, sticky="ew")
        ctk.CTkLabel(afk_box, text="自动检测 AFK", font=("", 12)).pack()
        self.afk_switch = ctk.CTkSwitch(afk_box, text="", command=self._on_afk_toggle)
        self.afk_switch.pack(pady=4)
        if self._cfg["afk_enabled"]:
            self.afk_switch.select()
            self.after(400, self._ensure_afk)
        if not _IS_WINDOWS:
            self.afk_switch.configure(state="disabled")
            ctk.CTkLabel(afk_box, text="(仅 Windows)", font=("", 9),
                         text_color="gray").pack()

        inv_box = ctk.CTkFrame(side, fg_color="transparent")
        inv_box.grid(row=6, column=0, padx=10, pady=(0, 10), sticky="ew")
        ctk.CTkLabel(inv_box, text="florr 反转键", font=("", 12)).pack()
        self.invert_attack_switch = ctk.CTkSwitch(
            inv_box, text="反转攻击键",
            command=lambda: self._persist_flag("invert_attack", self.invert_attack_switch.get()))
        self.invert_attack_switch.pack(pady=(4, 0), anchor="w")
        if self._cfg["invert_attack"]:
            self.invert_attack_switch.select()
        self.invert_defense_switch = ctk.CTkSwitch(
            inv_box, text="反转防御键",
            command=lambda: self._persist_flag("invert_defense", self.invert_defense_switch.get()))
        self.invert_defense_switch.pack(pady=(2, 0), anchor="w")
        if self._cfg["invert_defense"]:
            self.invert_defense_switch.select()

        # ---- 主区 ----
        self.content = ctk.CTkFrame(self)
        self.content.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        # 登录引导区(默认隐藏) —— 先建, 下面账号页要用它
        self._guide_frame = ctk.CTkFrame(self.content)
        self._guide_grid_kw = dict(row=1, column=0, sticky="ew", pady=(8, 0))
        for txt in ("① 已在 Chrome 打开 florr.io",
                    "② 在那个窗口登录你的账号",
                    "③ 登录完成后点右边 →"):
            ctk.CTkLabel(self._guide_frame, text=txt, anchor="w").pack(
                anchor="w", padx=10)
        gbtns = ctk.CTkFrame(self._guide_frame, fg_color="transparent")
        gbtns.pack(anchor="e", padx=10, pady=6)
        ctk.CTkButton(gbtns, text="完成", width=70,
                      command=lambda: self._login_guide.finish()).pack(side="left", padx=4)
        ctk.CTkButton(gbtns, text="取消", width=70, fg_color="gray",
                      command=lambda: self._login_guide.cancel()).pack(side="left")
        self._login_guide = gui_chrome_flow.LoginGuide(
            _GuideHost(self._guide_frame, self._guide_grid_kw), after=self.after)

        self._sched_list = gui_schedule.ScheduleList(
            self.content, get_cfg=self._get_cfg, save_cfg=self._save_cfg,
            open_editor=self._open_editor)
        self._accounts = gui_accounts.AccountsPage(
            self.content, get_cfg=self._get_cfg, save_cfg=self._save_cfg,
            login_guide=self._login_guide)
        self._accounts.new_profile_cb = self._make_profile_then

        self.log_box = ctk.CTkTextbox(self.content, font=("Menlo", 11), state="disabled")
        self.log_box.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        self.content.grid_rowconfigure(2, weight=1)

        self.status_label = ctk.CTkLabel(self.content, text="状态：未运行", anchor="w")
        self.status_label.grid(row=3, column=0, sticky="ew", pady=(8, 4))

        self.start_btn = ctk.CTkButton(self.content, text="▶ 开始调度", height=40,
                                       command=self._on_start_stop)
        self.start_btn.grid(row=4, column=0, sticky="ew")

        self._page_widgets = {"时间表": self._sched_list, "账号": self._accounts}
        self._show_page("时间表")

        # 放在最后: 这个钩子会往 self.log_box 里写, 得等控件都建好.
        self.report_callback_exception = self._report_exception

    # ---- cfg 读写 ----
    def _get_cfg(self):
        return self._cfg

    def _save_cfg(self, cfg):
        app_config.save_config(cfg)
        self._cfg = app_config.load_config()
        return self._cfg

    # ---- 页面切换 ----
    def _show_page(self, name):
        for n, w in self._page_widgets.items():
            if n == name:
                w.grid(row=0, column=0, sticky="nsew")
            else:
                w.grid_remove()

    # ---- 时块编辑 ----
    def _open_editor(self, block):
        if self._sched_running:
            return
        cfg = self._cfg
        tpl = block if block is not None else gui_schedule.new_block_template(cfg)
        others = [b for b in cfg["schedule"] if b.get("id") != tpl.get("id")]
        ed = gui_schedule.TimeBlockEditor(
            self, block=tpl, others=others, profiles=cfg["profiles"],
            on_save=self._save_block)
        ed.new_profile_cb = self._make_profile_then

    def _save_block(self, blk):
        sched = self._cfg["schedule"]
        for i, b in enumerate(sched):
            if b["id"] == blk["id"]:
                sched[i] = blk
                break
        else:
            sched.append(blk)
        self._save_cfg(self._cfg)
        self._sched_list.refresh()

    def _make_profile_then(self, alias, on_ready):
        """账号页 / 编辑器下拉里"＋新建"共用: 建 profile + 目录 + 存 + 登录引导。"""
        cfg, err = gui_accounts.add_profile(self._cfg, alias)
        if err:
            self._log_line(f"新建账号失败: {err}\n")
            return
        rel = gui_accounts.profile_dir(cfg, alias)
        try:
            os.makedirs(gui_accounts.abs_profile_path(rel), exist_ok=True)
        except OSError as e:
            self._log_line(f"建 profile 目录失败: {e}\n")
            return
        self._save_cfg(cfg)
        self._accounts.refresh()
        self._login_guide.start(
            gui_accounts.abs_profile_path(rel),
            on_done=lambda: (self._accounts.refresh(), on_ready(alias)),
            on_cancel=lambda: None)

    # ---- 调度器 ----
    def _on_start_stop(self):
        if self._sched_running:
            self._sched_running = False
            if self._tick_job is not None:
                self.after_cancel(self._tick_job)
                self._tick_job = None
            self._stop_worker_sync()
            self._running_block_id = None
            self._sched_list.set_readonly(False)
            self._accounts.set_readonly(False)
            self.start_btn.configure(text="▶ 开始调度")
            self.status_label.configure(text="状态：未运行")
            self._log_line("—— 调度已停止 ——\n")
            return

        if not self._cfg["schedule"]:
            self._log_line("⚠️ 时间表是空的, 先加一个时块\n")
            return
        self._sched_running = True
        self._sched_list.set_readonly(True)
        self._accounts.set_readonly(True)
        self.start_btn.configure(text="■ 停止调度")
        self._log_line("—— 调度已启动 ——\n")
        self._sched_tick()

    def _sched_tick(self):
        lt = time.localtime()
        weekday = lt.tm_wday                       # Python: 周一=0 —— 跟本项目编号一致
        hhmm = "%02d:%02d" % (lt.tm_hour, lt.tm_min)
        blk = app_config.active_block(self._cfg["schedule"], weekday, hhmm)
        plan = plan_transition(self._running_block_id, blk, self._chrome_profile)
        if plan["action"] == "idle":
            self._stop_worker_sync()
            self._running_block_id = None
            self._log_line("⏸ 空档, worker 已停\n")
        elif plan["action"] == "run":
            self._enter_block(blk, plan["relaunch_chrome"])
        self._update_status(blk, weekday, hhmm)
        if self._sched_running:
            self._tick_job = self.after(_TICK_MS, self._sched_tick)

    def _enter_block(self, blk, relaunch):
        self._stop_worker_sync()
        if relaunch:
            rel = gui_accounts.profile_dir(self._cfg, blk["profile"])
            if rel is None:
                self._log_line(f"⚠️ 账号『{blk['profile']}』不存在, 跳过时块 {blk['id']}\n")
                self._running_block_id = None
                return
            pdir = gui_accounts.abs_profile_path(rel)
            if not os.path.isdir(pdir):
                self._log_line(f"⚠️ 账号『{blk['profile']}』还没登录过, 跳过时块 {blk['id']}\n")
                self._running_block_id = None
                return
            self._log_line(f"切到账号『{blk['profile']}』, 重开 Chrome…\n")
            try:
                cdp_bridge.launch_chrome_for_profile(pdir, fullscreen=True)
            except RuntimeError as e:
                self._log_line(f"⚠️ 起 Chrome 失败: {e}, 跳过时块 {blk['id']}\n")
                self._running_block_id = None
                return
            if cdp_bridge.wait_for_florr_tab(30) is None:
                self._log_line(f"⚠️ 账号『{blk['profile']}』未登录 / florr.io 没起来, "
                               f"跳过时块 {blk['id']}\n")
                self._running_block_id = None
                return
            self._chrome_profile = blk["profile"]

        self._cfg["active"] = gui_schedule.block_to_active(blk)
        app_config.save_config(self._cfg)
        self._spawn_worker()
        self._running_block_id = blk["id"]
        self._log_line(f"▶ 进入时块 {blk['id']}({blk['profile']} / {blk['map']}) "
                       f"{blk['start']}–{blk['end']}\n")

    def _update_status(self, blk, weekday, hhmm):
        nb = app_config.next_start(self._cfg["schedule"], weekday, hhmm)
        if nb is None:
            nxt = "下一个 —"
        else:
            d, t = nb
            same = "今天 " if d == weekday else f"周{gui_schedule.WEEKDAY_LABELS[d]} "
            nxt = f"下一个 {same}{t}"
        if blk is None:
            self.status_label.configure(text=f"状态：空档 · {nxt}")
        else:
            self.status_label.configure(
                text=f"状态：{blk['id']}({blk['profile']}/{blk['map']}) "
                     f"{blk['start']}–{blk['end']} · {nxt}")

    # ---- worker 子进程 ----
    def _spawn_worker(self):
        kwargs = {"env": {**os.environ, "PYTHONUNBUFFERED": "1",
                          "PYTHONIOENCODING": "utf-8"}}
        self.proc = subprocess.Popen(
            worker_command(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            bufsize=1, **kwargs)
        self._log_line("—— worker 已启动 ——\n")
        self._reader = threading.Thread(target=self._pump_log, args=(self.proc,),
                                        daemon=True)
        self._reader.start()

    def _pump_log(self, proc):
        for line in proc.stdout:
            if self._closing:
                return
            self.after(0, self._log_line, line)
        code = proc.wait()
        if self._closing:
            return
        self.after(0, self._on_worker_exit, proc, code)

    def _stop_worker_sync(self):
        """同步、有上限地收干净当前 worker. 关 stdin(EOF)让 worker 自己
        reset_keyboard() 再退; POSIX 上补一发 SIGTERM; 最多等 3s, 还活着就 kill.
        收完把 self.proc 置 None —— 慢半拍的 _pump_log 回调会因 proc != self.proc 早退."""
        proc = self.proc
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception as e:
            self._log_line(f"发送停止信号失败: {e}\n")
        if not _IS_WINDOWS:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
        if proc.poll() is None:
            proc.kill()
            self._log_line("—— worker 未响应, 已强制结束 ——\n")
        self.proc = None

    def _on_worker_exit(self, proc, code):
        if proc is not self.proc:
            return
        self._log_line(f"—— worker 结束 (退出码 {code}) ——\n")
        self.proc = None
        if self._sched_running:
            # 崩溃自愈: 清掉当前时块记号, 下次 tick 会重新进这个时块.
            self._running_block_id = None
        else:
            self.status_label.configure(text="状态：未运行")
            self.start_btn.configure(text="▶ 开始调度")

    # ---- AFK ----
    def _persist_afk(self, enabled):
        self._persist_flag("afk_enabled", enabled)

    def _persist_flag(self, key, value):
        """通用: 把一个顶层 bool 开关落盘. 不做 CDP 写 —— 下一轮 worker 生效."""
        cfg = app_config.load_config()
        cfg[key] = bool(value)
        app_config.save_config(cfg)
        self._cfg = app_config.load_config()

    def _busy_modal(self, text):
        top = ctk.CTkToplevel(self)
        top.title("")
        top.geometry("320x90")
        top.resizable(False, False)
        top.transient(self)
        top.protocol("WM_DELETE_WINDOW", lambda: None)
        ctk.CTkLabel(top, text=text).pack(expand=True, padx=20, pady=20)
        return top

    def _ensure_afk(self):
        if not _IS_WINDOWS:
            return
        if getattr(self, "_afk_busy", False):
            return
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

        self._afk_busy = True
        self.afk_switch.configure(state="disabled")
        modal = self._busy_modal("AFK 助手准备中，请稍候…\n(界面仍可操作)")

        def _work():
            try:
                outcome = start_afk(exe_exists=exe_exists, running=False,
                                    confirm_download=lambda: True)
                if outcome in ("started", "downloaded"):
                    afk_watch.ensure_florr_auto_afk_running()
            except Exception as e:
                outcome = f"出错: {e}"
            self.after(0, self._finish_ensure_afk, modal, outcome)

        threading.Thread(target=_work, daemon=True).start()

    def _finish_ensure_afk(self, modal, outcome):
        self._afk_busy = False
        try:
            self.afk_switch.configure(state="normal")
        except Exception:
            pass
        try:
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

    # ---- 杂项 ----
    def _report_exception(self, exc_type, exc_value, exc_tb):
        self._log_line(f"❌ {exc_type.__name__}: {exc_value}\n")
        traceback.print_exception(exc_type, exc_value, exc_tb)

    def _log_line(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text)
        lines = int(self.log_box.index("end-1c").split(".")[0])
        if lines > _LOG_MAX_LINES:
            self.log_box.delete("1.0", f"end-{_LOG_MAX_LINES}l")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def on_closing(self):
        self._closing = True
        if self._tick_job is not None:
            try:
                self.after_cancel(self._tick_job)
            except Exception:
                pass
        self._stop_worker_sync()
        self.destroy()


def main():
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
