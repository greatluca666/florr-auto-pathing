from utils import *
from overlay import create_overlay
import argparse
import signal
import sys
import threading
import cdp_bridge
import time
import random
import afk_watch
import enemy_detect
import app_config
import florr_settings
import server_lookup
import loadout_swap

# ===== 索敌配置 (sszone敌怪检测/追击/规避) =====
ENEMY_SCAN_INTERVAL = 0.12  # 秒, 索敌扫描节流间隔. 这是"决策新鲜度"的主旋钮:
                              # 追击/规避途中每tick都拿这份决策里的怪坐标去moveTo,
                              # 间隔越大, 中间那几tick就越是照着旧坐标全速走 —— 怪
                              # 早挪窝了, 人一头撞上去. 实测一次推理≈0.1s(Mac MPS;
                              # Windows CUDA更快), 设0.12基本每帧都能重扫. 推理太慢
                              # 的机器上循环会被推理本身卡住, 那也没办法, 至少不比
                              # 大间隔更差. 漫游时每腿路另受move_to_position的
                              # max_attempts限制(见下方wander分支).
AVOID_TRIGGER_PX = 400      # 屏幕像素半径, AVOID怪进入此半径触发逃离
CAUTIOUS_HOLD_PX = 250      # 屏幕像素, CAUTIOUS怪保持的最小距离(不继续贴近)
CHASE_MIN_CONF = 0.55      # 追击目标的最低置信度(幻影框过滤; 危险怪不受此限)
MYTHIC_LATCH_ENABLED  = True   # 贴脸有 Mythic 怪 → 锁定优先清掉再继续刷 (总开关)
MYTHIC_ENGAGE_PX      = 650    # Mythic 怪进此半径 → 锁定. 实测 --watch: 玩家眼里"贴脸"
                              # 的 Mythic 蝎子/甲虫 中心距其实 450~540px, 旧值 450 全卡在外
MYTHIC_RELEASE_PX     = 850    # 已锁定后, Mythic 出此半径才算脱离 (迟滞)
MYTHIC_RELEASE_MISSES = 3      # 连续多少次扫描没有合格 Mythic 才解锁
MYTHIC_STRAFE_RADIUS  = 180    # 甲虫/火蚁: 环绕它转圈的目标半径 (px)
MYTHIC_CACTUS_HOLD_PX = 220    # 仙人掌: 保持的距离 (px)
MYTHIC_STRAFE_K_RADIAL = 0.8   # 甲虫/火蚁环绕: 径向修正强度 (d 偏离半径时往里/外带多少)
# 以上数值是没实机测过的占位默认值, 实机跑一遍后再按观察到的效果调.
# ================================================

def lazy_heuristic(node1, node2):
    return math.sqrt((node1.x - node2.x) ** 2 + (node1.y - node2.y) ** 2)


def line_of_sight(map, node1, node2):
    x0, y0 = node1.x, node1.y
    x1, y1 = node2.x, node2.y
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy

    while True:
        if map[y0][x0] == 0:
            return False
        if x0 == x1 and y0 == y1:
            return True
        e2 = err * 2
        if e2 > -dy:
            err -= dy
            x0 += sx
        if e2 < dx:
            err += dx
            y0 += sy


def lazy_theta_star(map, start, goal):
    if start is None or goal is None:
        return None
    if map is None:
        return None
    
    class Node:
        def __init__(self, x, y, cost=math.inf, parent=None):
            self.x = x
            self.y = y
            self.cost = cost
            self.parent = parent

        def __lt__(self, other):
            return self.cost < other.cost
    
    open_list = []
    closed_list = set()
    start_node = Node(start[0], start[1], 0)
    goal_node = Node(goal[0], goal[1])
    heapq.heappush(open_list, (start_node.cost +
                   lazy_heuristic(start_node, goal_node), start_node))

    while open_list:
        _, current = heapq.heappop(open_list)
        if (current.x, current.y) in closed_list:
            continue
        closed_list.add((current.x, current.y))

        if current.x == goal_node.x and current.y == goal_node.y:
            path = []
            while current:
                path.append((current.x, current.y))
                current = current.parent
            return path[::-1]

        neighbors = [(current.x + dx, current.y + dy) for dx, dy in [(-1, 0),
                                                                     (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)]]
        for nx, ny in neighbors:
            if 0 <= nx < len(map[0]) and 0 <= ny < len(map) and map[ny][nx] == 255:
                neighbor = Node(nx, ny)
                if (nx, ny) not in closed_list:
                    if current.parent and line_of_sight(map, current.parent, neighbor):
                        new_cost = current.parent.cost + \
                            lazy_heuristic(current.parent, neighbor)
                        if new_cost < neighbor.cost:
                            neighbor.cost = new_cost
                            neighbor.parent = current.parent
                    else:
                        new_cost = current.cost + \
                            lazy_heuristic(current, neighbor)
                        if new_cost < neighbor.cost:
                            neighbor.cost = new_cost
                            neighbor.parent = current
                    heapq.heappush(open_list, (neighbor.cost +
                                   lazy_heuristic(neighbor, goal_node), neighbor))

    return None


def reset_keyboard():
    pyautogui.keyUp("space")
    keyup("w")
    keyup("a")
    keyup("s")
    keyup("d")


