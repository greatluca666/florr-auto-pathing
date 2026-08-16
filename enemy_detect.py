import math

import cv2
import numpy as np
import pyautogui
from ultralytics import YOLO

# florr.io稀有度色表(低到高). 来源: 一个公开的florr.io稀有度检测油猴脚本, 缺
# Eternal档(脚本没更新到那个档, 借用Super当占位) —— 跟这个项目其它靠实测校准出来
# 的颜色(比如玩家标记的f8de60)一样, 这张表投入实机使用前需要拿真实截图校准一遍,
# 别直接信.
RARITY_COLORS = {
    "Common": "7EEF6D",
    "Unusual": "FFE65D",
    "Rare": "4D52E3",
    "Epic": "861FDE",
    "Legendary": "DE1F1F",
    "Mythic": "1FDBDE",
    "Ultra": "FF2B75",
    "Super": "2BFFA3",
    "Eternal": "2BFFA3",  # 占位, 未校准 —— 借用Super的颜色, 见上面注释
    "Unique": "555555",
}

RARITY_ORDER = [
    "Common", "Unusual", "Rare", "Epic", "Legendary",
    "Mythic", "Ultra", "Super", "Eternal", "Unique",
]
RARITY_RANK = {name: i for i, name in enumerate(RARITY_ORDER)}


def _hex_to_bgr(hex_color):
    """'RRGGBB' → (B, G, R), 跟cv2的通道顺序一致. 复用utils.py里同样的
    (4, 2, 0)切片写法(那边给玩家标记色用的同一个手法)."""
    return tuple(int(hex_color[i:i + 2], 16) for i in (4, 2, 0))


def sample_rarity(image, bbox, tolerance=40):
    """在检测框上方采样一小块区域(florr.io怪物名牌悬浮在头顶), 按最近色距匹配
    RARITY_COLORS. 采样区域越界(空)或容差外没匹配上 → 默认Common —— 这是最
    宽松/正常接战的那一档, 颜色采样失败不会误触发规避行为."""
    x1, y1, x2, y2 = bbox
    cx = int((x1 + x2) / 2)
    tag_cy = max(0, int(y1) - 14)
    half_w, half_h = 20, 6
    y0, y1s = max(0, tag_cy - half_h), tag_cy + half_h
    x0, x1s = max(0, cx - half_w), cx + half_w
    region = image[y0:y1s, x0:x1s]
    if region.size == 0:
        return "Common"
    mean_bgr = region.reshape(-1, 3).mean(axis=0).tolist()

    best_name, best_dist = "Common", tolerance + 1
    for name in RARITY_ORDER:
        dist = math.dist(mean_bgr, _hex_to_bgr(RARITY_COLORS[name]))
        if dist < best_dist:
            best_name, best_dist = name, dist
    return best_name if best_dist <= tolerance else "Common"


# 数值越大优先级越高(故意跟RARITY_RANK同方向, 好用max()一起挑目标).
# sandstorm > cactus > beetle > scorpion > {sand_centipede, soldier_fire_ant}(并列最低)
SPECIES_RANK = {
    "sandstorm": 5,
    "cactus": 4,
    "beetle": 3,
    "scorpion": 2,
    "sand_centipede": 1,
    "soldier_fire_ant": 1,
}

_AVOID_PAIRS = {("scorpion", "Ultra"), ("beetle", "Ultra")}
_CAUTIOUS_PAIRS = {
    ("sandstorm", "Ultra"), ("cactus", "Ultra"),
    ("sand_centipede", "Ultra"), ("soldier_fire_ant", "Ultra"),
}


def classify_action(species, rarity):
    """按(物种, 稀有度)分档: ENGAGE(正常接战)/CAUTIOUS(可打但保持距离)/
    AVOID(不打, 触发规避). Mythic及以下全ENGAGE; Ultra档蝎子/甲虫AVOID,
    沙尘暴/仙人掌/沙蜈蚣/火蚁CAUTIOUS; 比Ultra还稀有(Super/Eternal/Unique, 实测
    这个刷怪区不会刷新这个档位)没规则覆盖时兜底AVOID —— 失败方向选"别惹", 不选
    "谨慎打": 比已经判AVOID的Ultra蝎子/甲虫还稀有的东西没理由更弱。"""
    if RARITY_RANK[rarity] < RARITY_RANK["Ultra"]:
        return "ENGAGE"
    if (species, rarity) in _AVOID_PAIRS:
        return "AVOID"
    if (species, rarity) in _CAUTIOUS_PAIRS:
        return "CAUTIOUS"
    return "AVOID"


def priority_score(species, rarity):
    """排序键, 数值越大优先级越高. 稀有度档位是第一比较项(碾压式), 物种优先级
    只在同稀有度档位时当平手规则."""
    return (RARITY_RANK[rarity], SPECIES_RANK[species])
