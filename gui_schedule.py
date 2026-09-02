"""时块调度 UI: 编辑器(CTkToplevel) + 列表折叠行 + 校验纯函数 + Tooltip.

纯函数(_safe_dirname / validate_block / block_to_active)不碰 tk, 单测直接调.
控件类(TimeBlockEditor / ScheduleList)才 import customtkinter —— 放在文件下半段.
"""
import re
import tkinter as tk

import customtkinter as ctk

import app_config

WEEKDAY_LABELS = ("一", "二", "三", "四", "五", "六", "日")

_ACTIVE_KEYS = app_config._ACTIVE_KEYS
# 目录名: 保留 \w(含汉字)和连字符, 其余替换成 _, 首尾 _ 去掉.
_SAFE_DIR_RE = re.compile(r"[^\w\-]", re.UNICODE)


def _safe_dirname(name):
    if not isinstance(name, str):
        return ""
    cleaned = _SAFE_DIR_RE.sub("_", name.strip())
    return cleaned.strip("_")


def block_to_active(block):
    """一个时块 -> worker 只读的 active 切片(7 个刷怪参数, 数值规整)."""
    loc = block["location"]
    area = block["farming_area"]
    return {
        "map": block["map"],
        "location": [int(loc[0]), int(loc[1])],
        "farming_area": [[int(area[0][0]), int(area[0][1])],
                         [int(area[1][0]), int(area[1][1])]],
        "farming_duration": int(block["farming_duration"]),
        "consecutive_short_round_limit": int(block["consecutive_short_round_limit"]),
        "enemy_ai_enabled": bool(block["enemy_ai_enabled"]),
        "auto_switch_server": bool(block["auto_switch_server"]),
    }


def _positive_int(v):
    try:
        return int(v) > 0
    except (TypeError, ValueError):
        return False


def validate_block(block, others):
    """返回错误中文串, 或 None 表示通过. others 里跟 block 同 id 的会被跳过."""
    if not block.get("days"):
        return "至少勾一个星期"
    start, end = block.get("start"), block.get("end")
    if not (app_config._valid_time(start) and app_config._valid_time(end)):
        return "时间格式要是 HH:MM"
    if start == end and start != "00:00":
        return "起止时间不能相同(全天请填 00:00–00:00)"
    if block.get("map") not in app_config._GUI_ENABLED_MAPS:
        return "海洋 / 蚁狱暂不可用, 请选沙漠"
    if not block.get("location") and not block.get("farming_area"):
        return "在地图上点个目标点, 或框个刷怪区"
    if not _positive_int(block.get("farming_duration")):
        return "刷怪时长要是正整数"
    if not _positive_int(block.get("consecutive_short_round_limit")):
        return "连续短局阈值要是正整数"
    for o in others:
        if o.get("id") == block.get("id"):
            continue
        if app_config.blocks_overlap(block, o):
            return f"跟时块 {o.get('id')} 时间重叠"
    return None


def _map_radio_state(map_name):
    """时块编辑器地图 radio 的 tk state: 不在 _GUI_ENABLED_MAPS 里的置灰."""
    return "normal" if map_name in app_config._GUI_ENABLED_MAPS else "disabled"


def new_block_template(cfg):
    """新建时块的默认值. 索敌 / 换服默认开(canvas 解码识怪, 不需要模型文件)."""
    return {
        "id": fresh_block_id(cfg), "enabled": True, "days": [],
        "start": "09:00", "end": "12:00",
        "profile": cfg["profiles"][0]["alias"] if cfg.get("profiles") else "默认",
        "map": "desert", "location": None, "farming_area": None,
        "farming_duration": 300, "consecutive_short_round_limit": 2,
        "enemy_ai_enabled": True, "auto_switch_server": True,
    }


def fresh_block_id(cfg):
    n = 0
    for b in cfg.get("schedule", []):
        m = re.match(r"blk-(\d+)$", str(b.get("id", "")))
        if m:
            n = max(n, int(m.group(1)))
    return f"blk-{n + 1}"


def weekday_short(days):
    return "".join(WEEKDAY_LABELS[d] for d in sorted(days)) or "—"


# ─────────────────────────────── 控件层 ───────────────────────────────

