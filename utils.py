import re
import json
import sys
import pyautogui
import time
import math
import os
import cv2
import traceback
import heapq
import random
import numpy as np

import cdp_bridge
import server_lookup

if sys.platform == "win32":
    # 没有这行, Windows下显示缩放不是100%时, PyAutoGUI截图/点击用的坐标系会被
    # 系统偷偷做DPI虚拟化映射, 跟真实物理像素对不上 —— 全屏时最明显(实测:
    # 关掉浏览器全屏反而能点对, 但一离开全屏, 屏幕中心就不等于游戏画布中心了,
    # 靠鼠标相对屏幕中心转向的移动逻辑跟着报废, 全屏/点得准不能兼得, 必须从根上
    # 让这个进程本身声明自己是DPI-aware的). florr-auto-afk(同作者同类项目)的
    # main.py末尾就有这行, 说明这个坑已经被踩过验证过.
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

try:
    SCREEN_WIDTH, SCREEN_HEIGHT = pyautogui.size()
except Exception:
    # 没有真实显示器(比如headless CI)时pyautogui.size()可能取不到 —— 退化成
    # 原来硬编码的1920x1080, 跟这个改动之前的行为完全一致, 不让import本身崩掉.
    SCREEN_WIDTH, SCREEN_HEIGHT = 1920, 1080

_REF_WIDTH, _REF_HEIGHT = 1920, 1080  # 下面这些函数里所有写死的坐标常量都是照这个分辨率量出来的


def scale_x(value):
    """把一个照1920宽量出来的x坐标/宽度, 换算成实际屏幕宽度下的等效值."""
    return round(value * SCREEN_WIDTH / _REF_WIDTH)


def scale_y(value):
    """同scale_x, 换算y坐标/高度(照1080高量出来的)."""
    return round(value * SCREEN_HEIGHT / _REF_HEIGHT)


def scale_point(x, y):
    return (scale_x(x), scale_y(y))


def scale_region(x, y, w, h):
    """把pyautogui.screenshot(region=[x,y,w,h])用的截图区域(照1920x1080量出来的)
    换算成实际分辨率下的区域. 宽高各自按对应轴单独换算(不是简单乘同一个比例),
    这样非16:9分辨率(宽高比跟1920x1080不一样)也不用另外分支处理 —— 跟
    scale_x/scale_y是同一套"每根轴独立缩放"逻辑.

    位置和宽高分开算两次scale_x/scale_y再相减(而不是直接scale_x(w)),是为了让
    四舍五入的误差不累积: right-left的差值比"起点+独立换算的宽度"更贴近实际
    截到的物理像素范围.
    """
    left = scale_x(x)
    top = scale_y(y)
    right = scale_x(x + w)
    bottom = scale_y(y + h)
    return [left, top, right - left, bottom - top]


def clamp_to_screen(x, y, margin=2):
    """把一个鼠标目标位置钳制在屏幕范围内(留一点margin) —— 防止小分辨率屏幕上
    算出来的转向偏移量把pyautogui.moveTo()的目标坐标推到屏幕外报错."""
    return (
        min(max(x, margin), SCREEN_WIDTH - margin),
        min(max(y, margin), SCREEN_HEIGHT - margin),
    )


def __getattr__(name):
    """PEP 562: module-level __getattr__ to dynamically compute MOUSE_SCALE.
    这样每次访问utils.MOUSE_SCALE都会用当前的SCREEN_WIDTH/SCREEN_HEIGHT重新计算,
    允许测试里monkeypatch屏幕分辨率后正确反映到MOUSE_SCALE的值."""
    if name == "MOUSE_SCALE":
        return min(SCREEN_WIDTH / _REF_WIDTH, SCREEN_HEIGHT / _REF_HEIGHT)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

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
    pos = get_player_position()
    if pos == (250, 50):
        print(f"🗺️ toggle_map: 位置命中(250,50), 按M切换地图 (调用前位置={pos})")
        pyautogui.press('m')
        time.sleep(1)


