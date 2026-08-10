import re
import json
import pyautogui
import time
import math
import os
import cv2
import traceback
import heapq
import random
import numpy as np

MAP = ""


def apply_map(name):
    global MAP
    assert name in [path.removesuffix(".png") for path in os.listdir("./maps")]
    MAP = name


def if_in_area(areas: list[tuple[tuple[int, int], tuple[int, int]]], point: tuple[int, int]):
    """检查point在不在areas任意一个矩形里.

    不假设area[0]一定是"左上角"、area[1]一定是"右下角" —— 之前就假设了这个顺序,
    main.py里farming_area=[(20,15),(9,76)]第一个角x比第二个角x还大, 导致
    `20 <= x <= 9`这种区间永远判不出True, 玩家哪怕站在区域正中间都判定"不在区域
    内"。这里对每个轴分别取min/max再判断, 不管两个角怎么给都能判对。
    """
    for area in areas:
        (x1, y1), (x2, y2) = area
        min_x, max_x = min(x1, x2), max(x1, x2)
        min_y, max_y = min(y1, y2), max(y1, y2)
        if min_x <= point[0] <= max_x and min_y <= point[1] <= max_y:
            return True
    return False


def distance(pos1, pos2):
    return math.sqrt((pos1[0] - pos2[0])**2 + (pos1[1] - pos2[1])**2)


def check_map_border(opencv_img):
    if MAP == "ocean":
        target_color = "4c4950"
    elif MAP == "desert":
        target_color = "4f3422"
    target_color_bgr = tuple(int(target_color[i:i+2], 16) for i in (4, 2, 0))
    lower_bound = np.array([max(0, target_color_bgr[0] - 3), max(0,
                           target_color_bgr[1] - 3), max(0, target_color_bgr[2] - 3)])
    upper_bound = np.array([min(255, target_color_bgr[0] + 3), min(255,
                           target_color_bgr[1] + 3), min(255, target_color_bgr[2] + 3)])

    mask = cv2.inRange(opencv_img, lower_bound, upper_bound)

    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

    points = []
    for contour in contours:
        for i in range(0, len(contour), 5):
            cv2.circle(opencv_img, tuple(contour[i][0]), 1, (0, 255, 0), -1)
            points.append(tuple(contour[i][0]))

    return points


def toggle_map():
    if get_player_position() == (250, 50):
        pyautogui.press('m')
        time.sleep(1)


def calc_anti_stuck(borders, weight=1.0):
    screen_center = np.array([960, 540])
    total_force = np.array([0.0, 0.0])

    for point in borders:
        point_vector = np.array(point)
        distance = np.linalg.norm(screen_center - point_vector)
        if distance == 0:
            continue
        force_vector = (screen_center - point_vector) / distance
        total_force += force_vector

    final_position = screen_center + total_force * weight
    final_position[0] = np.clip(final_position[0], 0, 1920)
    final_position[1] = np.clip(final_position[1], 0, 1080)
    toggle_map()
    return final_position[0], final_position[1]


def execute_anti_stuck(duration=1.5):
    """卡住脱困: 优先用画面里的墙壁色算排斥方向, 排斥力太弱/没有就退化成随机方向硬闯.

    check_map_border靠一个写死的墙壁RGB(容差±3)在全屏截图里找墙 —— 实测这颜色
    经常一个像素都匹配不上(游戏画面是带纹理阴影的贴图, 不是纯色小地图符号,
    单一颜色+窄容差很容易全军覆没), 一旦borders是空的, 排斥力算出来就是零向量.

    光判"完全等于0"不够: 实测过好几次borders不是空的, 但只匹配到零星几个孤立
    像素, 算出来的力delta只有1像素左右(比如(0.26,-0.97)) —— 这点力换算成
    keydown按键时长约等于0, 角色压根没挪窝, 下一轮截图又是同一批孤立像素,
    算出来还是同一个delta, 陷入"看起来在脱困、实际原地不动"的死循环(实机验证过:
    连续多轮"卡住→脱困→还是卡住"打印的坐标一模一样)。改成力小于阈值(以屏幕
    像素为单位, 5px)就当没找到有效方向, 退化成随机方向硬闯.
    """
    pyautogui_img = pyautogui.screenshot(region=[0, 0, 1920, 1080])
    opencv_img = cv2.cvtColor(np.array(pyautogui_img), cv2.COLOR_RGB2BGR)
    borders = check_map_border(opencv_img)
    suggested_position = calc_anti_stuck(borders)
    print(f"🧭 脱困: 朝 {suggested_position} 移动...")
    screen_center = np.array([960, 540])
    delta = suggested_position - screen_center
    max_delta = np.max(np.abs(delta))
    if max_delta < 5:
        direction = random.choice(["w", "a", "s", "d", "wa", "wd", "sa", "sd"])
        print(f"⚠️ 附近没找到足够强的墙壁排斥力(力度{max_delta:.1f}), 退化成随机方向脱困: {direction}")
        keydown(direction)
        time.sleep(duration)
        keyup(direction)
        return
    duration_x = duration * abs(delta[0]) / max_delta
    duration_y = duration * abs(delta[1]) / max_delta
    if delta[0] > 0:
        keydown("d")
        time.sleep(duration_x)
        keyup("d")
    else:
        keydown("a")
        time.sleep(duration_x)
        keyup("a")

    if delta[1] > 0:
        keydown("s")
        time.sleep(duration_y)
        keyup("s")
    else:
        keydown("w")
        time.sleep(duration_y)
        keyup("w")


