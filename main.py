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

# ===== 索敌配置 (sszone敌怪检测/追击/规避) =====
ENEMY_MODEL_PATH = "models/desert.pt"
ENEMY_SCAN_INTERVAL = 0.12  # 秒, YOLO扫描节流间隔. 这是"决策新鲜度"的主旋钮:
                              # 追击/规避途中每tick都拿这份决策里的怪坐标去moveTo,
                              # 间隔越大, 中间那几tick就越是照着旧坐标全速走 —— 怪
                              # 早挪窝了, 人一头撞上去. 实测一次推理≈0.1s(Mac MPS;
                              # Windows CUDA更快), 设0.12基本每帧都能重扫. 推理太慢
                              # 的机器上循环会被推理本身卡住, 那也没办法, 至少不比
                              # 大间隔更差. 漫游时每腿路另受move_to_position的
                              # max_attempts限制(见下方wander分支).
AVOID_TRIGGER_PX = 400      # 屏幕像素半径, AVOID怪进入此半径触发逃离
CAUTIOUS_HOLD_PX = 250      # 屏幕像素, CAUTIOUS怪保持的最小距离(不继续贴近)
CHASE_MIN_CONF = 0.55      # 追击目标的最低YOLO置信度(幻影框过滤; 危险怪不受此限)
MYTHIC_LATCH_ENABLED  = True   # 贴脸有 Mythic 怪 → 锁定优先清掉再继续刷 (总开关)
MYTHIC_ENGAGE_PX      = 450    # Mythic 怪进此半径 → 锁定
MYTHIC_RELEASE_PX     = 600    # 已锁定后, Mythic 出此半径才算脱离 (迟滞)
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


def move_to_position(current_pos, target_pos, max_attempts=200, stall_limit=13, progress_epsilon=1.5):
    """移动到目标位置.

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
            overlay.update(state="无法检测位置", message=f"持续重试中 (第{retry_count}次)")
            if retry_count > 7:
                # 连续7次都没检测到, 大概率不是截图抖动这种一过性噪声了 —— 常见
                # 原因是地图被手动放大过(M键)导致小地图跟标定不对版, 或者窗口
                # 掉出全屏. 这种情况用户不会一直盯着控制台或者小状态框, 用屏幕
                # 正中央的大号警告窗——常驻画面上, 每轮重试都刷新保持可见, 直到
                # 重新检测到位置(下面的retry_count归零分支里hide_warning掉)为止.
                overlay.show_warning("无法检测到位置，请查看地图是否放大（M键）或窗口是否全屏（F11）")
            time.sleep(1)
            continue

        if retry_count > 7:
            overlay.hide_warning()
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
        detections = enemy_detect.scan_enemies(model_path=ENEMY_MODEL_PATH)
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

        # 索敌: 按ENEMY_SCAN_INTERVAL节流跑YOLO(不是每tick都跑, 推理有开销).
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
        move_result = move_to_position(current_pos, (random_x, random_y), max_attempts=20)

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
    apply_map() 必须在这里就调 —— utils 的 MAP 是模块级全局, load_binary_map()
    等一堆函数都读它."""
    apply_map(cfg["map"])
    return {
        "location": tuple(cfg["location"]),
        "farming_area": [tuple(p) for p in cfg["farming_area"]],
        "farming_duration": cfg["farming_duration"],
        "short_round_limit": cfg["consecutive_short_round_limit"],
        "enemy_ai_enabled": cfg["enemy_ai_enabled"],
        "auto_switch_server": cfg["auto_switch_server"],
    }


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

        if on_death_screen():
            print("💀 检测到死亡结算画面, 点击继续...")
            overlay.update(state="重新开始", message="死亡, 点击继续...")
            click_continue_after_death()
            time.sleep(2)
        if on_start_screen():
            print("🔁 检测到开局菜单, 点击开始按钮进入游戏...")
            overlay.update(state="重新开始", message="点击开始按钮...")
            click_start_game()
            time.sleep(3)

        print(f"📍 目标区域: {farming_area}\n")
        overlay.update(state="启动", target=location,
                       message=f"第{round_count}轮: 开始自动寻路到刷怪区域")

        if lazy_theta_pathing(location, [farming_area]):
            print("✅ 到达刷怪区域！")
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
                    switch_server()
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
