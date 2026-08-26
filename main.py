from utils import *
from overlay import create_overlay
import time
import random
import afk_watch
import enemy_detect

overlay = create_overlay()

# ===== 索敌配置 (sszone敌怪检测/追击/规避) =====
ENEMY_MODEL_PATH = "models/desert.pt"
ENEMY_SCAN_INTERVAL = 0.3   # 秒, YOLO扫描节流间隔(不是每tick都跑, 推理有开销).
                              # 注意: 漫游时每腿路最长受move_to_position的
                              # max_attempts限制(见下方wander分支), 实际响应
                              # 延迟以那个为准, 不是这个数字本身.
AVOID_TRIGGER_PX = 400      # 屏幕像素半径, AVOID怪进入此半径触发逃离
CAUTIOUS_HOLD_PX = 250      # 屏幕像素, CAUTIOUS怪保持的最小距离(不继续贴近)
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

        # 检查游戏状态
        stage = check_stage()
        if stage == "in_game_dead":
            reset_keyboard()
            overlay.update(state="已死亡")
            return "in_game_dead"
        elif stage == "in_menu":
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

        stage = check_stage()
        if stage == "in_game_dead":
            print("💀 玩家已死亡")
            overlay.update(state="已死亡")
            return False
        elif stage == "in_menu":
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


def auto_farming(farming_area, duration=300):
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
    chase_stall_count = 0
    chase_last_pos = None

    while time.time() - start_time < duration:
        if afk_watch.poll_afk_pause():
            overlay.update(state="AFK弹窗处理中", message="等待florr-auto-afk解题")
            time.sleep(0.2)
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
            continue

        # 索敌: 按ENEMY_SCAN_INTERVAL节流跑YOLO(不是每tick都跑, 推理有开销).
        # 索敌是附加功能, 任何异常都退化成"漫游", 不能让它打断刷怪主循环.
        now = time.time()
        if now - last_enemy_scan >= ENEMY_SCAN_INTERVAL:
            last_enemy_scan = now
            try:
                detections = enemy_detect.scan_enemies(model_path=ENEMY_MODEL_PATH)
                enemy_decision = enemy_detect.select_action(
                    detections,
                    avoid_trigger_px=AVOID_TRIGGER_PX,
                    cautious_hold_px=CAUTIOUS_HOLD_PX,
                )
            except Exception as e:
                print(f"⚠️ 索敌出错, 本轮当漫游处理: {e}")
                enemy_decision = ("wander", None)

        enemy_action = enemy_decision[0]

        if enemy_action == "flee":
            avoid_positions = enemy_decision[1]
            mouse_target = enemy_detect.flee_mouse_target(avoid_positions)
        elif enemy_action == "chase":
            target, hold_px = enemy_decision[1], enemy_decision[2]
            mouse_target = enemy_detect.aim_mouse_target(target["screen_pos"], hold_px=hold_px)

        if enemy_action in ("flee", "chase"):
            # wander分支靠move_to_position自带的卡住检测+execute_anti_stuck()脱困,
            # chase/flee这两个分支是每tick直接moveTo(), 没有等价机制 —— 补上同款
            # 卡住判定(看玩家自己的位置有没有实质进展), 卡住够久就让execute_anti_stuck()
            # 接管这一tick, 不再执行下面的追击/逃离moveTo().
            # 注意: mouse_target == SCREEN_CENTER时是aim_mouse_target/flee_mouse_target
            # 自己主动选择"停在这"(CAUTIOUS档保持距离/规避合力抵消没有明确方向), 不是
            # 卡住 —— 这种tick完全跳过卡住计数的推进和判定(不累加也不清零, 留给下一个
            # 真正在动的tick接着算), 否则会把"刻意停"误判成"卡住"进而执行脱困把玩家
            # 怼向它本该保持距离的危险目标。
            if mouse_target != enemy_detect.SCREEN_CENTER:
                chase_stall_count, should_yield = enemy_detect.chase_is_stalled(
                    chase_last_pos, current_pos, chase_stall_count)
                chase_last_pos = current_pos
                if should_yield:
                    print("⚠️ 追击/规避途中卡住, 脱困一下...")
                    overlay.update(state="卡住", message="追击/规避途中卡住, 脱困中")
                    execute_anti_stuck()
                    chase_stall_count = 0
                    continue

            if enemy_action == "flee":
                overlay.update(state="规避中", pos=current_pos, message="附近有危险稀有怪, 拉开距离")
            else:
                overlay.update(state="索敌中", pos=current_pos,
                                message=f"追击 {target['species']}({target['rarity']})")
            pyautogui.moveTo(clamp_to_screen(*mouse_target))
            time.sleep(0.05)
            continue

        # enemy_action == "wander": 没有可打/需规避的目标, 跟原来一样随机漫游.
        # 重置chase专属的卡住状态, 别让上一轮追击/规避的残留跨进漫游或下一轮追击.
        chase_stall_count = 0
        chase_last_pos = None
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
        stage = check_stage()
        if stage == "in_game_dead":
            print("💀 玩家已死亡")
            overlay.update(state="已死亡")
            exit_reason = "break"
            break
        elif stage == "in_menu":
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


