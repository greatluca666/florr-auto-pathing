"""GUI 与 worker 共用的配置读写. main.py 以前把这些值硬编码在 __main__ 里,
现在集中到 exe 同级的 config.json —— GUI 写, worker(`main.py --worker`)读.

设计要点跟 afk_watch._write_afk_config() 一致: 读不出来/键坏了不抛, 回落到
DEFAULTS(= 以前硬编码那套) + print 一句警告. 这里只读不写用户那份坏文件,
所以不需要像 afk_watch 那样备份 .bak.
"""
import copy
import json
import os
import re
import sys

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.argv[0])), "config.json"
)

# 每个值 = 改造前 main.py __main__ 里的硬编码值.
DEFAULTS = {
    "map": "desert",
    "location": [22, 32],
    "farming_area": [[9, 8], [51, 56]],
    "farming_duration": 300,
    "consecutive_short_round_limit": 2,
    # 默认关: 索敌要 models/desert.pt, 那个权重文件没进仓库也没进发布包. 默认开
    # 的话全新安装一跑就是每 ENEMY_SCAN_INTERVAL(0.3s) 一条"索敌出错"刷屏.
    # README / PACKAGING 里写的也是"默认关闭, 要自己放权重", 这里跟文档对齐.
    "enemy_ai_enabled": False,
    "auto_switch_server": True,
    "afk_enabled": False,
}

# maps/ 下的 3 个 png(去扩展名). 新增地图要同步这里 —— 跟 utils.check_map_border()
# 那种"就地写死一小张表"的仓库既有做法一致, 不在 import 时去 listdir.
_VALID_MAPS = ("desert", "ocean", "anthell")

# 一个时块 / active 切片里的刷怪参数键(不含 afk_enabled —— 那是 GUI 全局的).
_ACTIVE_KEYS = (
    "map", "location", "farming_area", "farming_duration",
    "consecutive_short_round_limit", "enemy_ai_enabled", "auto_switch_server",
)
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _is_int_pair(v):
    return (
        isinstance(v, (list, tuple))
        and len(v) == 2
        and all(isinstance(n, int) and not isinstance(n, bool) for n in v)
    )


def _coerce_v1(raw):
    """阶段1 的扁平校验: 拿 DEFAULTS 当底, 逐键把 raw 里合法的值覆盖上去; 不合法
    的键留默认 + 警告. 未知键直接丢掉. 现在只用于迁移旧文件 + 规整 active 切片."""
    cfg = copy.deepcopy(DEFAULTS)
    if not isinstance(raw, dict):
        print(f"⚠️ config.json 顶层不是对象(是 {type(raw).__name__}), 全部用默认值")
        return cfg

    for key, default in DEFAULTS.items():
        if key not in raw:
            continue
        val = raw[key]
        ok = False
        if key == "map":
            ok = isinstance(val, str) and val in _VALID_MAPS
        elif key == "location":
            ok = _is_int_pair(val)
            if ok:
                val = [int(val[0]), int(val[1])]
        elif key == "farming_area":
            ok = (
                isinstance(val, (list, tuple))
                and len(val) == 2
                and all(_is_int_pair(corner) for corner in val)
            )
            if ok:
                val = [[int(c[0]), int(c[1])] for c in val]
        elif key == "farming_duration":
            ok = isinstance(val, int) and not isinstance(val, bool) and val > 0
        elif key == "consecutive_short_round_limit":
            ok = isinstance(val, int) and not isinstance(val, bool) and val >= 1
        else:  # enemy_ai_enabled / auto_switch_server / afk_enabled
            ok = isinstance(val, bool)

        if ok:
            cfg[key] = val
        else:
            print(f"⚠️ config.json 的 {key} 值不合法({val!r}), 用默认 {default!r}")

    return cfg


# ─────────────────────────── v2 schema ───────────────────────────
# 顶层: version / afk_enabled / profiles / schedule / active.
# 阶段2 新增: 按星期几的时块调度 + 独立 Chrome profile.

DEFAULTS_V2 = {
    "version": 2,
    "afk_enabled": False,
    "profiles": [{"alias": "默认", "dir": "chrome-profiles/默认"}],
    # 全新装是空的 —— 用户自己加时块. 空 schedule 时 worker 不会真跑.
    "schedule": [],
    # 占位: 只在"全新装 + 空 schedule"时当 active 兜底. 索敌默认值沿用 DEFAULTS
    # 里的 False —— 迁移不该强翻用户没开的功能; "默认开索敌"体现在 GUI 新建时块
    # 的默认值上(gui_schedule._new_block_template / 编辑器), 不在这里.
    "active": {k: copy.deepcopy(DEFAULTS[k]) for k in _ACTIVE_KEYS},
}