def calc_anti_stuck(borders, weight=1.0):
    screen_center = np.array([SCREEN_WIDTH / 2, SCREEN_HEIGHT / 2])
    total_force = np.array([0.0, 0.0])

    for point in borders:
        point_vector = np.array(point)
        distance = np.linalg.norm(screen_center - point_vector)
        if distance == 0:
            continue
        force_vector = (screen_center - point_vector) / distance
        total_force += force_vector

    final_position = screen_center + total_force * weight
    final_position[0] = np.clip(final_position[0], 0, SCREEN_WIDTH)
    final_position[1] = np.clip(final_position[1], 0, SCREEN_HEIGHT)
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
    pyautogui_img = pyautogui.screenshot(region=[0, 0, SCREEN_WIDTH, SCREEN_HEIGHT])
    opencv_img = cv2.cvtColor(np.array(pyautogui_img), cv2.COLOR_RGB2BGR)
    borders = check_map_border(opencv_img)
    suggested_position = calc_anti_stuck(borders)
    print(f"🧭 脱困: 朝 {suggested_position} 移动...")
    screen_center = np.array([SCREEN_WIDTH / 2, SCREEN_HEIGHT / 2])
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
    delta = round(delta * MOUSE_SCALE)
    cx, cy = SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2
    if direction == "w":
        pyautogui.moveTo(*clamp_to_screen(cx, cy - delta))
    if direction == "s":
        pyautogui.moveTo(*clamp_to_screen(cx, cy + delta))
    if direction == "a":
        pyautogui.moveTo(*clamp_to_screen(cx - delta, cy))
    if direction == "d":
        pyautogui.moveTo(*clamp_to_screen(cx + delta, cy))
    if direction == "wa":
        pyautogui.moveTo(*clamp_to_screen(cx - delta, cy - delta))
    if direction == "wd":
        pyautogui.moveTo(*clamp_to_screen(cx + delta, cy - delta))
    if direction == "sa":
        pyautogui.moveTo(*clamp_to_screen(cx - delta, cy + delta))
    if direction == "sd":
        pyautogui.moveTo(*clamp_to_screen(cx + delta, cy + delta))


def keyup(direction):
    pyautogui.moveTo(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)


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
    pyautogui.moveTo(*scale_point(307, 32))
    pyautogui.doubleClick()
    pyautogui.doubleClick()
    pyautogui.doubleClick()


# 换服务器: 试过三种更麻烦的路子, 都被这个方案替代掉了——
#   1) 点游戏内"设置"面板下拉框: 坐标拿debug_screen_pos.py标记截图逐个确认过、
#      确认落在正确的行上, 但pyautogui的点击就是不被那个画布控件识别(单击/双击
#      都试过, debug_single_click.py隔离测试过, 排除了坐标和点击次数两个变量,
#      依然选不中), 真人手动点完全正常 —— 是pyautogui合成的点击事件对这个
#      canvas控件不管用, 不是坐标或点击次数的问题.
#   2) cp6.forceServerID("码") + 手动抄的静态码表: 靠CDP执行确认过真的能触发
#      重连, 但码是从第三方florr.io服务器码追踪站(ashish.top、craft.darkmax.top)
#      人工抄的, 会不定期失效 —— 得人时不时去核对更新, 不是配一次就一直管用.
#   3) cp6.disconnect()不指定服务器: 不用管码过期, 但交给游戏自己重新分配,
#      没法保证真的换到了不同服务器(可能原地重连回同一个).
# 现在这版: forceServerID(码)还是要用(能确保真的指向一个不同服务器), 但码不再
# 手动抄 —— 直接调server_lookup.py查的官方(M28 Games)实时接口, 每次调用拿到的
# 都是当下最新数据, 天然不存在"码过期"问题.
SERVER_COOLDOWN_SECONDS = 30 * 60  # 同一个服务器码30分钟内不再选回去

_server_last_used = {}  # server_id -> 上次切到它的time.time()时间戳, 进程内存, 不落盘