def move_to_position(current_pos, target_pos, max_attempts=200, stall_limit=13,
                     progress_epsilon=1.5, on_tick=None):
    """移动到目标位置.

    on_tick: 可选回调, 每个内循环 tick(moveTo 之后、sleep 之前)调一次, 传入当前
    minimap 坐标. 返回真值 → 立刻收手, move_to_position 把那个真值原样返回给调用方
    (约定用短字符串, 比如 "enemy"). 给 auto_farming 的 wander 腿用: 这函数是阻塞的,
    整段(max_attempts×0.05s)期间外层拿不回控制权、跑不了索敌, 快怪冲过来就撞死 ——
    钩子让 wander 途中也能触发一次索敌、需要接战/规避时中断这条腿. on_tick=None
    (execute_path / lazy_theta_pathing 那些纯赶路调用)时行为跟以前完全一样.

    跟原版(github.com/Shiny-Ladybug/florr-auto-pathing)的go_direction比对后, 补回了
    两条它有而我们这版"简化版本"漏掉的关键判定 —— 之前只看"到没到5px内", 不看有
    没有在朝目标靠近, 导致过头或者原地打转都要死等到max_attempts才认卡住:
      - 冲过头(这次比上次离目标还远) —— 已经很接近了, 直接算到达, 别死磕这一段.
      - 连续stall_limit次距离都没缩短(原地打转) —— 才真正判定为卡住, 不是简单数
        循环次数. max_attempts只是保底上限, 防止极端情况死循环, 平时基本不会撞到.

    原版(以及我们最早抄过来那版)这两条判定都是用"距离完全相等"(dist == last_dist)
    做比较 —— 实测位置检测本身有量化噪声, 连续两帧distance几乎不可能位级精确相等,
    导致卡在死角/洞里时stall_count永远攒不起来, "卡住"判定形同虚设, 角色能在原地
    干耗到天荒地老。改用progress_epsilon容差带: 只要没有明显缩短(缩短量小于
    progress_epsilon)就算一次停滞, 不再要求毫厘不差.
    """
    if current_pos is None or target_pos is None:
        return "stuck"

    last_dist = None
    stall_count = 0
    attempts = 0
    while attempts < max_attempts:
        if afk_watch.poll_afk_pause():
            overlay.update(state="AFK弹窗处理中", message="等待florr-auto-afk解题")
            time.sleep(0.2)
            # 暂停期间角色可能被上一次鼠标指令继续带着走(这游戏靠鼠标位置转向,
            # 不是靠按键状态) —— 暂停12秒后dist跟last_dist已经没有可比性了,
            # 不清零的话很容易被误判成"冲过头"直接算到达. 清成跟函数开头一样的
            # 初始状态, 让暂停后第一个真实tick当"刚开始移动"处理.
            last_dist = None
            stall_count = 0
            continue

        current_pos = get_player_position()
        if current_pos is None:
            overlay.update(state="无法检测位置", message="移动中丢失玩家位置")
            return "stuck"

        # 计算方向
        dx = target_pos[0] - current_pos[0]
        dy = target_pos[1] - current_pos[1]
        dist = math.sqrt(dx**2 + dy**2)

        overlay.update(state="移动中", pos=current_pos, target=target_pos)

        # 如果已到达目标
        if dist < 5:
            reset_keyboard()
            return True

        if last_dist is not None:
            if dist > last_dist + progress_epsilon:
                # 明显冲过头了, 已经足够接近, 当作到达, 不继续死磕这一段.
                reset_keyboard()
                return True
            elif dist < last_dist - progress_epsilon:
                # 明显缩短了, 真有进展, 停滞计数清零.
                stall_count = 0
            else:
                # 在容差带内(包括原来要求毫厘不差才算的"完全相等") —— 没有实质进展.
                stall_count += 1
        last_dist = dist

        if stall_count > stall_limit:
            reset_keyboard()
            overlay.update(state="卡住", message=f"原地打转{stall_count}次")
            return "stuck"

        # 移动鼠标指向目标
        extend = max(min(dist * 45, 500), 50) * mouse_scale()
        if dist > 0:
            extend_x = extend * dx / dist
            extend_y = extend * dy / dist
        else:
            extend_x = extend_y = 0

        mouse_pos = clamp_to_screen(SCREEN_WIDTH // 2 + extend_x, SCREEN_HEIGHT // 2 + extend_y)
        pyautogui.moveTo(mouse_pos)

        # 检查游戏状态 —— 不用check_stage(): 它的in_game_dead/in_menu判定靠
        # 探测固定像素点是不是某个精确RGB, 连1920x1080参照分辨率下都从没真正
        # 触发过(见on_death_screen()的注释), 2026-08-26在1280x923上实测过更是
        # 直接把"正常游戏中"误判成"in_game_dead"(探测点landed在别的白色UI上)。
        # on_death_screen()/on_start_screen()是靠采样一块区域算颜色占比, 已经
        # 经过缩放+实机验证, 顶层重试循环(lazy_theta_pathing)一直用的就是这套,
        # 这里跟着统一, 不再有两条不一致的死亡检测逻辑.
        if on_death_screen():
            reset_keyboard()
            overlay.update(state="已死亡")
            return "in_game_dead"
        elif on_start_screen():
            reset_keyboard()
            overlay.update(state="菜单中")
            return "in_menu"

        if on_tick is not None:
            signal = on_tick(current_pos)
            if signal:
                reset_keyboard()
                return signal

        attempts += 1
        time.sleep(0.05)

    reset_keyboard()
    overlay.update(state="卡住", message=f"{max_attempts}次尝试后仍未到达")
    return "stuck"


def execute_path(path):
    """执行路径"""
    if path is None or len(path) == 0:
        return "stuck"
    
    print(f"🗺️  执行路径，共 {len(path)} 个节点...")
    
    for i in range(len(path) - 1):
        current = path[i]
        next_point = path[i + 1]
        
        print(f"   [{i+1}/{len(path)-1}] 移动到 {next_point}")
        result = move_to_position(current, next_point)
        
        if result == "stuck":
            print(f"   ⚠️ 在 {next_point} 卡住了")
            return "stuck"
        elif result in ["in_game_dead", "in_menu"]:
            return result
    
    print("✅ 路径执行完成")
    return True


def lazy_theta_pathing(location, area=[]):
    """寻路到目标区域. 检测不到位置、或者移动卡住, 都不放弃, 一直重试
    (脱困后重新规划路径)直到真的到达/玩家死亡/进了菜单为止。
    """
    retry_count = 0

    while True:
        if afk_watch.poll_afk_pause():
            overlay.update(state="AFK弹窗处理中", message="等待florr-auto-afk解题")
            time.sleep(0.2)
            continue

        # 死亡/开局画面的检查必须放在最前面、且不能只在"pos is None"分支里做 ——
        # 实机踩过坑: 死亡结算画面上凑巧有个像素跟玩家标记色对上了, 稳定测出一个
        # 假位置(不是None!), 导致下面那个"pos is None才查死亡画面"的分支永远
        # 进不去, 角色明明卡在死亡画面上, 脚本还在拿假坐标一遍遍重新规划路径。
        # 不管这轮测没测到位置, 每次循环开头都先确认没有落在这两个画面上.
        if on_death_screen() or on_start_screen():
            print("🔁 检测到落在死亡/开局画面上, 交回上层处理")
            overlay.update(state="出错", message="落在死亡/开局画面, 交回上层重开")
            return False

        pos = get_player_position()

        if pos is None:
            # 死亡/开局画面已经在循环开头查过了, 到这里还是None就是真的暂时没
            # 认出玩家标记(截图抖动之类), 单纯重试.
            retry_count += 1
            print(f"⚠️ 无法检测玩家位置，持续重试中 (第{retry_count}次)...")
            # 只在小状态框 + 控制台/GUI 日志里提示 —— 以前 retry_count>7 会弹一个
            # 屏幕正中央的大号黄色警告窗, 结果那个窗盖住了小地图, get_player_position()
            # 截图截到的是警告窗本身, 位置永远认不回来, 警告窗也就再也不消失. 死循环.
            hint = ("持续重试中 (第{}次)".format(retry_count) if retry_count <= 7
                    else "第{}次仍测不到 —— 检查地图是否被放大(M键) / 窗口是否全屏(F11)".format(retry_count))
            overlay.update(state="无法检测位置", message=hint)
            time.sleep(1)
            continue

        retry_count = 0
        print(f"\n📍 寻路: {pos} -> {location}")
        overlay.update(state="寻路中", pos=pos, target=location, message="规划路径...")
        time_now = time.time()

        binary_map = load_binary_map()
        if binary_map is None:
            print("❌ 地图加载失败")
            overlay.update(state="出错", message="地图加载失败")
            return False

        path = lazy_theta_star(binary_map, pos, location)
        print(f"⏱️  寻路耗时: {time.time() - time_now:.2f}秒")

        if path is None:
            print("❌ 路径规划失败")
            overlay.update(state="出错", message="路径规划失败")
            return False

        print(f"✅ 找到路径，共 {len(path)} 个点")
        overlay.update(message=f"找到路径, 共{len(path)}个点")
        stat = execute_path(path)

        # 检查是否到达目标区域
        current_pos = get_player_position()
        if current_pos and if_in_area(area, current_pos):
            print(f"✅ 已到达目标区域！位置: {current_pos}\n")
            overlay.update(state="完成", pos=current_pos, message="已到达目标区域")
            return True

        if current_pos == location:
            print(f"✅ 已到达目标位置！\n")
            overlay.update(state="完成", pos=current_pos, message="已到达目标位置")
            return True

        if stat == "stuck":
            print("🔄 检测到卡住, 脱困后重新规划路径...")
            overlay.update(state="卡住", message="脱困中, 稍后重新寻路")
            execute_anti_stuck()
            continue

        if on_death_screen():
            print("💀 玩家已死亡")
            overlay.update(state="已死亡")
            return False
        elif on_start_screen():
            print("📋 玩家在菜单中")
            overlay.update(state="菜单中")
            return False


def random_walkable_point(area, binary_map, max_tries=20):
    """在矩形区域内随机采样一个可走点(binary_map里=255的), 而不是纯瞎猜坐标.

    之前是在整个矩形里直接randint, 完全不管地图形状 —— 采样到墙里/区域外形状
    (不是每个刷怪区域都是实心矩形)的点很常见, 角色会直接顶着墙走不过去。
    这里改成拒绝采样: 采到墙就重来, max_tries次都不行就退回原来的随机点
    (兜底, 不会因为极端形状的区域卡死采样).
    """
    (x1, y1), (x2, y2) = area
    for _ in range(max_tries):
        x = random.randint(x1, x2)
        y = random.randint(y1, y2)
        if binary_map is not None and 0 <= y < binary_map.shape[0] and 0 <= x < binary_map.shape[1]:
            if binary_map[y, x] == 255:
                return x, y
    return random.randint(x1, x2), random.randint(y1, y2)


def _maybe_scan_enemies(enemy_ai_enabled, now, last_enemy_scan, prev_decision, prev_detections):
    """索敌节流 + 总开关. 返回 (decision, detections, last_enemy_scan, scanned).

    - enemy_ai_enabled=False: 永远返回漫游决策 + 空检测列表, 一次都不碰 enemy_detect.
    - 距上次扫描不到 ENEMY_SCAN_INTERVAL: 沿用上一轮的 decision 和 detections.
    - 到点了: 跑一次 scan_enemies + select_action; 任何异常 → 漫游 + 空列表.
    detections 单独回传是给 Mythic 近身锁定用的 (select_action 不看这个).
    scanned 只在真跑了一次 scan_enemies 的分支为 True (含扫描抛错 —— 尝试过一次
    观测就算数); 关掉索敌 / 节流跳过的 tick 是 False. 调用方靠这个只在新鲜扫描上
    推进 Mythic miss 计数, 别让节流 tick 拿同一份缓存检测重复扣数.
    """
    if not enemy_ai_enabled:
        return ("wander", None), [], last_enemy_scan, False
    if now - last_enemy_scan < ENEMY_SCAN_INTERVAL:
        return prev_decision, prev_detections, last_enemy_scan, False
    last_enemy_scan = now
    try:
        detections = enemy_detect.scan_enemies()
        decision = enemy_detect.select_action(
            detections,
            avoid_trigger_px=AVOID_TRIGGER_PX,
            cautious_hold_px=CAUTIOUS_HOLD_PX,
            chase_min_conf=CHASE_MIN_CONF,
        )
    except Exception as e:
        print(f"⚠️ 索敌出错, 本轮当漫游处理: {e}")
        decision, detections = ("wander", None), []
    return decision, detections, last_enemy_scan, True


def _update_mythic_latch(latched, misses, has_target, release_misses):
    """Mythic 近身锁定的状态机 (纯函数). has_target = 这次扫描有没有合格的近身
    Mythic. 有 → 锁定, misses 清零. 没有且已锁定 → misses+1, 攒够 release_misses
    就解锁 (迟滞, 扛检测闪烁). 返回 (latched, misses)."""
    if has_target:
        return True, 0
    if not latched:
        return False, 0
    misses += 1
    if misses >= release_misses:
        return False, 0
    return True, misses


def _drive_and_check_stall(mouse_target, current_pos, chase_pos_history, state, message):
    """chase / flee / 清青怪 三条分支共用的"卡住检测 + 出手"收尾.

    mouse_target == enemy_detect.SCREEN_CENTER 是"刻意停在这" (保持距离 / 合力抵消),
    不算移动 —— 这种 tick 不往 history 塞样本 (留给下一个真在动的 tick). 其余情况:
    攒近期 minimap 坐标, 时间窗内净位移不足 → execute_anti_stuck() 接管这一 tick、
    返回 "stuck"; 否则 overlay 更新 + moveTo + sleep, 返回 "moved"."""
    if mouse_target != enemy_detect.SCREEN_CENTER:
        chase_pos_history.append(current_pos)
        if len(chase_pos_history) > enemy_detect.CHASE_STALL_WINDOW:
            chase_pos_history.pop(0)
        if enemy_detect.chase_is_stalled(chase_pos_history):
            print(f"⚠️ {state}途中卡住, 脱困一下...")
            overlay.update(state="卡住", message=f"{state}卡住, 脱困中")
            execute_anti_stuck()
            chase_pos_history.clear()
            return "stuck"
    overlay.update(state=state, pos=current_pos, message=message)
    pyautogui.moveTo(clamp_to_screen(*mouse_target))
    time.sleep(0.05)
    return "moved"


def auto_farming(farming_area, duration=300, *, enemy_ai_enabled=True):
    """自动刷怪逻辑（依赖一直攻击按钮）—— 在区域内连续走动, 不停下站桩.

    原来是走到一个随机点就停下等move_interval秒(靠站桩+一直攻击刷怪), 用户反馈
    应该在区域内持续走动而不是走走停停 —— 一到点立刻挑下一个可走点接着走, 全程
    不主动暂停, 靠外部"一直攻击"按钮在移动中持续输出.
    """
    x1, y1 = farming_area[0]
    x2, y2 = farming_area[1]

    min_x, max_x = min(x1, x2), max(x1, x2)
    min_y, max_y = min(y1, y2), max(y1, y2)
    farming_area = [(min_x, min_y), (max_x, max_y)]
    binary_map = load_binary_map()

    print(f"\n🎮 开始在区域 {farming_area} 进行自动刷怪...")
    print(f"⏱️  刷怪时长: {duration}秒（持续走动模式）\n")
    overlay.update(state="刷怪中", message=f"区域 {farming_area}")

    start_time = time.time()
    move_count = 0
    exit_reason = "timeout"
    last_enemy_scan = 0.0
    enemy_decision = ("wander", None)
    detections = []
    chase_pos_history = []   # 近期minimap坐标, 供enemy_detect.chase_is_stalled()看净位移
    mythic_latch = False
    mythic_misses = 0
    mythic_target_pos = None   # 上一 tick 锁定 Mythic 的屏幕坐标, 给 pick_mythic_target 做连续性

    def _wander_enemy_watch(_pos):
        """move_to_position 的 on_tick 钩子: wander 腿途中做一次(节流的)索敌, 需要
        规避/接战/锁 Mythic 时返回 "enemy" 中断这条腿, 外层下个 tick 就按刚更新的
        enemy_decision 处理. 只更新扫描状态、不推进 mythic miss 计数(那个归外层
        mythic 分支的 scanned 门管)."""
        nonlocal enemy_decision, detections, last_enemy_scan
        enemy_decision, detections, last_enemy_scan, _scanned = _maybe_scan_enemies(
            enemy_ai_enabled, time.time(), last_enemy_scan, enemy_decision, detections)
        if enemy_decision[0] in ("flee", "chase"):
            return "enemy"
        if (MYTHIC_LATCH_ENABLED and enemy_ai_enabled
                and enemy_detect.pick_mythic_target(
                    detections, center=enemy_detect.SCREEN_CENTER, latched=mythic_latch,
                    engage_px=MYTHIC_ENGAGE_PX, release_px=MYTHIC_RELEASE_PX,
                    chase_min_conf=CHASE_MIN_CONF, prev_pos=mythic_target_pos) is not None):
            return "enemy"
        return None

    while time.time() - start_time < duration:
        if afk_watch.poll_afk_pause():
            overlay.update(state="AFK弹窗处理中", message="等待florr-auto-afk解题")
            time.sleep(0.2)
            # 暂停/丢位置/出区一圈回来后场景可能全变了 —— 别带着旧锁定用 600 释放
            # 半径, 让下一 tick 重新过 450 接战门槛.
            mythic_latch, mythic_misses, mythic_target_pos = False, 0, None
            continue

        # 死亡/开局画面检查放在循环最前面、不依赖"位置测不到" —— 死亡结算画面上
        # 曾经实测出过稳定的假位置(不是None), 只在current_pos is None分支里查
        # 会被这种假阳性绕过去, 角色明明已经死了脚本还在拿假坐标继续瞎刷.
        if on_death_screen() or on_start_screen():
            print("🔁 检测到落在死亡/开局画面上, 交回上层处理")
            overlay.update(state="出错", message="落在死亡/开局画面, 交回上层重开")
            exit_reason = "break"
            break

        current_pos = get_player_position()

        if current_pos is None:
            print("⚠️ 无法检测玩家位置")
            overlay.update(state="无法检测位置")
            time.sleep(1)
            mythic_latch, mythic_misses, mythic_target_pos = False, 0, None
            continue

        # 检查是否还在刷怪区域
        if not if_in_area([farming_area], current_pos):
            print(f"⚠️ 离开刷怪区域 (当前: {current_pos})，重新寻路回去")
            overlay.update(state="离开刷怪区域", pos=current_pos, message="重新寻路回去")
            target_x = (farming_area[0][0] + farming_area[1][0]) // 2
            target_y = (farming_area[0][1] + farming_area[1][1]) // 2
            if not lazy_theta_pathing((target_x, target_y), [farming_area]):
                print("❌ 无法回到刷怪区域")
                overlay.update(state="出错", message="无法回到刷怪区域")
                exit_reason = "break"
                break
            mythic_latch, mythic_misses, mythic_target_pos = False, 0, None
            continue

        # 索敌: 按ENEMY_SCAN_INTERVAL节流解码canvas帧(不是每tick都跑, 有开销).
        # 索敌是附加功能, 任何异常都退化成"漫游", 不能让它打断刷怪主循环.
        now = time.time()
        enemy_decision, detections, last_enemy_scan, scanned = _maybe_scan_enemies(
            enemy_ai_enabled, now, last_enemy_scan, enemy_decision, detections)
        enemy_action = enemy_decision[0]

        # 1) flee 最优先 —— 且立刻放掉 Mythic 锁定 (躲优先, 不为打 Mythic 送死).
        if enemy_action == "flee":
            mythic_latch, mythic_misses = False, 0
            mouse_target = enemy_detect.flee_mouse_target(enemy_decision[1])
            _drive_and_check_stall(mouse_target, current_pos, chase_pos_history,
                                   "规避中", "附近有危险稀有怪, 拉开距离")
            continue

        # 2) Mythic 近身锁定 —— flee 之外, 贴脸有合格 Mythic 就锁定按物种走位磨掉.
        if MYTHIC_LATCH_ENABLED and enemy_ai_enabled:
            mtarget = enemy_detect.pick_mythic_target(
                detections, center=enemy_detect.SCREEN_CENTER, latched=mythic_latch,
                engage_px=MYTHIC_ENGAGE_PX, release_px=MYTHIC_RELEASE_PX,
                chase_min_conf=CHASE_MIN_CONF, prev_pos=mythic_target_pos)
            # miss 计数只在真跑过扫描的 tick 推进 —— 节流 tick 拿的是同一份缓存
            # 检测, 再扣一次等于把同一帧证据数两遍, 3-miss 释放在快机器上缩成 ~2.
            if scanned:
                mythic_latch, mythic_misses = _update_mythic_latch(
                    mythic_latch, mythic_misses, mtarget is not None, MYTHIC_RELEASE_MISSES)
            if mythic_latch and mtarget is not None:
                mythic_target_pos = mtarget["screen_pos"]
                repel = enemy_decision[3] if enemy_action == "chase" else []
                mouse_target = enemy_detect.mythic_move_target(
                    mtarget, enemy_detect.SCREEN_CENTER,
                    strafe_radius=MYTHIC_STRAFE_RADIUS,
                    cactus_hold_px=MYTHIC_CACTUS_HOLD_PX,
                    repel_positions=repel, k_radial=MYTHIC_STRAFE_K_RADIAL)
                policy = enemy_detect.MYTHIC_KITE_SPECIES[mtarget["species"]]
                _drive_and_check_stall(mouse_target, current_pos, chase_pos_history,
                                       "清青怪", f"遛 {mtarget['species']}({policy})")
                continue
            # 没锁定 / 这 tick 没目标 —— 放掉连续性锚点, 别让下次锁定拿旧坐标.
            mythic_target_pos = None

        # 3) 普通追击 —— 不 fleeing 也没锁定 Mythic.
        if enemy_action == "chase":
            target, hold_px, repel = enemy_decision[1], enemy_decision[2], enemy_decision[3]
            mouse_target = enemy_detect.aim_mouse_target(
                target["screen_pos"], hold_px=hold_px, repel_positions=repel)
            _drive_and_check_stall(mouse_target, current_pos, chase_pos_history,
                                   "索敌中", f"追击 {target['species']}({target['rarity']})")
            continue

        # 4) enemy_action == "wander": 没有可打/需规避的目标, 随机漫游.
        chase_pos_history.clear()
        random_x, random_y = random_walkable_point(farming_area, binary_map)

        # 移动到目标点 —— 到了立刻挑下一个点接着走, 不暂停.
        print(f"🚶 移动到 ({random_x}, {random_y})")
        overlay.update(state="刷怪中", pos=current_pos, target=(random_x, random_y), message=f"持续走动中 (第{move_count + 1}次)")
        # max_attempts=20 (≈1s worst case at time.sleep(0.05)每tick) 而不是默认的
        # 200(≈10s) —— 让外层循环更频繁拿回控制权重新索敌扫描, 见下面ENEMY_SCAN_INTERVAL
        # 的注释.
        move_result = move_to_position(current_pos, (random_x, random_y),
                                       max_attempts=20, on_tick=_wander_enemy_watch)

        if move_result == "enemy":
            # 路途中扫到怪(该 flee/chase/锁 Mythic) —— 立刻回外层, 下个 tick 用
            # _wander_enemy_watch 刚更新的 enemy_decision 处理, 不算走完一趟.
            continue
        if move_result == "stuck":
            print("⚠️ 移动受阻, 脱困一下...")
            overlay.update(state="卡住", message="脱困中")
            execute_anti_stuck()
        elif move_result in ["in_game_dead", "in_menu"]:
            print(f"⚠️ 游戏状态变化: {move_result}")
            exit_reason = "break"
            break
        else:
            # 只有真正走到点上才计入移动次数, "受阻"那次不算.
            move_count += 1

        # 检查游戏状态
        if on_death_screen():
            print("💀 玩家已死亡")
            overlay.update(state="已死亡")
            exit_reason = "break"
            break
        elif on_start_screen():
            print("📋 玩家在菜单中")
            overlay.update(state="菜单中")
            exit_reason = "break"
            break

    elapsed = time.time() - start_time
    print(f"\n" + "="*50)
    print(f"✅ 刷怪完成！")
    print(f"   实际耗时: {elapsed:.1f}秒")
    print(f"   移动次数: {move_count}")
    print(f"="*50)
    if exit_reason == "timeout":
        overlay.update(state="完成", message=f"刷怪结束, 共移动{move_count}次")
    else:
        overlay.update(message=f"刷怪结束, 共移动{move_count}次")

    # 是不是刷满了整个duration —— 给调用方(主循环)判断"这轮算不算刷够时长"用,
    # 不刷满(死亡/被踢/卡死放弃)的连续出现太多次, 说明这个服务器可能有问题
    # (比如刷怪区域被占、或者哪里持续卡关), 值得换个服务器而不是死磕.
    return exit_reason == "timeout"


def _apply_worker_config(cfg):
    """把 config.json 的值应用/摊平成 run_worker 主循环要用的局部值.
    v2: 读 cfg['active'](GUI 调度器在起 worker 前刷成"当前生效时块"的刷怪参数);
    老扁平文件 / 手写调试文件: 回退 cfg 本身; 再缺的键: 回退 app_config.DEFAULTS.
    apply_map() 必须在这里就调 —— utils 的 MAP 是模块级全局, load_binary_map()
    等一堆函数都读它."""
    src = cfg.get("active")
    if not isinstance(src, dict):
        src = cfg
    d = app_config.DEFAULTS
    apply_map(src.get("map", d["map"]))
    return {
        "location": tuple(src.get("location", d["location"])),
        "farming_area": [tuple(p) for p in src.get("farming_area", d["farming_area"])],
        "farming_duration": src.get("farming_duration", d["farming_duration"]),
        "short_round_limit": src.get("consecutive_short_round_limit",
                                    d["consecutive_short_round_limit"]),
        "enemy_ai_enabled": src.get("enemy_ai_enabled", d["enemy_ai_enabled"]),
        "auto_switch_server": src.get("auto_switch_server", d["auto_switch_server"]),
        "biome": server_lookup.biome_key_for_map(src.get("map", d["map"])),
        "enter_game_swap": src.get("enter_game_swap", d["enter_game_swap"]),
        "reach_area_swap": src.get("reach_area_swap", d["reach_area_swap"]),
    }


def _reassert_florr_toggles(want_attack, want_defense):
    """按 config 把 florr 的反转攻击键 / 反转防御键字节写成 1(True)/0(False).
    florr 每次从菜单进局会从账号数据把这两个字节盖回 —— 所以 run_worker 启动时一次 +
    每轮进游戏后一次都要重写. 返回 {"attack": status, "defense": status}
    (status ∈ unchanged/changed/failed). unchanged 静默; changed / failed 才打日志;
    任一 failed 都不中断 worker."""
    out = {}
    for name, label, addr, want in (
        ("attack", "反转攻击键", florr_settings.INVERT_ATTACK_ADDR, 1 if want_attack else 0),
        ("defense", "反转防御键", florr_settings.INVERT_DEFENSE_ADDR, 1 if want_defense else 0),
    ):
        status, detail = florr_settings.ensure_flag(cdp_bridge.eval_js, addr, want)
        out[name] = status
        if status == "changed":
            print(f"✅ {label} 已(重新)设为 {want}")
        elif status == "failed":
            print(f"⚠️ {label} 未确认 ({detail}) —— 手动到 设置→控制 里勾/取消")
    return out


_BIOME_LOCK_RETRIES = 3
_BIOME_LOCK_RETRY_SLEEP = 3.0
_BIOME_RECONNECT_SLEEP = 3.0


def _lock_biome(biome):
    """把客户端钉到 biome 对应生态区的服务器. florr 不记忆上次选的生态区 —— 不锁
    的话 click_start_game() 进的是 florr 默认那个(通常花园), 跟寻路用的地图对不上.
    复用 switch_server(biome) 的 CDP forceServerID(仓库历史确认过能触发重连).

    失败重试 _BIOME_LOCK_RETRIES 次(隔 _BIOME_LOCK_RETRY_SLEEP 秒), 都不成只警告
    不阻断(跟 _reassert_florr_toggles 一个风格)—— 宁可这轮进错生态区, 也不卡死在
    开局菜单外面. 成功后 sleep 等重连落地再让调用方开始寻路. 返回 True/False.
    """
    for attempt in range(1, _BIOME_LOCK_RETRIES + 1):
        try:
            sid = switch_server(biome)
            print(f"🗺️ 已锁定生态区 {biome} (服务器 {sid})")
            time.sleep(_BIOME_RECONNECT_SLEEP)
            return True
        except Exception as e:
            print(f"⚠️ 锁定生态区第 {attempt}/{_BIOME_LOCK_RETRIES} 次失败: {e}")
            if attempt < _BIOME_LOCK_RETRIES:
                time.sleep(_BIOME_LOCK_RETRY_SLEEP)
    print("⚠️ 生态区没锁上, 先按当前服务器进游戏 (下轮回开局菜单再试)")
    return False


def _wait_for_start_menu(timeout=15, interval=0.5):
    """轮询等 florr 开局菜单("开始"按钮)出现/回来. forceServerID 锁生态区会触发一次
    重连、florr 短暂离开开局菜单 —— 锁完等菜单回来再点, 别在重连空档里空点(那会让
    click_start_game 复查时以为已经进去了). 到点还没出现返回 False, 调用方
    (click_start_game) 自己还会重试. 已经在菜单上则立刻返回 True."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if on_start_screen():
            return True
        time.sleep(interval)
    return False


def run_worker(cfg):
    """刷怪 worker: 由 GUI 以 `main.py --worker` 子进程拉起. 掉线/死亡后自动点
    开始重来, 不主动停(沿用改造前 __main__ 的行为)."""
    # 直接 python main.py --worker 调试时给个清楚的报错 —— 交互式 Chrome 引导
    # 已经搬进 GUI, 这条路不再自己拉 Chrome.
    if not cdp_bridge.is_dedicated_chrome_ready():
        print("❌ 专用 Chrome 未就绪. 请从 GUI 启动(GUI 会引导你准备 Chrome).")
        sys.exit(1)

    # florr-auto-afk 的生命周期整个归 GUI 管(gui_app._ensure_afk / _on_afk_toggle,
    # 两条路都先看 AFK 开关). worker 这边只 poll_afk_pause() 读它的日志, 绝不自己
    # 去拉起它 —— 以前无条件 ensure_florr_auto_afk_running() 有两个真实后果:
    # (1) exe 不在时它会走 input() 问要不要下载, 而 console=False 的打包 exe 里
    #     worker 的 stdin 是死的, 直接 RuntimeError 把 worker 撂倒;
    # (2) 用户刚在界面上关掉 AFK 开关(GUI 已 stop_florr_auto_afk), worker 一起来
    #     又给它拉回去.
    global overlay
    overlay = create_overlay()

    # bot 自己不按攻击键, 靠 florr 的「反转攻击键」持续输出;「反转防御键」是对称的
    # 可选项. florr 每次从菜单进局都会从账号数据重载设置、把这两个字节盖回原值 ——
    # 所以不能只在这写一次, 每轮进游戏后都要重写(_reassert_florr_toggles, 见主循环).
    # 这里先探一次给即时反馈: 地址没标定 / florr 更新导致地址失效时立刻在悬浮窗
    # 警告, 不用等第一轮.

    # 没登录过的 Chrome profile 停在 florr 的登录选择页(绿色「以游客身份游玩」
    # + Discord/Apple). 先点掉它, 让 florr 开始加载游戏 —— 否则下面的
    # _reassert_florr_toggles() 走 CDP 读 window.Module 时 WASM 还没就绪, 白报
    # 一次 failed. 登录过的号 on_guest_screen() 恒 False, 这段是 no-op.
    if on_guest_screen():
        print("👤 未登录标题页, 先点『以游客身份游玩』进正常标题页...")
        overlay.update(state="重新开始", message="点击游客登录...")
        click_play_as_guest()
        time.sleep(2)

    # 反转攻击键 / 反转防御键的目标值直接从整份 cfg 取(顶层键, 不在 active 切片 /
    # _apply_worker_config 输出里), 缺键回退 app_config.DEFAULTS.
    _d = app_config.DEFAULTS
    want_attack = cfg.get("invert_attack", _d["invert_attack"])
    want_defense = cfg.get("invert_defense", _d["invert_defense"])

    if "failed" in _reassert_florr_toggles(want_attack, want_defense).values():
        overlay.update(message="⚠️ 反转键未全部确认, 见日志")

    w = _apply_worker_config(cfg)
    location = w["location"]
    farming_area = w["farming_area"]
    farming_duration = w["farming_duration"]
    CONSECUTIVE_SHORT_ROUND_LIMIT = w["short_round_limit"]

    print("🎮 开始自动寻路+刷怪 (掉线/死亡后自动点开始重来, 不主动停)\n")
    consecutive_short_rounds = 0
    round_count = 0
    while True:
        round_count += 1
        round_start_time = time.time()
        print(f"\n{'='*50}\n第 {round_count} 轮\n{'='*50}")

        entered_game = False
        if on_guest_screen():
            print("👤 检测到未登录标题页, 点击『以游客身份游玩』...")
            overlay.update(state="重新开始", message="点击游客登录...")
            click_play_as_guest()
            time.sleep(2)
        if on_death_screen():
            print("💀 检测到死亡结算画面, 点击继续...")
            overlay.update(state="重新开始", message="死亡, 点击继续...")
            click_continue_after_death()
            entered_game = True
            time.sleep(2)
        if on_start_screen():
            print("🔁 检测到开局菜单, 点击开始按钮进入游戏...")
            overlay.update(state="重新开始", message="点击开始按钮...")
            # 在标题页(还没连进局)就把服务器钉到配置的生态区, 再点开始 —— 点开始
            # 会连到这台服务器 = 进对生态区. 顺序不能反: 先 click_start_game() 进局
            # 再 forceServerID, 会触发一次重连把人踢回标题页, 形成"进游戏→踢出→
            # 进游戏"死循环 (florr 从局内 forceServerID 不会原地换服, 是断开重连).
            _lock_biome(w["biome"])
            # forceServerID 的重连期间 florr 会短暂离开开局菜单, 等"开始"按钮回来
            # 再点, 否则 click_start_game 复查时会把空档当成"已经进去了".
            _wait_for_start_menu()
            click_start_game()
            entered_game = True
            time.sleep(3)

        # 进游戏了(或本来就在局内): florr 刚才可能从账号数据把「反转攻击键 /
        # 反转防御键」重置了, 每轮重写一次. unchanged 静默(常态), changed / failed
        # 才打日志.
        _reassert_florr_toggles(want_attack, want_defense)

        # 只在这一轮真的(重新)进了游戏、或首轮时才切 loadout —— 一命跑满
        # farming_duration 没死的下一轮不过上面两个分支, 玩家还在场上、florr 没重置
        # loadout, 再按一次 digits 这种盲切换会让非对称配置每轮漂移. press_swap
        # 内部 warn-only, 不打断轮次.
        swap_this_round = entered_game or round_count == 1
        if swap_this_round:
            loadout_swap.press_swap(w["enter_game_swap"])

        print(f"📍 目标区域: {farming_area}\n")
        overlay.update(state="启动", target=location,
                       message=f"第{round_count}轮: 开始自动寻路到刷怪区域")

        if lazy_theta_pathing(location, [farming_area]):
            print("✅ 到达刷怪区域！")
            # 到刷怪区了: 按配置的键切到"输出" loadout. 跟 enter swap 同一道 gate ——
            # 存活续命轮 florr 没重置 loadout, 不重按.
            if swap_this_round:
                loadout_swap.press_swap(w["reach_area_swap"])
            auto_farming(farming_area, farming_duration,
                         enemy_ai_enabled=w["enemy_ai_enabled"])
        else:
            print("❌ 本轮未能到达目标区域")
            overlay.update(message="本轮未能到达目标区域")
            time.sleep(1)

        round_elapsed = time.time() - round_start_time
        completed_full_duration = round_elapsed >= farming_duration
        if completed_full_duration:
            consecutive_short_rounds = 0
        else:
            consecutive_short_rounds += 1
            print(f"⚠️ 这条命只撑了{round_elapsed:.0f}秒, 没到{farming_duration}秒 "
                  f"(连续{consecutive_short_rounds}次)")
            if (w["auto_switch_server"]
                    and consecutive_short_rounds >= CONSECUTIVE_SHORT_ROUND_LIMIT):
                print(f"🌐 连续{consecutive_short_rounds}轮没刷满, 换个服务器...")
                overlay.update(state="换服务器",
                               message=f"连续{consecutive_short_rounds}轮没刷满, 切换中")
                try:
                    switch_server(w["biome"])
                    consecutive_short_rounds = 0
                    time.sleep(2)
                except Exception as e:
                    print(f"⚠️ 换服务器失败, 先用当前服务器继续刷 (下轮再重试): {e}")
                    overlay.update(message=f"换服务器失败(下轮重试): {e}")


def _worker_graceful_exit(signum, frame):
    """GUI 点"停止"时给 worker 发的信号处理: 先把按住的方向键/空格松开, 再退出 ——
    直接 kill 的话这些键会一直是按下状态."""
    try:
        reset_keyboard()
    finally:
        sys.exit(0)


def _install_worker_signal_handlers():
    # POSIX 上 GUI 的"停止"会补一发 SIGTERM; Windows 上 SIGBREAK 只有从真控制台
    # (开发时 `python main.py --worker`)按 Ctrl+Break 才会来 —— 打包成
    # console=False 的 exe 之后两边都不保险, 真正的停止信号走 stdin EOF, 见
    # _install_worker_stdin_watcher().
    signal.signal(signal.SIGTERM, _worker_graceful_exit)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _worker_graceful_exit)


def _worker_stdin_watch():
    """阻塞读 stdin 直到对端关掉管道(EOF), 然后松开按住的键再退出."""
    try:
        sys.stdin.read()      # 阻塞到对端关闭管道 / 手动 Ctrl-D
    except Exception:
        pass
    try:
        reset_keyboard()
    finally:
        os._exit(0)


def _install_worker_stdin_watcher():
    """GUI 关闭 worker 的 stdin 管道 = 请求停止. 起一个守护线程阻塞读 stdin, 读到
    EOF(管道被关)就先松开按住的键再退出 —— 打包成 console=False 的 exe 后
    CTRL_BREAK / SIGTERM 都不一定送得到, stdin EOF 是唯一跨平台可靠的信号."""
    if sys.stdin is None:
        return
    t = threading.Thread(target=_worker_stdin_watch, daemon=True)
    t.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="florr-auto-pathing")
    parser.add_argument("--worker", action="store_true",
                        help="内部用: 跑刷怪循环子进程(由 GUI 拉起, 不要手动加)")
    args = parser.parse_args()

    if args.worker:
        _install_worker_signal_handlers()
        _install_worker_stdin_watcher()
        run_worker(app_config.load_config())
    else:
        from gui_app import main as gui_main  # 惰性 import: 不让 `import main` 拖进 GUI 依赖
        gui_main()
