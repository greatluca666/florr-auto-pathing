from utils import *
from overlay import create_overlay
import time
import random

overlay = create_overlay()

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
        extend = max(min(dist * 45, 500), 50)
        if dist > 0:
            extend_x = extend * dx / dist
            extend_y = extend * dy / dist
        else:
            extend_x = extend_y = 0

        mouse_pos = (1920 // 2 + extend_x, 1080 // 2 + extend_y)
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
        pos = get_player_position()

        if pos is None:
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


def auto_farming(farming_area, duration=300, move_interval=2.0):
    """自动刷怪逻辑（依赖一直攻击按钮）"""
    x1, y1 = farming_area[0]
    x2, y2 = farming_area[1]

    min_x, max_x = min(x1, x2), max(x1, x2)
    min_y, max_y = min(y1, y2), max(y1, y2)
    farming_area = [(min_x, min_y), (max_x, max_y)]
    binary_map = load_binary_map()

    print(f"\n🎮 开始在区域 {farming_area} 进行自动刷怪...")
    print(f"⏱️  刷怪时长: {duration}秒")
    print(f"⏰ 每次停留: {move_interval}秒（一直攻击模式）\n")
    overlay.update(state="刷怪中", message=f"区域 {farming_area}")

    start_time = time.time()
    move_count = 0
    exit_reason = "timeout"

    while time.time() - start_time < duration:
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

        # 在区域内随机选择一个可走的目标点(不是瞎猜矩形里的坐标, 避开墙)
        random_x, random_y = random_walkable_point(farming_area, binary_map)

        # 移动到目标点
        print(f"🚶 移动到 ({random_x}, {random_y})")
        move_result = move_to_position(current_pos, (random_x, random_y))

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

        # 在位置停留，依赖一直攻击按钮自动攻击
        print(f"⚔️  停留 {move_interval}秒...")
        overlay.update(state="刷怪中", pos=(random_x, random_y), message=f"停留 {move_interval}秒 (第{move_count}次)")
        time.sleep(move_interval)

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


if __name__ == "__main__":
    apply_map("desert")

    # ===== 配置部分 =====
    location = (14, 45)
    farming_area = [(20, 15), (9, 76)]
    farming_duration = 300  # 5 分钟
    move_interval = 2.0     # 每次停留时间
    # ====================

    print("🎮 开始自动寻路到刷怪区域...")
    print(f"📍 目标区域: {farming_area}\n")
    overlay.update(state="启动", target=location, message="开始自动寻路到刷怪区域")

    # 寻路到目标区域
    if lazy_theta_pathing(location, [farming_area]):
        print("✅ 到达刷怪区域！")
        # 开始刷怪
        auto_farming(farming_area, farming_duration, move_interval)
    else:
        print("❌ 无法到达目标区域")
        # 不传state: lazy_theta_pathing已设好具体状态(已死亡/菜单中/卡住/出错),
        # _merge_state只合并非None字段, 省略state就不会用泛泛的"出错"覆盖掉它.
        overlay.update(message="无法到达目标区域")

    print("\n🏁 脚本结束")