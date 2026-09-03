import math

import utils
import cdp_bridge
import canvas_decode


RARITY_ORDER = [
    "Common", "Unusual", "Rare", "Epic", "Legendary",
    "Mythic", "Ultra", "Super", "Eternal", "Unique",
]
RARITY_RANK = {name: i for i, name in enumerate(RARITY_ORDER)}


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


# ── Mythic 近身处理 ("先清青怪") ──────────────────────────────────────────
# 青怪 = Mythic 档 (青 = Mythic 名牌的青色). 沙漠的 6 种怪里, sandstorm 是刷怪
# 目标本身, 不进这套 —— 其余 5 种每种按下面的策略走位磨死.
MYTHIC_KITE_SPECIES = {
    "beetle": "strafe",           # 直冲型, 垂直环绕让它打空
    "soldier_fire_ant": "strafe",
    "scorpion": "ram",            # 直接撞
    "sand_centipede": "ram",
    "cactus": "hold",             # 站桩带刺, 保持距离在旁边
}

# 多个 Mythic 同时在场时先处理谁 (用户给的顺序, 只用于 Mythic 锁定, 跟 SPECIES_RANK
# 那套普通追击优先级无关).
MYTHIC_TARGET_RANK = {
    "beetle": 5,
    "soldier_fire_ant": 4,
    "scorpion": 3,
    "sand_centipede": 2,
    "cactus": 1,
}


def mythic_candidates(detections, chase_min_conf=None):
    """从 detections 里挑出够格进 Mythic 锁定池的: rarity 是 Mythic、species 在
    MYTHIC_KITE_SPECIES (sandstorm 排除)、置信度过 chase_min_conf (同追击的幻影框
    过滤). 返回列表, 可能为空."""
    if chase_min_conf is None:
        chase_min_conf = CHASE_MIN_CONF
    return [
        d for d in detections
        if d.get("rarity") == "Mythic"
        and d.get("species") in MYTHIC_KITE_SPECIES
        and d.get("confidence", 1.0) >= chase_min_conf
    ]


SCREEN_CENTER = (utils.SCREEN_WIDTH / 2, utils.SCREEN_HEIGHT / 2)  # 屏幕中心, 同时也是
                              # "停止移动"的鼠标位置约定(见utils.keyup()) ——
                              # aim_mouse_target/flee_mouse_target在"保持距离"/
                              # "没有明确方向"时都退回这个值, 调用方(main.py)靠跟这个
                              # 常量比较来判断"这tick是不是故意停住"。跟utils.py共用
                              # 同一份SCREEN_WIDTH/SCREEN_HEIGHT, 不再自己独立写死一份.


def pick_mythic_target(detections, center=SCREEN_CENTER, latched=False,
                       engage_px=450, release_px=600, chase_min_conf=None,
                       prev_pos=None):
    """挑这一 tick 要处理的那只 Mythic. 搜索半径: 已锁定用 release_px (放宽, 迟滞),
    没锁定用 engage_px. 半径内没有合格 Mythic → None.

    没锁定 / 没给 prev_pos: 按 (MYTHIC_TARGET_RANK, 离屏幕中心近) 取最高.
    已锁定且给了 prev_pos: 目标位置连续性优先 —— 取离 prev_pos 最近的候选, 只有当
    另一个候选 MYTHIC_TARGET_RANK 严格更高 (真来了更值得打的) 才切过去, 同 rank
    之间仍按离 prev_pos 近取. 免得两只同 rank Mythic 因亚像素抖动每 tick 翻 180°."""
    radius = release_px if latched else engage_px
    cx, cy = center

    def dist(d):
        px, py = d["screen_pos"]
        return math.hypot(px - cx, py - cy)

    in_range = [d for d in mythic_candidates(detections, chase_min_conf=chase_min_conf)
                if dist(d) <= radius]
    if not in_range:
        return None

    if latched and prev_pos is not None:
        ppx, ppy = prev_pos

        def dist_prev(d):
            px, py = d["screen_pos"]
            return math.hypot(px - ppx, py - ppy)

        nearest = min(in_range, key=dist_prev)
        best_rank = max(MYTHIC_TARGET_RANK[d["species"]] for d in in_range)
        if best_rank > MYTHIC_TARGET_RANK[nearest["species"]]:
            better = [d for d in in_range
                      if MYTHIC_TARGET_RANK[d["species"]] == best_rank]
            return min(better, key=dist_prev)
        return nearest

    return max(in_range, key=lambda d: (MYTHIC_TARGET_RANK[d["species"]], -dist(d)))