def _pick_server_id(ids, last_used, now, cooldown_seconds=SERVER_COOLDOWN_SECONDS):
    """从ids里随机挑一个不在冷却期内的服务器码.

    last_used是{server_id: 上次切到它的时间戳}. cooldown_seconds内用过的码
    被排除掉, 剩下的候选里随机选一个(不是固定选第一个, 3台服务器轮着用不然
    永远只在同两个之间跳). 排除完一个候选都不剩时(比如可选服务器总共就3个,
    换得比冷却期还频繁, 全在冷却里), 退化成挑最久没用过的那个 —— 好歹是当下
    真实存在的服务器, 不能因为"非要挑没冷却过的"卡死不换.
    """
    candidates = [i for i in ids if now - last_used.get(i, 0) >= cooldown_seconds]
    if candidates:
        return random.choice(candidates)
    return min(ids, key=lambda i: last_used.get(i, 0))


def switch_server(biome="desert"):
    """查一次官方实时服务器列表(server_lookup.fetch_server_ids), 用
    _pick_server_id()挑一个30分钟内没选过的, 通过CDP在florr.io标签页里跑
    cp6.forceServerID(...)切过去.

    需要Chrome用cdp_bridge.py模块文档里那三个参数启动. 网络请求失败/找不到
    florr.io标签页时原样抛出异常(urllib的异常/cdp_bridge.eval_js()的
    RuntimeError), 不在这里吞掉 —— 换服务器失败main.py那边应该能看到报错,
    不是静默啥也没发生.
    """
    ids = server_lookup.fetch_server_ids(biome)
    now = time.time()
    server_id = _pick_server_id(ids, _server_last_used, now)
    _server_last_used[server_id] = now

    print(f"🌐 切换服务器: {server_id}")
    cdp_bridge.eval_js(f'cp6.forceServerID("{server_id}")')
    return server_id


_BUTTON_GREEN_RGB = (27, 203, 37)  # florr.io确认类按钮统一用这个绿色底(开始/继续都是)
_START_BUTTON_POS = scale_point(1059, 527)     # 开局菜单"开始"按钮(还没进过局/或已经回到开局菜单), 1920x1080下量出来的
_CONTINUE_BUTTON_POS = scale_point(959, 634)   # 死亡结算画面"继续"按钮(注意: 跟开局菜单是两个完全不同的界面!), 同样是1920x1080下量出来的


def _green_button_ratio(pos, half_w=15, half_h=10):
    """采样按钮周围一小块区域, 算绿色像素占比 —— 不能只采一个点.

    按钮上的文字/图标带黑色描边, 单点坐标很容易正好落在描边或图标上而不是纯色
    背景上, 只有采样一整块区域看绿色占比才稳. 实测按钮区域里文字+图标占比不小,
    纯绿色背景经常只剩10%~20%, 别把阈值定太高.

    half_w/half_h默认值是1920x1080下量出来的采样半径, 换算到实际分辨率(至少
    留1px, 否则超小分辨率下可能四舍五入成0导致采样区域是空的).
    """
    x, y = pos
    half_w = max(1, scale_x(half_w))
    half_h = max(1, scale_y(half_h))
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


# "继续"按钮比"开始"按钮小, "继续"两个字相对占比更大 —— 用_green_button_ratio()
# 默认的15x10采样半径时, 那两个字几乎能把整个采样框填满, 纯绿色背景被挤没了.
# 实机截图验证过(debug_death_click.py存的debug_before_click.png, 真死亡画面):
#   15x10框: ratio=0.0033 (被文字占满, 远低于旧阈值0.02, 检测直接判False)
#   30x16框: ratio=0.3760 (放大采样范围, 纯绿色背景占比回归正常)
# 阈值和采样半径都得跟着放大的框重新定, 不是单独调阈值就能补救.
_DEATH_SCREEN_GREEN_THRESHOLD = 0.15
_DEATH_SCREEN_SAMPLE_HALF_W = 30
_DEATH_SCREEN_SAMPLE_HALF_H = 16


