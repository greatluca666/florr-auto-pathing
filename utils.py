import re
import json
import pyautogui
import time
import math
import os
import cv2
import traceback
import heapq
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
    pyautogui_img = pyautogui.screenshot(region=[0, 0, 1920, 1080])
    opencv_img = cv2.cvtColor(np.array(pyautogui_img), cv2.COLOR_RGB2BGR)
    borders = check_map_border(opencv_img)
    suggested_position = calc_anti_stuck(borders)
    print(f"🧭 脱困: 朝 {suggested_position} 移动...")
    screen_center = np.array([960, 540])
    delta = suggested_position - screen_center
    max_delta = np.max(np.abs(delta))
    if max_delta == 0:
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


def click_start_game():
    """点掉线/死亡后菜单界面的"开始游戏"按钮, 坐标是实机悬停鼠标实测出来的
    (1920x1080全屏布局下). 换分辨率/换布局需要重新量.
    """
    pyautogui.moveTo(967, 902)
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