def mythic_move_target(target, center=SCREEN_CENTER, *, strafe_radius, cactus_hold_px,
                       max_extend=None, repel_positions=None, k_radial=0.8):
    """按 target 的物种策略算这一 tick 鼠标该移到哪:
      ram   (蝎子/蜈蚣)   —— 直接朝目标全速贴, 等同 aim_mouse_target(hold_px=None)
      hold  (仙人掌)       —— 远于 hold*1.15 逼近; 近于 hold*0.85 沿 -u 后撤;
                             中间沿垂直方向 perp 绕圈
      strafe(甲虫/火蚁)    —— 垂直环绕 perp + 朝 strafe_radius 的径向修正
                             (d>r 往里带, d<r 往外推), 归一化后 ×max_extend
    perp 取固定一侧 (-u_y, u_x). d==0 无方向 → 返回 center."""
    if max_extend is None:
        max_extend = 500 * utils.mouse_scale()
    policy = MYTHIC_KITE_SPECIES.get(target["species"], "ram")
    px, py = target["screen_pos"]
    cx, cy = center
    vx, vy = px - cx, py - cy
    d = math.hypot(vx, vy)
    if d == 0:
        return center
    ux, uy = vx / d, vy / d
    perp = (-uy, ux)

    if policy == "ram":
        return aim_mouse_target(target["screen_pos"], hold_px=None, center=center,
                                max_extend=max_extend, repel_positions=repel_positions)

    if policy == "hold":
        if d > cactus_hold_px * 1.15:
            return aim_mouse_target(target["screen_pos"], hold_px=None, center=center,
                                    max_extend=max_extend, repel_positions=repel_positions)
        if d < cactus_hold_px * 0.85:
            dx, dy = -ux, -uy            # 后撤
        else:
            dx, dy = perp               # 绕圈
        return (cx + dx * max_extend, cy + dy * max_extend)

    # policy == "strafe"
    radial = (d - strafe_radius) / strafe_radius * k_radial
    dx = perp[0] + ux * radial
    dy = perp[1] + uy * radial
    m = math.hypot(dx, dy)
    if m < 1e-6:
        return center
    return (cx + dx / m * max_extend, cy + dy / m * max_extend)


def aim_mouse_target(target_pos, hold_px=None, center=SCREEN_CENTER, max_extend=None,
                     repel_positions=None, repel_px=None, repel_gain=1.6):
    """把目标的屏幕坐标换算成鼠标该移到的位置 —— 纯屏幕坐标系计算, 跟
    move_to_position()那套小地图坐标系是两套独立空间, 不能互相传参数。
    hold_px设了值时, 一旦已经进到这个距离内就不再继续靠近(退回屏幕中心, 停止
    输出"继续接近"的方向), 给CAUTIOUS档的怪用; hold_px=None时无视距离上限一直
    往目标方向贴(只按max_extend限速度), 给ENGAGE档用。

    repel_positions给了值时(一串危险怪的屏幕坐标), 会往"远离它们"的方向叠一个
    排斥分量到追击方向上 —— 追归追, 但路径绕开半路的危险怪, 不是直直怼过去。
    只有危险怪进到repel_px以内才起作用, 越近推得越狠(线性衰减×repel_gain);
    repel_positions为空/None时行为跟以前完全一样。合成方向被排斥力抵消到约0 →
    这一tick退回屏幕中心(停一下), 等下一帧重新算。

    max_extend默认None时按1920x1080参照值500乘utils.mouse_scale()换算 —— 跟
    utils.keydown()的delta是同一种"1920x1080量出来的屏幕转向距离"，同样需要
    按分辨率缩放。repel_px默认None时同理按参照值450换算。显式传值(比如测试里传
    500)会跳过默认换算, 保持既有调用点行为不变。"""
    if max_extend is None:
        max_extend = 500 * utils.mouse_scale()
    tx, ty = target_pos
    cx, cy = center
    dx, dy = tx - cx, ty - cy
    dist = math.hypot(dx, dy)
    if dist == 0:
        return center

    rx, ry = 0.0, 0.0
    if repel_positions:
        if repel_px is None:
            repel_px = 450 * utils.mouse_scale()
        for px, py in repel_positions:
            adx, ady = cx - px, cy - py
            ad = math.hypot(adx, ady)
            if ad == 0 or ad >= repel_px:
                continue
            w = (1.0 - ad / repel_px) * repel_gain
            rx += adx / ad * w
            ry += ady / ad * w

    if hold_px is not None and dist <= hold_px:
        # 已经进到CAUTIOUS保持距离内: 平时就停(退回中心), 但半路有危险怪在推 →
        # 这一tick还是往远离危险的方向挪一下, 别傻站着被撞。
        if rx == 0.0 and ry == 0.0:
            return center
        rmag = math.hypot(rx, ry)
        step = min(max_extend, repel_px)
        return (cx + rx / rmag * step, cy + ry / rmag * step)

    ux, uy = dx / dist + rx, dy / dist + ry
    umag = math.hypot(ux, uy)
    if umag < 1e-6:
        return center
    extend = min(dist, max_extend)
    return (cx + ux / umag * extend, cy + uy / umag * extend)


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


