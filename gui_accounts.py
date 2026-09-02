"""账号(Chrome profile)管理: 纯数据操作 + 账号页控件.

纯函数对 cfg dict 操作, 返回 (cfg, err|None), 不写盘 —— 调用方负责 save_config
+ os.makedirs / os.rename 那些副作用. 控件类 AccountsPage 在文件下半段.
"""
from gui_schedule import _safe_dirname


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