def on_death_screen():
    """检测屏幕上是不是正显示着死亡结算画面("你死于XX" + 绿色"继续"按钮).

    这是跟开局菜单完全不同的一个界面(死于XX的文字、花瓣战利品面板、"继续"/"关闭"
    两个按钮, 位置和文案都不一样), check_stage()原来那套in_game_dead判定
    (探测像素(316,32)是不是纯白255,255,255)在实机上从没真正触发过 —— 同样是
    没验证过的硬编码签名。这里直接测"继续"按钮那块是不是绿的.
    """
    ratio = _green_button_ratio(
        _CONTINUE_BUTTON_POS,
        half_w=_DEATH_SCREEN_SAMPLE_HALF_W,
        half_h=_DEATH_SCREEN_SAMPLE_HALF_H,
    )
    return ratio > _DEATH_SCREEN_GREEN_THRESHOLD


def click_continue_after_death():
    """确认死亡结算画面, 回到开局菜单(还需要再调click_start_game才能真正进下一局).

    之前两版都试过回车(先纯回车, 再补"点一下抢焦点+回车"), 实机截图拿到手才
    发现方向从一开始就错了: florr.io里回车是开聊天框的快捷键(截图左下角写着
    "按下[ENTER]或点击这里聊天"), 根本不会触发这个"继续"按钮, 跟焦点没关系.
    (959,634)这个坐标本身是准的, 对着真实1920x1080截图量过, 正落在按钮范围内.

    改回纯点击, 但沿用这个项目自己在abandon_game()里已经踩过坑验证过的套路:
    单次click()第一下常常只把窗口/标签页激活, 点击事件没能真正传进游戏画布,
    要连点几次才可靠命中.

    实测坐标+点击机制本身都没问题(debug_death_click.py隔离测过, 点击成功) ——
    main.py主循环里失败, 是因为on_death_screen()一测到颜色达标就立刻点, 而
    死亡画面很可能还在渐入动画里, 颜色刚过检测阈值那一瞬间按钮还没真正可交互.
    隔离测试之所以每次都成功, 是因为脚本给了5秒倒计时, 画面早就稳定了才点 ——
    人手速也从没快到能踩中这个窗口, 所以感觉不到"冷却", 但紧循环里的脚本能.
    加一点等待, 让画面先稳定下来.
    """
    time.sleep(0.5)
    pyautogui.moveTo(_CONTINUE_BUTTON_POS)
    time.sleep(0.2)
    pyautogui.click()
    time.sleep(0.1)
    pyautogui.click()


def click_start_game():
    """确认开局菜单, 真正进入游戏. 同click_continue_after_death()的理由:
    纯点击, 连点两次保证命中(参见该函数注释里的完整说明)."""
    pyautogui.moveTo(_START_BUTTON_POS)
    time.sleep(0.2)
    pyautogui.click()
    time.sleep(0.1)
    pyautogui.click()


def check_stage():
    full_screen = [0, 0, SCREEN_WIDTH, SCREEN_HEIGHT]
    color = pyautogui.screenshot(region=full_screen).getpixel(scale_point(316, 32))
    if color == (187, 85, 85):
        return "in_game"
    elif color == (255, 255, 255):
        return "in_game_dead"
    else:
        color = pyautogui.screenshot(region=full_screen).getpixel(scale_point(156, 35))
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


def _ensure_grayscale_2d(img):
    """cv2.imread(path, cv2.IMREAD_GRAYSCALE)理论上强制转成纯(H,W), 但实测在
    Windows上撞到过它吐出带一条多余尾随通道维的(H,W,1)(Mac上没复现, 是OpenCV/
    平台组合的已知行为差异). 这个二义性会让所有假设'map是2D数组'的下游代码
    (calibrate_player的rows,cols=map.shape、lazy_theta_star的map[y][x]、
    random_walkable_point的binary_map[y,x]等)全部遭殃 —— 与其挨个打补丁, 不如
    在唯一的加载入口把形状锁死."""
    if img is not None and img.ndim == 3:
        img = img[:, :, 0]
    return img


def load_binary_map():
    return _ensure_grayscale_2d(cv2.imread(f'./maps/{MAP}.png', cv2.IMREAD_GRAYSCALE))


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