CHASE_STALL_WINDOW = 25  # tick, ≈1.25s @ time.sleep(0.05); 判"追击途中卡住"看的时间窗


def chase_is_stalled(pos_history, min_progress=4.0, window=CHASE_STALL_WINDOW):
    """追击/规避途中判断是否真的卡住了 —— 看整段时间窗内玩家的**净位移**, 不是
    看相邻两tick挪了多少。追一个会走位的目标时, 相邻tick位移小是常态(绕圈、
    微调), 旧写法(相邻tick差<1.5就+1, 连续15次就脱困)会把正常追击误判成卡住、
    半路触发execute_anti_stuck()把玩家怼向目标。改成: 攒满一个window的位置样本
    后, 窗口首尾净位移 < min_progress(minimap坐标单位)才算卡住 —— 贴墙被顶住
    净位移≈0, 正常追击哪怕绕圈净位移也会累积过阈值。

    pos_history: 调用方维护的近期minimap坐标列表(get_player_position()的返回值,
    不是屏幕坐标), 最新的在末尾。样本不足一个window → 返回False(还没攒够, 不判)。
    只返回bool(该不该让步脱困), 不再回传计数 —— 状态在调用方那个列表里。"""
    if pos_history is None or len(pos_history) < window:
        return False
    x0, y0 = pos_history[-window]
    x1, y1 = pos_history[-1]
    return math.hypot(x1 - x0, y1 - y0) < min_progress


CHASE_MIN_CONF = 0.55  # 只有置信度到这个数的检测框才够格当"追击目标". 0.4~0.55
                        # 那档框经常是幻影(半透明沙尘暴边缘、影子), 拿它当目标就是
                        # 朝空气全速冲. 危险怪(AVOID/CAUTIOUS)不受此限 —— 宁可对着
                        # 一个可能不存在的强怪多绕一下, 不能漏躲。


def select_action(detections, avoid_trigger_px=400, cautious_hold_px=250,
                  center=SCREEN_CENTER, chase_min_conf=CHASE_MIN_CONF):
    """每tick的索敌决策入口. detections是scan_enemies()给的检测列表(或测试里
    手搭的同结构字典列表). 返回三选一:
      ("flee", avoid_positions)             —— 触发半径内有AVOID怪, 优先规避
      ("chase", target, hold_px, repel)     —— 有值得专门追的目标(稀有度>=Mythic,
                                               或Ultra档CAUTIOUS怪); repel是半路要
                                               绕开的危险怪坐标(AVOID全部 + 除目标外
                                               的CAUTIOUS), 传给aim_mouse_target当
                                               排斥源
      ("wander", None)                      —— 没有到Mythic档的目标, 交回随机漫游
    AVOID怪永远进不了"chase"候选池, 哪怕它稀有度算下来优先级最高。追击目标还要
    过chase_min_conf置信度关; 没过关的ENGAGE直接丢, 没过关的AVOID/CAUTIOUS仍算
    危险源(进flee判定/repel), 只是不当追击目标。

    ★ chase只留给Mythic及以上(和Ultra CAUTIOUS): 密集刷怪区(比如沙尘暴区)每tick
    都有Common/传奇沙尘暴当最高分候选, 早先版本每tick都返回chase去追它 —— 结果
    auto_farming永远走chase分支、从不进wander, 对着一个被打死/新刷/乱动的目标
    原地微振, chase_is_stalled误判"卡住"触发execute_anti_stuck乱跳, 整轮
    move_count=0一点没刷. Common..传奇这些交回wander撞怪 + 外部"一直攻击"就够了,
    不值得专门追。"""
    avoid_positions = []
    cautious_dets = []
    candidates = []
    for d in detections:
        bucket = classify_action(d["species"], d["rarity"])
        conf = d.get("confidence", 1.0)
        if bucket == "AVOID":
            avoid_positions.append(d["screen_pos"])
            continue
        if bucket == "CAUTIOUS":
            cautious_dets.append(d)
        if conf >= chase_min_conf:
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
        # max()按稀有度档优先, 所以best没到Mythic档 == 所有候选都没到. 没到就不追,
        # 交回wander(见docstring里的"密集刷怪区死循环"). Ultra CAUTIOUS(rank 6)恒
        # >= Mythic, 保留"对Ultra沙尘暴/仙人掌保持距离接战"这条.
        if RARITY_RANK[best["rarity"]] >= RARITY_RANK["Mythic"]:
            hold_px = cautious_hold_px if best_bucket == "CAUTIOUS" else None
            # 半路危险源: 所有AVOID怪(不管在不在flee触发半径内 —— 402px的Ultra蝎子
            # 不该触发flee, 但追别的怪时也不能直直穿过它) + 除目标外的CAUTIOUS怪。
            repel = list(avoid_positions)
            repel += [d["screen_pos"] for d in cautious_dets if d is not best]
            return ("chase", best, hold_px, repel)

    return ("wander", None)