class _Tooltip:
    """悬停解释. CustomTkinter 没内置, 纯 tkinter 手搓: <Enter> 后 400ms 弹一个
    无边框 Toplevel, <Leave> / 点击销毁."""

    def __init__(self, widget, text):
        self._w = widget
        self._text = text
        self._tip = None
        self._job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._job = self._w.after(400, self._show)

    def _show(self):
        if self._tip is not None:
            return
        x = self._w.winfo_rootx() + 12
        y = self._w.winfo_rooty() + self._w.winfo_height() + 6
        self._tip = tk.Toplevel(self._w)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self._tip, text=self._text, justify="left", wraplength=280,
                 bg="#2b2b2b", fg="#e5e5e5", relief="solid", borderwidth=1,
                 padx=8, pady=5).pack()

    def _hide(self, _e=None):
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    def _cancel(self):
        if self._job is not None:
            self._w.after_cancel(self._job)
            self._job = None


class TimeBlockEditor(ctk.CTkToplevel):
    """一个时块的编辑窗. 非模态: 不 grab_set / 不 -topmost, 用户能最小化去干别的.
    保存前跑 validate_block, 失败红字不关窗. on_save(block_dict) 由调用方接。
    new_profile_cb(alias, on_ready) 由 App 注入 —— 处理"账号下拉里选＋新建"。"""

    _TIP_DURATION = ("一轮在刷怪区停留多少秒。也是『刷满』判定线 —— "
                     "一条命活过这个秒数才算这轮刷满。")
    _TIP_SHORT = ("连续这么多轮没撑到刷怪时长(被秒 / 到不了区), "
                  "且『自动换服务器』开着, 就自动跳服。")

    def __init__(self, master, *, block, others, profiles, on_save):
        super().__init__(master)
        self.title("时块")
        self.geometry("560x720")
        self.transient(master)
        self._block = dict(block)
        self._others = others
        self._profiles = list(profiles)
        self._on_save = on_save
        self.new_profile_cb = None
        self._point = tuple(block["location"]) if block.get("location") else None
        self._area = ([tuple(block["farming_area"][0]), tuple(block["farming_area"][1])]
                      if block.get("farming_area") else None)
        self._build()

    def _build(self):
        wk = ctk.CTkFrame(self, fg_color="transparent")
        wk.pack(anchor="w", padx=12, pady=(12, 4))
        self._day_vars = []
        for i, lab in enumerate(WEEKDAY_LABELS):
            v = ctk.IntVar(value=1 if i in self._block.get("days", []) else 0)
            ctk.CTkCheckBox(wk, text=lab, width=44, variable=v).pack(side="left", padx=2)
            self._day_vars.append(v)

        tr = ctk.CTkFrame(self, fg_color="transparent")
        tr.pack(anchor="w", padx=12, pady=4)
        self._start_e = ctk.CTkEntry(tr, width=70, placeholder_text="09:00")
        self._start_e.insert(0, self._block.get("start", ""))
        self._start_e.pack(side="left")
        ctk.CTkLabel(tr, text=" – ").pack(side="left")
        self._end_e = ctk.CTkEntry(tr, width=70, placeholder_text="12:00")
        self._end_e.insert(0, self._block.get("end", ""))
        self._end_e.pack(side="left")
        for e in (self._start_e, self._end_e):
            e.bind("<FocusOut>", lambda ev, w=e: self._normalize_entry(w), add="+")
        ctk.CTkLabel(tr, text="  (跨午夜: 起 > 止; 全天: 00:00–00:00)",
                     font=("", 9), text_color="gray").pack(side="left")

        vals = [p["alias"] for p in self._profiles] + ["＋ 新建…"]
        self._acct = ctk.CTkOptionMenu(self, values=vals, command=self._on_acct_pick)
        self._acct.set(self._block.get("profile", vals[0]))
        self._acct.pack(anchor="w", padx=12, pady=4)

        self._map = tk.StringVar(value=self._block.get("map", "desert"))
        map_row = ctk.CTkFrame(self, fg_color="transparent")
        map_row.pack(anchor="w", padx=12, pady=4)
        _MAP_LABELS = {"desert": "沙漠", "ocean": "海洋", "anthell": "蚁狱"}
        for m in app_config._VALID_MAPS:
            state = _map_radio_state(m)
            text = _MAP_LABELS.get(m, m)
            if state == "disabled":
                text += "(暂不可用)"
            ctk.CTkRadioButton(map_row, text=text, variable=self._map, value=m,
                               state=state, command=self._on_map_change).pack(
                side="left", padx=(0, 10))

        from gui_map_picker import MapPicker
        self._picker = MapPicker(self, on_point_change=self._on_point,
                                 on_area_change=self._on_area)
        self._picker.pack(fill="both", expand=True, padx=12, pady=4)
        self._picker.load_map(self._map.get())
        self._picker.set_point(self._point)
        self._picker.set_area(self._area)

        self._enemy = ctk.CTkSwitch(self, text="索敌 AI")
        if self._block.get("enemy_ai_enabled", True):
            self._enemy.select()
        self._enemy.pack(anchor="w", padx=12, pady=(6, 0))
        ctk.CTkLabel(self, text="仅沙漠", font=("", 9),
                     text_color="gray").pack(anchor="w", padx=12)
        self._autosw = ctk.CTkSwitch(self, text="自动换服务器",
                                     command=self._sync_short_enabled)
        if self._block.get("auto_switch_server", True):
            self._autosw.select()
        self._autosw.pack(anchor="w", padx=12, pady=6)

        self._adv_open = False
        self._adv_btn = ctk.CTkButton(self, text="▸ 高级选项", anchor="w",
                                      fg_color="transparent", command=self._toggle_adv)
        self._adv_btn.pack(anchor="w", padx=12)
        self._adv = ctk.CTkFrame(self, fg_color="transparent")
        dur_row = ctk.CTkFrame(self._adv, fg_color="transparent")
        dur_row.pack(anchor="w")
        ctk.CTkLabel(dur_row, text="刷怪时长(秒)").pack(side="left")
        q1 = ctk.CTkLabel(dur_row, text=" ? ", text_color="gray")
        q1.pack(side="left")
        _Tooltip(q1, self._TIP_DURATION)
        self._dur_e = ctk.CTkEntry(self._adv, width=90)
        self._dur_e.insert(0, str(self._block.get("farming_duration", 300)))
        self._dur_e.pack(anchor="w", pady=(0, 6))
        sh_row = ctk.CTkFrame(self._adv, fg_color="transparent")
        sh_row.pack(anchor="w")
        ctk.CTkLabel(sh_row, text="连续短局阈值").pack(side="left")
        q2 = ctk.CTkLabel(sh_row, text=" ? ", text_color="gray")
        q2.pack(side="left")
        _Tooltip(q2, self._TIP_SHORT)
        self._short_e = ctk.CTkEntry(self._adv, width=90)
        self._short_e.insert(0, str(self._block.get("consecutive_short_round_limit", 2)))
        self._short_e.pack(anchor="w")
        self._sync_short_enabled()

        self._err = ctk.CTkLabel(self, text="", text_color="#ff5555")
        self._err.pack(anchor="w", padx=12, pady=(6, 0))
        br = ctk.CTkFrame(self, fg_color="transparent")
        br.pack(anchor="e", padx=12, pady=10)
        ctk.CTkButton(br, text="取消", width=70, command=self.destroy).pack(side="left", padx=4)
        ctk.CTkButton(br, text="保存", width=70, command=self._save).pack(side="left")

    def _normalize_entry(self, entry):
        """失焦: 能规整就把输入框内容替换成规范 HH:MM; 规整不了就原样留着,
        交给保存时的 validate_block 红字. 纯 UI 便利, 不参与校验闭环(_collect 自己也规整)."""
        fixed = app_config.normalize_time(entry.get().strip())
        if fixed is not None and fixed != entry.get():
            entry.delete(0, "end")
            entry.insert(0, fixed)

    def _toggle_adv(self):
        self._adv_open = not self._adv_open
        self._adv_btn.configure(text=("▾ 高级选项" if self._adv_open else "▸ 高级选项"))
        if self._adv_open:
            self._adv.pack(anchor="w", padx=12)
        else:
            self._adv.pack_forget()

    def _sync_short_enabled(self):
        self._short_e.configure(state=("normal" if self._autosw.get() else "disabled"))

    def _on_acct_pick(self, val):
        if val != "＋ 新建…":
            return
        dlg = ctk.CTkInputDialog(text="新账号别名:", title="新建账号")
        alias = (dlg.get_input() or "").strip()
        if not alias:
            self._acct.set(self._profiles[0]["alias"])
            return
        if self.new_profile_cb is None:
            self._err.configure(text="新建账号未接线")
            self._acct.set(self._profiles[0]["alias"])
            return

        self.new_profile_cb(alias, self._add_profile_to_menu)

    def _add_profile_to_menu(self, new_alias):
        """新建账号 + 登录引导完成后回调: 把新别名加进账号下拉并选中.
        登录引导是非模态的, 用户可能在登录完成前把这个编辑器关掉了 —— 那样
        self / self._acct 的 tk 控件已销毁, configure 会 TclError. profile 本身
        已被 new_profile_cb 建好 + 存盘, 这里只是刷下拉, 编辑器没了就直接跳过."""
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._profiles.append({"alias": new_alias, "dir": f"chrome-profiles/{new_alias}"})
        self._acct.configure(values=[p["alias"] for p in self._profiles] + ["＋ 新建…"])
        self._acct.set(new_alias)

    def _on_map_change(self):
        # CTkRadioButton 的 command 不带值, 从 StringVar 自己读.
        self._point = None
        self._area = None
        self._picker.load_map(self._map.get())
        self._picker.set_point(None)
        self._picker.set_area(None)

    def _on_point(self, pt):
        self._point = pt

    def _on_area(self, area):
        self._area = [tuple(area[0]), tuple(area[1])]

    def _collect(self):
        from gui_app import resolve_point_and_area
        point, area = resolve_point_and_area(self._point, self._area)
        days = [i for i, v in enumerate(self._day_vars) if v.get()]
        start_raw = self._start_e.get().strip()
        end_raw = self._end_e.get().strip()
        blk = dict(self._block)
        blk.update(
            days=days,
            start=app_config.normalize_time(start_raw) or start_raw,
            end=app_config.normalize_time(end_raw) or end_raw,
            profile=self._acct.get(), map=self._map.get(),
            location=list(point) if point else None,
            farming_area=[list(area[0]), list(area[1])] if area else None,
            enemy_ai_enabled=bool(self._enemy.get()),
            auto_switch_server=bool(self._autosw.get()),
        )
        try:
            blk["farming_duration"] = int(self._dur_e.get())
        except ValueError:
            blk["farming_duration"] = 0
        try:
            blk["consecutive_short_round_limit"] = int(self._short_e.get())
        except ValueError:
            blk["consecutive_short_round_limit"] = 0
        return blk

    def _save(self):
        blk = self._collect()
        err = validate_block(blk, self._others)
        if err:
            self._err.configure(text=err)
            return
        self._on_save(blk)
        self.destroy()


