"""账号(Chrome profile)管理: 纯数据操作 + 账号页控件.

纯函数对 cfg dict 操作, 返回 (cfg, err|None), 不写盘 —— 调用方负责 save_config
+ os.makedirs / os.rename 那些副作用. 控件类 AccountsPage 在文件下半段.
"""
import os
import sys
from tkinter import messagebox

import customtkinter as ctk

from gui_schedule import _safe_dirname


def abs_profile_path(rel_dir):
    """config 里 profile 的 dir 是相对 exe 同级的 (chrome-profiles/<别名>).
    绝对路径按 sys.argv[0] 定位, 跟 app_config.CONFIG_PATH 一套语义."""
    if os.path.isabs(rel_dir):
        return rel_dir
    root = os.path.dirname(os.path.abspath(sys.argv[0]))
    return os.path.join(root, rel_dir)


def profile_dir(cfg, alias):
    for p in cfg.get("profiles", []):
        if p["alias"] == alias:
            return p["dir"]
    return None


def _aliases(cfg):
    return [p["alias"] for p in cfg.get("profiles", [])]


def add_profile(cfg, alias):
    alias = (alias or "").strip()
    if not alias:
        return cfg, "账号名不能为空"
    if not _safe_dirname(alias):
        return cfg, "账号名里没有可用作目录名的字符"
    if alias in _aliases(cfg):
        return cfg, f"账号名『{alias}』已存在"
    cfg["profiles"].append({"alias": alias, "dir": f"chrome-profiles/{alias}"})
    return cfg, None


def rename_profile(cfg, old, new):
    new = (new or "").strip()
    if not new:
        return cfg, "新名字不能为空"
    if not _safe_dirname(new):
        return cfg, "新名字里没有可用作目录名的字符"
    if new == old:
        return cfg, None
    if new in _aliases(cfg):
        return cfg, f"账号名『{new}』已存在"
    for p in cfg["profiles"]:
        if p["alias"] == old:
            p["alias"] = new
            p["dir"] = f"chrome-profiles/{new}"
            break
    else:
        return cfg, f"没有账号『{old}』"
    for b in cfg.get("schedule", []):
        if b.get("profile") == old:
            b["profile"] = new
    return cfg, None


def delete_profile(cfg, alias):
    used = [b.get("id") for b in cfg.get("schedule", []) if b.get("profile") == alias]
    if used:
        return cfg, f"时块 {', '.join(used)} 还在用『{alias}』, 先改掉那些时块的账号"
    cfg["profiles"] = [p for p in cfg["profiles"] if p["alias"] != alias]
    return cfg, None


# ─────────────────────────────── 控件层 ───────────────────────────────

class AccountsPage(ctk.CTkFrame):
    """profile 列表: 新建 / 登录 / 改名 / 删除. 调度运行中整页只读.
    new_profile_cb(alias, on_ready) 由 App 注入(建目录 + save + 登录引导)。"""

    def __init__(self, master, *, get_cfg, save_cfg, login_guide):
        super().__init__(master)
        self._get_cfg = get_cfg
        self._save_cfg = save_cfg
        self._login = login_guide
        self.new_profile_cb = None
        self._readonly = False
        self.refresh()

    def set_readonly(self, ro):
        self._readonly = bool(ro)
        self.refresh()

    def refresh(self):
        for w in list(self.winfo_children()):
            w.destroy()
        st = "disabled" if self._readonly else "normal"
        for p in self._get_cfg().get("profiles", []):
            row = ctk.CTkFrame(self)
            row.pack(fill="x", pady=3, padx=4)
            ctk.CTkLabel(row, text=p["alias"], width=90, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=p["dir"], text_color="gray", anchor="w").pack(
                side="left", fill="x", expand=True)
            for txt, fn in (("登录", self._login_profile), ("改名", self._rename),
                            ("删除", self._delete)):
                ctk.CTkButton(row, text=txt, width=52, state=st,
                              command=lambda f=fn, a=p["alias"]: f(a)).pack(
                    side="left", padx=2)
        ctk.CTkButton(self, text="＋ 新建账号", state=st, command=self._new).pack(
            fill="x", pady=(8, 2), padx=4)

    def _login_profile(self, alias):
        d = profile_dir(self._get_cfg(), alias)
        if d is None:
            return
        self._login.start(abs_profile_path(d),
                          on_done=self.refresh, on_cancel=lambda: None)

    def _rename(self, alias):
        dlg = ctk.CTkInputDialog(text=f"把『{alias}』改成:", title="改名")
        new = (dlg.get_input() or "").strip()
        if not new:
            return
        cfg = self._get_cfg()
        old_rel = profile_dir(cfg, alias)
        cfg, err = rename_profile(cfg, alias, new)
        if err:
            messagebox.showwarning("账号", err)
            return
        try:
            old_abs = abs_profile_path(old_rel) if old_rel else None
            if old_abs and os.path.isdir(old_abs):
                os.rename(old_abs, os.path.join(os.path.dirname(old_abs), new))
        except OSError:
            messagebox.showwarning(
                "账号", "配置已改, 但目录没改成(该账号的 Chrome 可能还开着)")
        self._save_cfg(cfg)
        self.refresh()

    def _delete(self, alias):
        cfg, err = delete_profile(self._get_cfg(), alias)
        if err:
            messagebox.showwarning("账号", err)
            return
        self._save_cfg(cfg)
        self.refresh()

    def _new(self):
        dlg = ctk.CTkInputDialog(text="新账号别名:", title="新建账号")
        alias = (dlg.get_input() or "").strip()
        if not alias:
            return
        if self.new_profile_cb:
            self.new_profile_cb(
                alias, lambda *_a: self.winfo_exists() and self.refresh())