_DESERT_SPECIES = {"scorpion", "beetle", "cactus", "sandstorm", "sand_centipede", "soldier_fire_ant"}
_SPECIES_ALIASES = {
    # 中文客户端: canvas 解出的名字是中文, slug = 原字符串(lower/空格替换对中文是 no-op)。
    # value 必须落在 SPECIES_RANK 那 6 个 slug 里, 否则 priority_score 会 KeyError。
    "沙尘暴": "sandstorm",
    "仙人掌": "cactus",
    "甲虫": "beetle",
    "蝎子": "scorpion",
    "蜈蚣": "sand_centipede",
    "火兵蚁": "soldier_fire_ant",
    "火蚁": "soldier_fire_ant",       # 工蚁; 本项目不分工/兵, 都归 soldier_fire_ant
    "火蚁穴": "soldier_fire_ant",     # Fire Ant Hole: 不动的出怪口, 没有独立 slug —— 折到
                                     # 最接近的火蚁威胁; Mythic 档会被当 strafe 目标, 可接受
    "瓢虫": "sand_centipede",         # 非沙漠怪(花园的), 无害低价值 —— 折到最低 rank slug
                                     # 当低优先接战目标, 免得每帧刷"未识别"日志
}

_seen_unknown_names = set()   # slugs already reported by _species_from_name — recovers the
                              # diagnostic the deleted debug_enemy_detect.py used to give:
                              # the 6 desert slugs are hardcoded (originally from the old YOLO
                              # class labels), never checked against what florr's English client
                              # actually renders. If a real
                              # desert mob shows under an unexpected name it silently vanishes
                              # from every detection — this at least names it once in the log.

# 名牌稀有度词的颜色 -> RARITY_ORDER 下标. 跟旧稀有度色表同一批值, 只是换成从
# canvas 绘制调用读到的、带 "#" 的 fill 色. Super(rank 7)/Eternal(rank 8) 实测不刷,
# rank 8 空着.
_RANK_BY_RARITY_COLOR = {
    "#7EEF6D": 0, "#FFE65D": 1, "#4D52E3": 2, "#861FDE": 3, "#DE1F1F": 4,
    "#1FDBDE": 5, "#FF2B75": 6, "#2BFFA3": 7, "#555555": 9,
}


def _species_from_name(name):
    """florr 客户端英文名 -> desert 物种 slug. 六种沙漠怪之外(路过的玩家 / 别的
    生态的怪)-> None(跳过). 客户端语言必须是 English."""
    if not name:
        return None
    slug = name.strip().lower().replace(" ", "_")
    if slug in _DESERT_SPECIES:
        return slug
    if slug in _SPECIES_ALIASES:
        return _SPECIES_ALIASES[slug]
    if slug and slug not in _seen_unknown_names:
        _seen_unknown_names.add(slug)
        print(f"ℹ️ canvas 解出未识别怪物名: {name!r} (slug={slug!r}) —— 若是沙漠怪, 加进 _SPECIES_ALIASES")
    return None


def _tier_from_color(rarity_color):
    """名牌稀有度词的颜色 -> RARITY_ORDER 里的档名. 认不出 / None -> Common
    (跟旧稀有度采样读失败时同款兜底, 不会误触发规避)."""
    rank = _RANK_BY_RARITY_COLOR.get(rarity_color)
    return RARITY_ORDER[rank] if rank is not None else "Common"


