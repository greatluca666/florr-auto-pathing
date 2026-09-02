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


def _coerce(raw):
    """拿 DEFAULTS 当底, 逐键把 raw 里合法的值覆盖上去; 不合法的键留默认 + 警告.
    未知键直接丢掉(worker 只认 DEFAULTS 里那几个键)."""
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


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)
    except Exception as e:
        print(f"⚠️ 读 config.json 失败, 全部用默认值: {e}")
        return copy.deepcopy(DEFAULTS)
    return _coerce(raw)


def save_config(cfg):
    """写之前先 _coerce, GUI 传进来的东西也不例外 —— 别让界面 bug 写出一份坏配置."""
    cleaned = _coerce(cfg)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2, ensure_ascii=False)


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
