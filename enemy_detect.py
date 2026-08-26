import math

import cv2
import numpy as np
import pyautogui
from ultralytics import YOLO

import utils

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


MIN_RARITY_PIXEL_RATIO = 0.08  # 采样区域里至少8%像素匹配上, 才采信这个稀有度判定;
                                 # 低于这个占比大概率是背景色主导, 不是真名牌颜色.


def sample_rarity(image, bbox, tolerance=40, min_pixel_ratio=MIN_RARITY_PIXEL_RATIO):
    """在检测框上方采样一小块区域(florr.io怪物名牌悬浮在头顶), 按每种稀有度颜色
    数命中像素数、取数量最多的那档(不是取平均色再找最近邻 —— 名牌是带背景的
    局部色块, 平均色会被背景冲淡到不可用, 跟utils.py里get_player_location_on_map
    同款'数像素'手法, 不用'取均值'). 采样区域越界(空)、或最多那档命中像素占比
    低于min_pixel_ratio(大概率是背景主导, 不是真名牌) → 默认Common —— 这是最
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

    total_px = region.shape[0] * region.shape[1]
    best_name, best_count = "Common", 0
    for name in RARITY_ORDER:
        b, g, r = _hex_to_bgr(RARITY_COLORS[name])
        lower = np.array([max(0, b - tolerance), max(0, g - tolerance), max(0, r - tolerance)])
        upper = np.array([min(255, b + tolerance), min(255, g + tolerance), min(255, r + tolerance)])
        mask = cv2.inRange(region, lower, upper)
        count = int(np.count_nonzero(mask))
        if count > best_count:
            best_name, best_count = name, count

    if best_count / total_px < min_pixel_ratio:
        return "Common"
    return best_name


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


SCREEN_CENTER = (utils.SCREEN_WIDTH / 2, utils.SCREEN_HEIGHT / 2)  # 屏幕中心, 同时也是
                              # "停止移动"的鼠标位置约定(见utils.keyup()) ——
                              # aim_mouse_target/flee_mouse_target在"保持距离"/
                              # "没有明确方向"时都退回这个值, 调用方(main.py)靠跟这个
                              # 常量比较来判断"这tick是不是故意停住"。跟utils.py共用
                              # 同一份SCREEN_WIDTH/SCREEN_HEIGHT, 不再自己独立写死一份.


def aim_mouse_target(target_pos, hold_px=None, center=SCREEN_CENTER, max_extend=None):
    """把目标的屏幕坐标换算成鼠标该移到的位置 —— 纯屏幕坐标系计算, 跟
    move_to_position()那套小地图坐标系是两套独立空间, 不能互相传参数。
    hold_px设了值时, 一旦已经进到这个距离内就不再继续靠近(退回屏幕中心, 停止
    输出"继续接近"的方向), 给CAUTIOUS档的怪用; hold_px=None时无视距离上限一直
    往目标方向贴(只按max_extend限速度), 给ENGAGE档用。

    max_extend默认None时按1920x1080参照值500乘utils.mouse_scale()换算 —— 跟
    utils.keydown()的delta是同一种"1920x1080量出来的屏幕转向距离"，同样需要
    按分辨率缩放(最终回归审查发现的缺口: 这条追击/规避的转向路径之前漏掉了,
    utils.keydown()那条漫游路径缩放过了)。显式传值(比如测试里传500)会跳过这个
    默认换算, 直接用调用方给的值 —— 保持既有调用点(测试)的行为不变。"""
    if max_extend is None:
        max_extend = 500 * utils.mouse_scale()
    tx, ty = target_pos
    cx, cy = center
    dx, dy = tx - cx, ty - cy
    dist = math.hypot(dx, dy)
    if dist == 0:
        return center
    if hold_px is not None and dist <= hold_px:
        return center
    extend = min(dist, max_extend)
    return (cx + dx / dist * extend, cy + dy / dist * extend)


def flee_mouse_target(avoid_positions, center=SCREEN_CENTER, extend=None):
    """算所有AVOID怪的排斥力合向量, 换算成鼠标该移到的位置(往远离它们的方向)。
    合力互相抵消成约0向量(比如两个AVOID怪分别在玩家两侧)时没有明确逃离方向,
    退回屏幕中心 —— 等同于"停止移动", 跟utils.keyup()把鼠标收回中心停止移动是
    同一个约定。

    extend默认None时按1920x1080参照值400乘utils.mouse_scale()换算, 理由同
    aim_mouse_target的max_extend。"""
    if extend is None:
        extend = 400 * utils.mouse_scale()
    cx, cy = center
    fx, fy = 0.0, 0.0
    for px, py in avoid_positions:
        dx, dy = cx - px, cy - py
        dist = math.hypot(dx, dy)
        if dist == 0:
            continue
        fx += dx / dist
        fy += dy / dist
    mag = math.hypot(fx, fy)
    if mag < 0.05:
        return center
    return (cx + fx / mag * extend, cy + fy / mag * extend)


def chase_is_stalled(last_pos, current_pos, stall_count, progress_epsilon=1.5, stall_limit=15):
    """追击/规避途中判断是否卡住了(玩家位置连续没有实质进展, 跟move_to_position
    的卡住判定思路一致, 但这里没有'目标点'可比距离 —— 追的目标本身在动, 只能
    看玩家自己的位置有没有变化). 返回(更新后的stall_count, 是否该让步给一轮
    漫游). last_pos/current_pos都是minimap坐标系(get_player_position()的返回值,
    不是屏幕坐标) —— 这里比较的是'玩家挪没挪窝', 不是跟目标的屏幕坐标做减法,
    没有违反屏幕坐标系/小地图坐标系不能混用的规则."""
    if last_pos is None or current_pos is None:
        return 0, False
    dx = current_pos[0] - last_pos[0]
    dy = current_pos[1] - last_pos[1]
    moved = math.hypot(dx, dy)
    if moved < progress_epsilon:
        stall_count += 1
    else:
        stall_count = 0
    return stall_count, stall_count >= stall_limit


def select_action(detections, avoid_trigger_px=400, cautious_hold_px=250, center=SCREEN_CENTER):
    """每tick的索敌决策入口. detections是scan_enemies()给的检测列表(或测试里
    手搭的同结构字典列表). 返回三选一:
      ("flee", avoid_positions)   —— 触发半径内有AVOID怪, 优先规避
      ("chase", target, hold_px)  —— 没有近身危险, 但有可打目标(ENGAGE/CAUTIOUS)
      ("wander", None)            —— 啥有效目标都没有, 交回原来的随机漫游
    AVOID怪永远进不了"chase"候选池, 哪怕它稀有度算下来优先级最高。"""
    avoid_positions = []
    candidates = []
    for d in detections:
        bucket = classify_action(d["species"], d["rarity"])
        if bucket == "AVOID":
            avoid_positions.append(d["screen_pos"])
        else:
            candidates.append((d, bucket))

    if avoid_positions:
        cx, cy = center
        in_range = [
            p for p in avoid_positions
            if math.hypot(p[0] - cx, p[1] - cy) <= avoid_trigger_px
        ]
        if in_range:
            return ("flee", in_range)

    if candidates:
        best, best_bucket = max(
            candidates,
            key=lambda pair: priority_score(pair[0]["species"], pair[0]["rarity"]))
        hold_px = cautious_hold_px if best_bucket == "CAUTIOUS" else None
        return ("chase", best, hold_px)

    return ("wander", None)


_model = None


def load_enemy_model(path="models/desert.pt"):
    """加载一次desert.pt, 模块级单例缓存. 只走ultralytics.YOLO()的安全加载
    路径(底层是torch的weights_only安全反序列化), 不直接用不设限的
    torch.load(..., weights_only=False) —— 见
    docs/superpowers/specs/2026-08-16-sszone-enemy-detection-design.md的
    "模型来源"说明。"""
    global _model
    if _model is None:
        _model = YOLO(path)
    return _model


def scan_enemies(image=None, conf=0.4, model_path="models/desert.pt"):
    """跑一次YOLO检测, 返回屏幕坐标系(不是小地图坐标系!)下的检测列表.
    image=None时截一次全屏游戏画面; 传image是为了测试时能喂合成图片, 不用依赖
    真实截屏(pyautogui.screenshot()在没有真实显示器的环境里跑不了)。model_path
    转手传给load_enemy_model() —— 不在这里写死, 让调用方(main.py)的配置常量
    真正管用, 不是摆设。"""
    if image is None:
        screenshot = pyautogui.screenshot(region=[0, 0, utils.SCREEN_WIDTH, utils.SCREEN_HEIGHT])
        image = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

    model = load_enemy_model(model_path)
    results = model.predict(image, conf=conf, verbose=False)
    if not results:
        return []

    result = results[0]
    names = result.names
    detections = []
    for box in result.boxes:
        species = names[int(box.cls[0])]
        confidence = float(box.conf[0])
        x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
        bbox = (x1, y1, x2, y2)
        screen_pos = ((x1 + x2) / 2, (y1 + y2) / 2)
        detections.append({
            "species": species,
            "rarity": sample_rarity(image, bbox),
            "screen_pos": screen_pos,
            "bbox": bbox,
            "confidence": confidence,
        })
    return detections