if __name__ == "__main__":
    apply_map("desert")

    # ===== 配置部分 =====
    # 左上角那条对角斜向窄通道的上半段(maps/desert.png是300x300二值图, 逐行量过
    # 通道宽度: y=10~100持续窄(10~35px宽), y=105突然跳到88px+说明并入了旁边的
    # 主开阔区 —— 上半段取y:8~56. 框内可走像素占比56.6%, (22,32)是框内确认可走
    # 的点, 拿来当寻路目标.
    location = (22, 32)
    farming_area = [(9, 8), (51, 56)]
    farming_duration = 300  # 5 分钟
    # ====================

    print("🎮 开始自动寻路+刷怪 (掉线/死亡后自动点开始重来, 不主动停)\n")

    # 连续两轮都没刷满farming_duration(死亡/被踢/卡死放弃, 或者压根没到达刷怪
    # 区域, 都算0分钟刷怪时间), 就换个服务器 —— 不跟当前这个死磕, 阈值直接复用
    # farming_duration本身, 不再另开一个独立的"5分钟"概念跟它各调各的.
    CONSECUTIVE_SHORT_ROUND_LIMIT = 2
    consecutive_short_rounds = 0

    round_count = 0
    while True:
        round_count += 1
        # 这轮"一条命"的计时起点 —— 死亡/开局画面处理、寻路、刷怪全算在内, 不再
        # 只算auto_farming()自己跑的那一小段. 之前那版只算auto_farming()内部计时,
        # 寻路卡住重试挣扎的时间(实测经常好几十秒到几分钟)不算进去, 跟玩家自己
        # 感觉"这条命撑了多久"对不上——寻路挣扎本身也是"这个服务器不行"的信号,
        # 不该被排除在外.
        round_start_time = time.time()
        print(f"\n{'='*50}\n第 {round_count} 轮\n{'='*50}")

        # 每轮开头先检查: 死亡结算画面/开局菜单(掉线、被踢、或脚本刚启动时游戏
        # 还没点开始)都会落在这两种画面之一 —— 注意这是两个完全不同的界面(死亡
        # 画面是"你死于XX"+"继续"按钮, 开局菜单是用户名+"开始"按钮), 得分别处理:
        # 死亡画面先点"继续"回到开局菜单, 开局菜单再点"开始"真正进局。放在循环
        # 顶部而不是只在轮次结束后检查, 这样脚本刚启动、游戏还没开始的情况也能
        # 处理到, 不会一上来就对着菜单傻寻路.
        if on_death_screen():
            print("💀 检测到死亡结算画面, 点击继续...")
            overlay.update(state="重新开始", message="死亡, 点击继续...")
            click_continue_after_death()
            time.sleep(2)
        if on_start_screen():
            print("🔁 检测到开局菜单, 点击开始按钮进入游戏...")
            overlay.update(state="重新开始", message="点击开始按钮...")
            click_start_game()
            time.sleep(3)  # 等游戏加载

        print(f"📍 目标区域: {farming_area}\n")
        overlay.update(state="启动", target=location, message=f"第{round_count}轮: 开始自动寻路到刷怪区域")

        # 寻路到目标区域
        if lazy_theta_pathing(location, [farming_area]):
            print("✅ 到达刷怪区域！")
            auto_farming(farming_area, farming_duration)
        else:
            print("❌ 本轮未能到达目标区域")
            # 不传state: lazy_theta_pathing已设好具体状态(已死亡/菜单中/卡住/出错),
            # _merge_state只合并非None字段, 省略state就不会用泛泛的"出错"覆盖掉它.
            overlay.update(message="本轮未能到达目标区域")
            time.sleep(1)

        # 这条命从round_start_time到现在到底撑了多久 —— 寻路+刷怪的时间都算进去了.
        round_elapsed = time.time() - round_start_time
        completed_full_duration = round_elapsed >= farming_duration

        if completed_full_duration:
            consecutive_short_rounds = 0
        else:
            consecutive_short_rounds += 1
            print(f"⚠️ 这条命只撑了{round_elapsed:.0f}秒, 没到{farming_duration}秒 (连续{consecutive_short_rounds}次)")
            if consecutive_short_rounds >= CONSECUTIVE_SHORT_ROUND_LIMIT:
                print(f"🌐 连续{consecutive_short_rounds}轮没刷满, 换个服务器...")
                overlay.update(state="换服务器", message=f"连续{consecutive_short_rounds}轮没刷满, 切换中")
                # 换服务器是附加功能(查接口/连CDP都可能失败, 比如Chrome没用对参数
                # 启动、证书验证失败、网络问题), 失败了不能让整个"不主动停"的机器人
                # 直接崩掉 —— 打印清楚原因, 不清零计数, 下一轮接着重试(跟这个项目
                # 一贯的"卡住不放弃, 无限重试"风格一致, 不是遇到问题就躺平).
                try:
                    switch_server()
                    consecutive_short_rounds = 0
                    time.sleep(2)  # 给切换后的画面留点稳定时间, 下一轮循环顶部的死亡/开局检测再接手
                except Exception as e:
                    print(f"⚠️ 换服务器失败, 先用当前服务器继续刷 (下轮再重试): {e}")
                    overlay.update(message=f"换服务器失败(下轮重试): {e}")