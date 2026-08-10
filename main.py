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


def move_to_position(current_pos, target_pos, max_attempts=30):
    """移动到目标位置 - 简化版本"""
    if current_pos is None or target_pos is None:
        return "stuck"

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
    """寻路到目标区域"""
    retry_count = 0
    max_retries = 3
    
    while True:
        pos = get_player_position()
        
        if pos is None:
            retry_count += 1
            print(f"⚠️ 无法检测玩家位置，重试 {retry_count}/{max_retries}...")
            if retry_count >= max_retries:
                print("❌ 多次重试失败")
                return False
            time.sleep(1)
            continue
        
        retry_count = 0
        print(f"\n📍 寻路: {pos} -> {location}")
        time_now = time.time()
        
        binary_map = load_binary_map()
        if binary_map is None:
            print("❌ 地图加载失败")
            return False
        
        path = lazy_theta_star(binary_map, pos, location)
        print(f"⏱️  寻路耗时: {time.time() - time_now:.2f}秒")
        
        if path is None:
            print("❌ 路径规划失败")
            return False
        
        print(f"✅ 找到路径，共 {len(path)} 个点")
        stat = execute_path(path)
        
        # 检查是否到达目标区域
        current_pos = get_player_position()
        if current_pos and if_in_area(area, current_pos):
            print(f"✅ 已到达目标区域！位置: {current_pos}\n")
            return True
        
        if current_pos == location:
            print(f"✅ 已到达目标位置！\n")
            return True
        
        if stat == "stuck":
            print("🔄 检测到卡住")
            return False
        
        stage = check_stage()
        if stage == "in_game_dead":
            print("💀 玩家已死亡")
            return False
        elif stage == "in_menu":
            print("📋 玩家在菜单中")
            return False


def auto_farming(farming_area, duration=300, move_interval=2.0):
    """自动刷怪逻辑（依赖一直攻击按钮）"""
    x1, y1 = farming_area[0]
    x2, y2 = farming_area[1]
    
    min_x, max_x = min(x1, x2), max(x1, x2)
    min_y, max_y = min(y1, y2), max(y1, y2)
    farming_area = [(min_x, min_y), (max_x, max_y)]
    
    print(f"\n🎮 开始在区域 {farming_area} 进行自动刷怪...")
    print(f"⏱️  刷怪时长: {duration}秒")
    print(f"⏰ 每次停留: {move_interval}秒（一直攻击模式）\n")
    
    start_time = time.time()
    move_count = 0
    
    while time.time() - start_time < duration:
        current_pos = get_player_position()
        
        if current_pos is None:
            print("⚠️ 无法检测玩家位置")
            time.sleep(1)
            continue
        
        # 检查是否还在刷怪区域
        if not if_in_area([farming_area], current_pos):
            print(f"⚠️ 离开刷怪区域 (当前: {current_pos})，重新寻路回去")
            target_x = (farming_area[0][0] + farming_area[1][0]) // 2
            target_y = (farming_area[0][1] + farming_area[1][1]) // 2
            if not lazy_theta_pathing((target_x, target_y), [farming_area]):
                print("❌ 无法回到刷怪区域")
                break
            continue
        
        # 在区域内随机选择一个目标点
        random_x = random.randint(farming_area[0][0], farming_area[1][0])
        random_y = random.randint(farming_area[0][1], farming_area[1][1])
        
        # 移动到目标点
        print(f"🚶 移动到 ({random_x}, {random_y})")
        move_result = move_to_position(current_pos, (random_x, random_y))
        
        if move_result == "stuck":
            print("⚠️ 移动受阻")
        elif move_result in ["in_game_dead", "in_menu"]:
            print(f"⚠️ 游戏状态变化: {move_result}")
            break
        
        # 在位置停留，依赖一直攻击按钮自动攻击
        print(f"⚔️  停留 {move_interval}秒...")
        time.sleep(move_interval)
        
        move_count += 1
        
        # 检查游戏状态
        stage = check_stage()
        if stage == "in_game_dead":
            print("💀 玩家已死亡")
            break
        elif stage == "in_menu":
            print("📋 玩家在菜单中")
            break
    
    elapsed = time.time() - start_time
    print(f"\n" + "="*50)
    print(f"✅ 刷怪完成！")
    print(f"   实际耗时: {elapsed:.1f}秒")
    print(f"   移动次数: {move_count}")
    print(f"="*50)


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
    
    # 寻路到目标区域
    if lazy_theta_pathing(location, [farming_area]):
        print("✅ 到达刷怪区域！")
        # 开始刷怪
        auto_farming(farming_area, farming_duration, move_interval)
    else:
        print("❌ 无法到达目标区域")
    
    print("\n🏁 脚本结束")