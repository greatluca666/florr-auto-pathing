"""时块调度 UI: 编辑器(CTkToplevel) + 列表折叠行 + 校验纯函数 + Tooltip.

纯函数(_safe_dirname / validate_block / block_to_active)不碰 tk, 单测直接调.
控件类(TimeBlockEditor / ScheduleList)才 import customtkinter —— 放在文件下半段.
"""
import re

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