def _coerce_profiles(v):
    out, seen = [], set()
    if isinstance(v, list):
        for item in v:
            if not isinstance(item, dict):
                continue
            alias = item.get("alias")
            if not (isinstance(alias, str) and alias.strip()):
                continue
            d = item.get("dir")
            if not (isinstance(d, str) and d):
                d = f"chrome-profiles/{alias}"
            if alias in seen:
                print(f"⚠️ config.json 重复的账号别名 {alias!r}, 丢弃后一个")
                continue
            seen.add(alias)
            out.append({"alias": alias, "dir": d})
    if not out:
        out = [{"alias": "默认", "dir": "chrome-profiles/默认"}]
    return out


def _coerce_block(raw, aliases, n):
    """一个时块: 全合法才返回规整后的 dict, 否则 None(调用方整块丢弃 + 警告)."""
    if not isinstance(raw, dict):
        return None
    bid = raw["id"] if isinstance(raw.get("id"), str) and raw["id"] else f"blk-{n}"
    days = raw.get("days")
    if not (isinstance(days, list) and days and all(
            isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in days)):
        return None
    days = sorted(set(days))
    start, end = raw.get("start"), raw.get("end")
    if not (_valid_time(start) and _valid_time(end)):
        return None
    if start == end and start != "00:00":
        return None
    profile = raw.get("profile")
    if not isinstance(profile, str):
        return None
    if raw.get("map") not in _VALID_MAPS:
        return None
    loc = raw.get("location")
    if not _is_int_pair(loc):
        return None
    area = raw.get("farming_area")
    if not (isinstance(area, (list, tuple)) and len(area) == 2
            and all(_is_int_pair(c) for c in area)):
        return None
    dur = raw.get("farming_duration")
    if not (isinstance(dur, int) and not isinstance(dur, bool) and dur > 0):
        return None
    lim = raw.get("consecutive_short_round_limit")
    if not (isinstance(lim, int) and not isinstance(lim, bool) and lim >= 1):
        return None
    eai, asw = raw.get("enemy_ai_enabled"), raw.get("auto_switch_server")
    if not isinstance(eai, bool) or not isinstance(asw, bool):
        return None
    enabled = raw.get("enabled")
    enabled = True if not isinstance(enabled, bool) else enabled
    if profile not in aliases:
        print(f"⚠️ config.json 时块 {bid} 引用的账号 {profile!r} 不存在, 已禁用该时块")
        enabled = False
    return {
        "id": bid, "enabled": enabled, "days": days, "start": start, "end": end,
        "profile": profile, "map": raw["map"],
        "location": [int(loc[0]), int(loc[1])],
        "farming_area": [[int(area[0][0]), int(area[0][1])],
                         [int(area[1][0]), int(area[1][1])]],
        "farming_duration": dur, "consecutive_short_round_limit": lim,
        "enemy_ai_enabled": eai, "auto_switch_server": asw,
    }


def _coerce_schedule(v, aliases):
    if not isinstance(v, list):
        return []
    out = []
    for i, raw_blk in enumerate(v, 1):
        blk = _coerce_block(raw_blk, aliases, i)
        if blk is None:
            print(f"⚠️ config.json 第 {i} 个时块不合法, 整块丢弃")
        else:
            out.append(blk)
    for a in range(len(out)):
        for b in range(a + 1, len(out)):
            if out[a]["enabled"] and out[b]["enabled"] and blocks_overlap(out[a], out[b]):
                print(f"⚠️ config.json 时块 {out[a]['id']} 与 {out[b]['id']} 时间重叠")
    return out


def _coerce_active(v, schedule):
    if isinstance(v, dict):
        got = _coerce_v1(v)
        return {k: got[k] for k in _ACTIVE_KEYS}
    if schedule:
        return {k: copy.deepcopy(schedule[0][k]) for k in _ACTIVE_KEYS}
    return {k: copy.deepcopy(DEFAULTS[k]) for k in _ACTIVE_KEYS}


def _coerce(raw):
    """v2 顶层校验. 坏时块整块丢; profile 悬空 → 该块禁用(不丢); profiles 空 → 补默认."""
    if not isinstance(raw, dict):
        print(f"⚠️ config.json 顶层不是对象(是 {type(raw).__name__}), 全部用默认值")
        return copy.deepcopy(DEFAULTS_V2)
    cfg = {"version": 2}
    cfg["afk_enabled"] = raw["afk_enabled"] if isinstance(raw.get("afk_enabled"), bool) else False
    cfg["profiles"] = _coerce_profiles(raw.get("profiles"))
    aliases = {p["alias"] for p in cfg["profiles"]}
    cfg["schedule"] = _coerce_schedule(raw.get("schedule"), aliases)
    cfg["active"] = _coerce_active(raw.get("active"), cfg["schedule"])
    return cfg