_frame_buffer = []   # drain_canvas_log 每次读空页面 log, 跨调用在这里攒; 每次裁到最新一帧
_FRAME_BUFFER_CAP = 20000   # 硬上限, 防 _frame_buffer 无界增长: 若 __canvasFrame 卡住不动
                            # (florr 绑死了自己那份 requestAnimationFrame 引用), 每条记录都是
                            # frame 0 → group_by_frame 永远只有 1 个 key → scan_enemies 每 tick
                            # 命中 "< 2" 提前返回, 下面那句按帧裁剪永远跑不到, buffer 每 tick
                            # 涨一截. canvas_hook.js 自己把页面 log 裁到 FRAME_RETENTION=5 帧,
                            # 20000 条已经很宽裕.


def scan_enemies(image=None, conf=0.4, model_path=None):
    """解码最新一帧完整的 canvas 绘制记录, 返回检测字典列表(跟旧 YOLO 版同结构:
    species / rarity / screen_pos / bbox / confidence). image/conf/model_path 保留
    只为兼容旧调用点, 不再用 -- 识别已经从"截图跑 YOLO"换成"解码 canvas 绘制调用".
    帧解不出(画面里没怪 -> camera_from_frame 抛) -> [](跟旧模型没框一个意思).

    每 tick 都调 inject_canvas_hook()(幂等) -- florr 重载后下一次扫描自动重注 hook.
    inject/drain/decode 整段套 try: inject_canvas_hook 版本不符会抛 RuntimeError,
    _send_cdp_command 找不到标签页也抛, canvas_decode 的除法可能抛
    ZeroDivisionError/IndexError, cdp_bridge 底下 websocket 可能抛 WebSocketException
    /OSError —— 全都当"这次没检测到"退化成 wander."""
    try:
        cdp_bridge.inject_canvas_hook()
        _frame_buffer.extend(cdp_bridge.drain_canvas_log())
        if len(_frame_buffer) > _FRAME_BUFFER_CAP:
            del _frame_buffer[:-_FRAME_BUFFER_CAP]   # 硬上限, 见 _FRAME_BUFFER_CAP 注释
        frames = canvas_decode.group_by_frame(_frame_buffer)
        if len(frames) < 2:
            return []
        keys = sorted(frames)
        recs = frames[keys[-2]]                 # 最新那帧可能还在画, 取次新的
        _frame_buffer[:] = [r for r in _frame_buffer if r.get("frame", -1) >= keys[-1]]
        cam = canvas_decode.camera_from_frame(recs)
        mobs = canvas_decode.mobs_from_frame(recs, cam)
    except Exception:
        return []                              # 任何异常 → 当作这次没解出来, 返回 []

    out = []
    for m in mobs:
        sp = _species_from_name(m.get("name"))
        if sp is None:
            continue
        sx, sy = m["sx"], m["sy"]
        out.append({
            "species": sp,
            "rarity": _tier_from_color(m.get("rarity_color")),
            "screen_pos": (sx, sy),
            "bbox": (sx - 1, sy - 1, sx + 1, sy + 1),
            "confidence": 1.0,
        })

    # 贴脸神话沙尘暴("青怪"): 你站在它身上时 florr 不画它的名字/稀有度牌, 血条锚点又
    # 跟你自己重合 → mobs_from_frame 当玩家滤掉了. 靠"锚点=玩家 + 有 #42E3F5 护盾副
    # 血条 + 没名字牌"这组特征把它认回来(5 次实机诊断全中), 当成 Mythic sandstorm.
    # 物种猜 sandstorm(用户的青怪主要就是它); 猜错也只是走 chase 分支, 对着屏幕中心
    # 打, 不会乱跑. 已经解出一只 Mythic+ sandstorm 就不重复加.
    try:
        pb = canvas_decode.point_blank_shielded_mob(recs, cam)
    except Exception:
        pb = None
    if pb is not None and not any(
            d["species"] == "sandstorm" and RARITY_RANK[d["rarity"]] >= RARITY_RANK["Mythic"]
            for d in out):
        cx, cy = pb["sx"], pb["sy"]
        out.append({
            "species": "sandstorm",
            "rarity": "Mythic",
            "screen_pos": (cx, cy),
            "bbox": (cx - 1, cy - 1, cx + 1, cy + 1),
            "confidence": 1.0,
        })
    return out