class ScheduleList(ctk.CTkScrollableFrame):
    """时块折叠行列表 + ＋新增. get_cfg() 返回当前 cfg dict; save_cfg(cfg) 落盘并
    回读; open_editor(block_or_None) 由 App 提供(弹 TimeBlockEditor)。"""

    def __init__(self, master, *, get_cfg, save_cfg, open_editor):
        super().__init__(master)
        self._get_cfg = get_cfg
        self._save_cfg = save_cfg
        self._open_editor = open_editor
        self._readonly = False
        self.refresh()

    def set_readonly(self, ro):
        self._readonly = bool(ro)
        self.refresh()

    def refresh(self):
        for w in list(self.winfo_children()):
            w.destroy()
        st = "disabled" if self._readonly else "normal"
        for blk in self._get_cfg().get("schedule", []):
            row = ctk.CTkFrame(self)
            row.pack(fill="x", pady=3)
            ev = ctk.IntVar(value=1 if blk["enabled"] else 0)
            ctk.CTkCheckBox(row, text="", width=28, variable=ev, state=st,
                            command=lambda b=blk, v=ev: self._toggle(b, v)).pack(
                side="left", padx=4)
            ctk.CTkLabel(
                row, anchor="w",
                text=f"{weekday_short(blk['days'])}  {blk['start']}–{blk['end']}  "
                     f"{blk['profile']} · {blk['map']}").pack(
                side="left", fill="x", expand=True)
            ctk.CTkButton(row, text="✎", width=32, state=st,
                          command=lambda b=blk: self._open_editor(b)).pack(side="left", padx=2)
            ctk.CTkButton(row, text="🗑", width=32, state=st, fg_color="#8a2b2b",
                          command=lambda b=blk: self._delete(b)).pack(side="left", padx=2)
        ctk.CTkButton(self, text="＋ 新增时块", state=st,
                      command=lambda: self._open_editor(None)).pack(fill="x", pady=(8, 2))

    def _toggle(self, blk, var):
        cfg = self._get_cfg()
        for b in cfg["schedule"]:
            if b["id"] == blk["id"]:
                b["enabled"] = bool(var.get())
        self._save_cfg(cfg)

    def _delete(self, blk):
        from tkinter import messagebox
        if not messagebox.askyesno(
                "删除时块", f"删掉 {blk['start']}–{blk['end']} 这个时块?", parent=self):
            return
        cfg = self._get_cfg()
        cfg["schedule"] = [b for b in cfg["schedule"] if b["id"] != blk["id"]]
        self._save_cfg(cfg)
        self.refresh()