def _rename_legacy_profile_dir():
    """阶段1 的 chrome-profile/ → 阶段2 的 chrome-profiles/默认/. best-effort:
    目标已存在 / 改名失败(目录被占用)都只 print 一句, 不抛 —— 用户下次用
    『默认』账号时会走登录引导补上。"""
    root = os.path.dirname(os.path.abspath(sys.argv[0]))
    old = os.path.join(root, "chrome-profile")
    new = os.path.join(root, "chrome-profiles", "默认")
    if not os.path.isdir(old) or os.path.exists(new):
        return
    try:
        os.makedirs(os.path.join(root, "chrome-profiles"), exist_ok=True)
        os.rename(old, new)
    except OSError as e:
        print(f"⚠️ 旧 Chrome profile 目录改名失败({e}); 用『默认』账号时会要求重新登录")


def migrate_v1(raw):
    """v1 扁平配置 → v2: 单『默认』profile + 一个全周全天时块 + active 切片。"""
    flat = _coerce_v1(raw if isinstance(raw, dict) else {})
    _rename_legacy_profile_dir()
    block = {
        "id": "blk-1", "enabled": True, "days": [0, 1, 2, 3, 4, 5, 6],
        "start": "00:00", "end": "00:00", "profile": "默认",
    }
    for k in _ACTIVE_KEYS:
        block[k] = copy.deepcopy(flat[k])
    return {
        "version": 2,
        "afk_enabled": flat["afk_enabled"],
        "profiles": [{"alias": "默认", "dir": "chrome-profiles/默认"}],
        "schedule": [block],
        "active": {k: copy.deepcopy(flat[k]) for k in _ACTIVE_KEYS},
    }


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS_V2)
    except Exception as e:
        print(f"⚠️ 读 config.json 失败, 全部用默认值: {e}")
        return copy.deepcopy(DEFAULTS_V2)
    if not isinstance(raw, dict):
        print(f"⚠️ config.json 顶层不是对象(是 {type(raw).__name__}), 全部用默认值")
        return copy.deepcopy(DEFAULTS_V2)
    if raw.get("version") != 2:
        cfg = _coerce(migrate_v1(raw))
        try:
            _write(cfg)
        except OSError as e:
            print(f"⚠️ 迁移后写回 config.json 失败: {e}")
        return cfg
    return _coerce(raw)


def _write(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def save_config(cfg):
    """写之前先 _coerce, GUI 传进来的东西也不例外 —— 别让界面 bug 写出一份坏配置."""
    _write(_coerce(cfg))


# ─────────────────────────── 调度时间数学(纯函数) ───────────────────────────
# 星期编号 0=周一 … 6=周日. 时间 "HH:MM" 24h. 区间半开 [start, end).

def _hhmm_to_min(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _valid_time(s):
    return isinstance(s, str) and _TIME_RE.match(s) is not None


def expand_block_days(block):
    """把一个时块摊平成 [(weekday, start_min, end_min)]. 半开区间 [start, end).
    00:00–00:00 = 全天; start >= end(且非全天)= 跨午夜, 拆成当天尾段 + 次日头段."""
    s = _hhmm_to_min(block["start"])
    e = _hhmm_to_min(block["end"])
    out = []
    for d in block["days"]:
        if s == 0 and e == 0:
            out.append((d, 0, 1440))
        elif s < e:
            out.append((d, s, e))
        else:
            out.append((d, s, 1440))
            if e > 0:
                out.append(((d + 1) % 7, 0, e))
    return out


def blocks_overlap(a, b):
    for (da, sa, ea) in expand_block_days(a):
        for (db, sb, eb) in expand_block_days(b):
            if da == db and sa < eb and sb < ea:
                return True
    return False


def active_block(schedule, weekday, hhmm):
    m = _hhmm_to_min(hhmm)
    for blk in schedule:
        if not blk.get("enabled"):
            continue
        for (d, s, e) in expand_block_days(blk):
            if d == weekday and s <= m < e:
                return blk
    return None


def next_start(schedule, weekday, hhmm):
    """从此刻(weekday, hhmm)起, 一周内最近的一个时块起点. 返回 (weekday, 'HH:MM')."""
    now = weekday * 1440 + _hhmm_to_min(hhmm)
    best = None
    for blk in schedule:
        if not blk.get("enabled"):
            continue
        for (d, s, _e) in expand_block_days(blk):
            start_abs = d * 1440 + s
            delta = (start_abs - now) % (7 * 1440)
            if delta == 0:
                continue
            if best is None or delta < best[0]:
                best = (delta, d, "%02d:%02d" % divmod(s, 60))
    return None if best is None else (best[1], best[2])