def keydown(direction, delta=500):
    if direction == "w":
        pyautogui.moveTo(1920//2, 1080//2-delta)
    if direction == "s":
        pyautogui.moveTo(1920//2, 1080//2+delta)
    if direction == "a":
        pyautogui.moveTo(1920//2-delta, 1080//2)
    if direction == "d":
        pyautogui.moveTo(1920//2+delta, 1080//2)
    if direction == "wa":
        pyautogui.moveTo(1920//2-delta, 1080//2-delta)
    if direction == "wd":
        pyautogui.moveTo(1920//2+delta, 1080//2-delta)
    if direction == "sa":
        pyautogui.moveTo(1920//2-delta, 1080//2+delta)
    if direction == "sd":
        pyautogui.moveTo(1920//2+delta, 1080//2+delta)


def keyup(direction):
    pyautogui.moveTo(1920//2, 1080//2)


def get_player_position(precise=False):
    image = get_map()
    binary_map = load_binary_map()
    # 玩家小地图标记的真实颜色 (f8de60, 实测验证稳定)。
    # 旧列表里那10种颜色其实是迷宫墙壁/地板色 (~95%像素覆盖),
    # 谁先匹配到就返回, 导致"位置"实际是墙壁轮廓噪声, 每帧乱跳。
    for color in ["f8de60"]:
        position = get_player_location_on_map(
            image, color, binary_map, precise)
        if position != None:
            return position


def abandon_game():
    pyautogui.moveTo(307, 32)
    pyautogui.doubleClick()
    pyautogui.doubleClick()
    pyautogui.doubleClick()


_BUTTON_GREEN_RGB = (27, 203, 37)  # florr.io确认类按钮统一用这个绿色底(开始/继续都是)
_START_BUTTON_POS = (1059, 527)     # 开局菜单"开始"按钮(还没进过局/或已经回到开局菜单)
_CONTINUE_BUTTON_POS = (959, 634)   # 死亡结算画面"继续"按钮(注意: 跟开局菜单是两个完全不同的界面!)


def _green_button_ratio(pos, half_w=15, half_h=10):
    """采样按钮周围一小块区域, 算绿色像素占比 —— 不能只采一个点.

    按钮上的文字/图标带黑色描边, 单点坐标很容易正好落在描边或图标上而不是纯色
    背景上, 只有采样一整块区域看绿色占比才稳. 实测按钮区域里文字+图标占比不小,
    纯绿色背景经常只剩10%~20%, 别把阈值定太高.
    """
    x, y = pos
    region = pyautogui.screenshot(region=[x - half_w, y - half_h, half_w * 2, half_h * 2])
    arr = np.array(region)[:, :, :3]
    match = np.all(np.abs(arr.astype(int) - np.array(_BUTTON_GREEN_RGB)) <= 25, axis=-1)
    return match.sum() / match.size


def on_start_screen():
    """检测屏幕上是不是正显示着开局菜单的绿色"开始"按钮(还没进局, 或已经从
    死亡画面点"继续"回到了这里).

    check_stage()那套单像素精确匹配是给别的画面校准的, 跟开局菜单对不上号(实测
    这个画面check_stage()只会返回"unknown")。与其猜另一个精确像素签名, 不如直接
    去测"开始"按钮那块是不是真是绿的 —— 检测的就是马上要点的那个东西.
    """
    return _green_button_ratio(_START_BUTTON_POS) > 0.1


_DEATH_SCREEN_GREEN_THRESHOLD = 0.02
# "继续"按钮比"开始"按钮文字占比更高(按钮更小、字体相对更大), 同样15x10半径的
# 采样框里纯绿色实测只剩3.7%左右, 阈值比on_start_screen()的0.1低不少, 不是笔误.


def on_death_screen():
    """检测屏幕上是不是正显示着死亡结算画面("你死于XX" + 绿色"继续"按钮).

    这是跟开局菜单完全不同的一个界面(死于XX的文字、花瓣战利品面板、"继续"/"关闭"
    两个按钮, 位置和文案都不一样), check_stage()原来那套in_game_dead判定
    (探测像素(316,32)是不是纯白255,255,255)在实机上从没真正触发过 —— 同样是
    没验证过的硬编码签名。这里直接测"继续"按钮那块是不是绿的.
    """
    return _green_button_ratio(_CONTINUE_BUTTON_POS) > _DEATH_SCREEN_GREEN_THRESHOLD


def click_continue_after_death():
    """点死亡结算画面的绿色"继续"按钮. 点完通常会回到开局菜单, 还需要再点一次
    "开始"(click_start_game)才能真正进下一局."""
    pyautogui.moveTo(_CONTINUE_BUTTON_POS)
    time.sleep(0.2)
    pyautogui.click()


def click_start_game():
    """点开始界面的绿色"开始"按钮, 坐标是截图里对绿色按钮做像素质心算出来的
    (1920x1080全屏布局下, "开始"按钮实测中心在(1059,527)). 换分辨率/换布局需要
    重新量 —— 之前凭鼠标悬停手动量过一次得到(967,902), 跟实际按钮对不上, 别再用.
    """
    pyautogui.moveTo(_START_BUTTON_POS)
    time.sleep(0.2)
    pyautogui.click()


def check_stage():
    color = pyautogui.screenshot(region=[0, 0, 1920, 1080]).getpixel((316, 32))
    if color == (187, 85, 85):
        return "in_game"
    elif color == (255, 255, 255):
        return "in_game_dead"
    else:
        color = pyautogui.screenshot(
            region=[0, 0, 1920, 1080]).getpixel((156, 35))
        if color == (155, 181, 107):
            return "in_menu"
        else:
            return "unknown"


def get_map():
    image = pyautogui.screenshot(region=[1600, 20, 1900-1600, 320-20])
    image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    return image


def preprocess_map(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lower_yellow = np.array([20, 100, 100])
    upper_yellow = np.array([30, 255, 255])
    yellow_mask = cv2.inRange(hsv, lower_yellow, upper_yellow)
    binary[yellow_mask > 0] = 255
    cv2.imwrite('./maps/anthell.png', binary)
    return binary


def load_binary_map():
    return cv2.imread(f'./maps/{MAP}.png', cv2.IMREAD_GRAYSCALE)


def get_player_location_on_map(opencv_img, target_color, map, precise=False):
    target_color_bgr = tuple(int(target_color[i:i+2], 16) for i in (4, 2, 0))
    lower_bound = np.array([max(0, target_color_bgr[0] - 20), max(0,
                           target_color_bgr[1] - 20), max(0, target_color_bgr[2] - 20)])
    upper_bound = np.array([min(255, target_color_bgr[0] + 20), min(255,
                           target_color_bgr[1] + 20), min(255, target_color_bgr[2] + 20)])

    mask = cv2.inRange(opencv_img, lower_bound, upper_bound)

    contours, _ = cv2.findContours(
        mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    for contour in contours:
        ((x, y), radius) = cv2.minEnclosingCircle(contour)
        if radius > 2:
            if precise:
                position = (x, y)
                return position
            else:
                position = (round(x), round(y))
                return calibrate_player(map, position)
    return None


def calibrate_player(map, player_position):
    rows, cols = map.shape
    min_distance = float('inf')
    nearest_walkable_position = player_position
    for y in range(rows):
        for x in range(cols):
            if map[y, x] == 255:
                distance = math.sqrt(
                    (x - player_position[0])**2 + (y - player_position[1])**2)
                if distance < min_distance:
                    min_distance = distance
                    nearest_walkable_position = (x, y)
    return nearest_walkable_position